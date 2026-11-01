"""Convert saved notebook measurements to the per-condition offline format.

Only experiment settings and bit arrays are copied. Account identifiers, job
identifiers, timestamps and notebook output are deliberately left out.
"""

from pathlib import Path
import argparse
import csv
import json
import numpy as np
from .io import write, sha


def import_memory(source, output):
    manifest = json.loads(
        (source / "submit_manifest_3d.json").read_text(encoding="utf8")
    )
    path = source / "raw_bits_3d.npz"
    with np.load(path, allow_pickle=False) as archive:
        for index, original in enumerate(manifest["configs"]):
            config = {
                name: original[name]
                for name in ("d", "basis", "prep", "rounds", "prep_bits")
            }
            config["tag"] = f"memory_{index:03d}"
            folder = output / config["tag"]
            folder.mkdir()
            raw = archive[f"raw_{index}"]
            np.savez_compressed(folder / "measurements.npz", raw=raw)
            write(
                folder / "experiment.json",
                {
                    "experiment": "memory",
                    "configuration": config,
                    "backend": manifest["backend"],
                    "shots": len(raw),
                    "preview": False,
                    "state": "complete",
                    "medians": {
                        k: manifest["medians"][k] for k in ("t1", "t2", "ro", "e2q")
                    },
                    "source_sha256": sha(path),
                    "condition_index": index,
                },
            )


def import_idle(source, output):
    """Product/Bell records, including the distance-five to eight diagnostic."""
    with (source / "tab10_1_configs.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        configs = list(csv.DictReader(handle))
    path = source / "raw_bits_2d.npz"
    random_bits = np.random.default_rng(2026).integers(0, 2, 41).astype(np.uint8)
    with np.load(path, allow_pickle=False) as archive:
        for index, original in enumerate(configs):
            distance = int(original["d"])
            size = distance * distance + (distance - 1) * (distance - 1)
            if distance != 5 and original["prep"] == "random":
                raise ValueError(
                    "The saved random preparation in this export is defined only at distance five."
                )
            config = {name: original[name] for name in ("basis", "prep")}
            prep = (
                np.ones(size, np.uint8)
                if config["prep"] == "ones"
                else (
                    random_bits
                    if config["prep"] == "random"
                    else np.zeros(size, np.uint8)
                )
            )
            config.update(
                d=distance,
                tag=f"idle_{index:03d}",
                prep_bits="".join(map(str, prep)),
                dd=original["dd"].lower() in ("true", "1"),
                placement="geometric" if original["camp"] == "A" else "calibration",
                delay_us_actual=float(original["delay"]),
            )
            arrays = (
                {"mc": archive[f"bits_mc_{index}"], "mt": archive[f"bits_mt_{index}"]}
                if config["basis"] == "BELL"
                else {"m": archive[f"bits_{index}"]}
            )
            folder = output / config["tag"]
            folder.mkdir()
            np.savez_compressed(folder / "measurements.npz", **arrays)
            write(
                folder / "experiment.json",
                {
                    "experiment": "idle",
                    "configuration": config,
                    "backend": "ibm_miami",
                    "shots": len(next(iter(arrays.values()))),
                    "preview": False,
                    "state": "complete",
                    "source_sha256": sha(path),
                    "condition_index": index,
                },
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment", choices=["idle", "memory"])
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Use an empty output folder.")
    args.output.mkdir(parents=True, exist_ok=True)
    (import_idle if args.experiment == "idle" else import_memory)(
        args.input, args.output
    )
    print("Measurement arrays converted locally; no account or job identifiers copied.")


if __name__ == "__main__":
    main()
