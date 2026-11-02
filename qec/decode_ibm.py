"""Compare complete decoders on shared IBM measurement records, offline."""

from pathlib import Path
import argparse
import json
import numpy as np
import pymatching
from .spatial import Geometry, FFTReference, mwpm, summary_flags
from .hdrg import SurfaceHDRG, HDRGDecoder
from .hdrg_fast import FastSurfaceHDRG
from .memory import Geometry3D, space_time_syndrome
from . import ibm_memory
from .io import sha, write, protocol


def idle_errors(config, raw):
    geometry = Geometry(config["d"])
    basis = config["basis"]
    if basis not in ("Z", "X", "BELL"):
        raise ValueError("Unknown measurement basis.")
    names = ("mt", "mc") if basis == "BELL" else ("m",)
    values = {name: np.asarray(raw[name]) for name in names}
    if any(
        value.ndim != 2
        or value.shape[1] != geometry.nd
        or len(value) == 0
        or not np.isin(value, (0, 1)).all()
        for value in values.values()
    ):
        raise ValueError(
            "Expected nonempty binary measurement arrays in data-qubit order."
        )
    raw = {name: value.astype(np.uint8) for name, value in values.items()}
    if basis == "BELL":
        errors = np.stack([raw["mt"], raw["mc"]], axis=1)
    else:
        if len(config["prep_bits"]) != geometry.nd or any(
            bit not in "01" for bit in config["prep_bits"]
        ):
            raise ValueError(
                "Preparation must specify one binary value per data qubit."
            )
        preparation = np.array([int(bit) for bit in config["prep_bits"]], np.uint8)
        measured = raw["m"] ^ preparation[None]
        errors = np.zeros((len(measured), 2, geometry.nd), np.uint8)
        errors[:, 0 if basis == "Z" else 1] = measured
    if errors.ndim != 3 or errors.shape[1:] != (2, geometry.nd) or np.any(errors > 1):
        raise ValueError("Measurement records do not match the specified patch.")
    return errors


def hdrg_for(d):
    """The final-readout diagnostic includes even unrotated distances as well."""
    if d % 2:
        return FastSurfaceHDRG(d)
    geometry = Geometry(d)
    fast = FastSurfaceHDRG.__new__(FastSurfaceHDRG)
    fast.d = d
    fast.lookup = np.full((geometry.n, geometry.n), -1, np.int32)
    fast.lookup[geometry.rr, geometry.cc] = np.arange(geometry.nd)
    reference = SurfaceHDRG.__new__(SurfaceHDRG)
    reference.d, reference.n = d, geometry.n
    reference.sites = np.array(geometry.sites)
    reference.lookup = fast.lookup
    reference.checks = [
        np.array(geometry.zchecks, np.int32),
        np.array(geometry.xchecks, np.int32),
    ]
    reference.decoders = []
    for component, checks in enumerate(reference.checks):
        axis = 1 - component
        distances = np.column_stack(
            ((checks[:, axis] + 1) // 2, (geometry.n - checks[:, axis]) // 2)
        )
        reference.decoders.append(
            HDRGDecoder(
                checks / 2,
                distances,
                lambda a, b, k=component: reference.pair_path(k, a, b),
                lambda a, b, k=component: reference.boundary_path(k, a, b),
                geometry.nd,
            )
        )
    errors = np.random.default_rng(81000 + d).integers(
        0, 2, (64, 2, geometry.nd), dtype=np.uint8
    )
    syndromes = geometry.syndromes(errors)
    if not np.array_equal(
        fast.decode_batch(syndromes), reference.decode_batch(syndromes)
    ):
        raise AssertionError("Compiled and reference HDRG disagree for this patch.")
    reference.pair_path.cache_clear()
    reference.boundary_path.cache_clear()
    return fast


def decode_idle(config, raw, passes=1, batch=32, precision="integer"):
    errors = idle_errors(config, raw)
    geometry = Geometry(config["d"])
    matchers = [pymatching.Matching(matrix) for matrix in geometry.H]
    hdrg = hdrg_for(config["d"])
    engine = FFTReference(precision, geometry.n)
    heads = (
        ["logical_z_flip"]
        if config["basis"] == "Z"
        else (
            ["logical_x_flip"]
            if config["basis"] == "X"
            else ["logical_z_flip", "logical_x_flip", "both"]
        )
    )
    collected = {}

    def record(name, residual):
        values = geometry.outcomes(residual)
        values["both"] = values["logical_z_flip"] | values["logical_x_flip"]
        for head in heads:
            collected.setdefault(name + "__" + head, []).append(values[head])
            collected.setdefault(name + "__" + head + "_strict_failure", []).append(
                values[head] | values["nonclear"]
            )
        collected.setdefault(name + "__unresolved", []).append(values["nonclear"])

    for start in range(0, len(errors), batch):
        current = errors[start : start + batch].copy()
        record("mwpm", mwpm(current, geometry, matchers))
        record("hdrg", current ^ hdrg.decode_batch(geometry.syndromes(current)))
        for count in range(1, passes + 1):
            inputs, syndromes = geometry.inputs(current)
            active = np.any(syndromes[0], axis=1) | np.any(syndromes[1], axis=1)
            if active.any():
                current[active] ^= (
                    engine.forward(inputs[active], check=start == 0)[
                        :, :, geometry.rr, geometry.cc
                    ]
                    > 0
                ).astype(np.uint8)
            name = f"cnn{count}"
            record(name, current)
            record(name + "_mwpm", mwpm(current, geometry, matchers))
            record(
                name + "_hdrg", current ^ hdrg.decode_batch(geometry.syndromes(current))
            )
    return {key: np.concatenate(value) for key, value in collected.items()}


def decode_memory(manifest, raw, passes=1, batch=8, precision="integer", device="cpu"):
    from .networks import configure, FloatCNN, IntegerCNN

    configure(device, 2)
    config = manifest["configuration"]
    ibm_memory.configure_manifest({"medians": manifest["medians"], "configs": [config]})
    volumes, truth = ibm_memory.unwrap_extract(raw["raw"], config)
    geometry = Geometry3D(config["d"], config["rounds"], config["basis"])
    circuit, _ = ibm_memory.stim_circuit(config)
    dem = circuit.detector_error_model(decompose_errors=True)
    weighted = pymatching.Matching.from_detector_error_model(dem)
    engine = FloatCNN(device) if precision == "float" else IntegerCNN(device)
    collected = {}

    def record(name, value):
        collected.setdefault(name, []).append(value)

    for start in range(0, len(volumes), batch):
        original = volumes[start : start + batch]
        observed = truth[start : start + batch]
        current = original.copy()
        total = np.zeros_like(original)
        syndrome = geometry.measured_detectors(current[:, 8:])
        full = geometry.full_detectors(current[:, 8:])
        for method in ("hdrg", "mwpm_pcm"):
            record(
                method + "__failure",
                geometry.global_decode(syndrome, method) != observed,
            )
        record("mwpm_dem__failure", weighted.decode_batch(full)[:, 0] != observed)
        for count in range(1, passes + 1):
            active = np.any(current[:, 8:], axis=(1, 2, 3, 4))
            if active.any():
                total[active] ^= (
                    engine.predict(current[active]) & geometry.edge_mask[None]
                )
                current[active, 8:] = (
                    original[active, 8:]
                    ^ space_time_syndrome(total[active], geometry.pres)
                ) & geometry.available[None]
            parity = geometry.edge_logical(total)
            syndrome = geometry.measured_detectors(current[:, 8:])
            full = geometry.full_detectors(current[:, 8:])
            name = f"cnn{count}"
            record(name + "__failure", parity != observed)
            record(
                name + "__strict_failure",
                (parity != observed) | np.any(syndrome, axis=1),
            )
            for method in ("hdrg", "mwpm_pcm"):
                record(
                    name + "_" + method + "__failure",
                    (parity ^ geometry.global_decode(syndrome, method)) != observed,
                )
            record(
                name + "_mwpm_dem__failure",
                (parity ^ weighted.decode_batch(full)[:, 0]) != observed,
            )
    geometry.spatial_path.cache_clear()
    return {key: np.concatenate(value) for key, value in collected.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Folder containing experiment.json and measurements.npz.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--passes", type=int, default=1)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--precision", choices=["float", "integer"], default="integer")
    parser.add_argument(
        "--device",
        choices=["cpu", "cuda"],
        default="cpu",
        help="Device for the space-time CNN; spatial reference runs on CPU.",
    )
    args = parser.parse_args()
    if min(args.passes, args.batch) < 1:
        parser.error("Pass count and batch size must be positive.")
    manifest_path = args.input / "experiment.json"
    data_path = args.input / "measurements.npz"
    manifest = json.loads(manifest_path.read_text(encoding="utf8"))
    if manifest.get("preview"):
        parser.error("A circuit preview has no physical measurement data to decode.")
    settings = {
        "experiment": manifest["experiment"],
        "configuration": manifest["configuration"],
        "input_sha256": sha(data_path),
        "manifest_sha256": sha(manifest_path),
        "passes": args.passes,
        "precision": args.precision,
        "batch": args.batch,
        "device": args.device,
    }
    record = protocol(args.output, settings)
    with np.load(data_path, allow_pickle=False) as raw:
        if manifest["experiment"] == "idle":
            values = decode_idle(
                manifest["configuration"], raw, args.passes, args.batch, args.precision
            )
        elif manifest["experiment"] == "memory":
            values = decode_memory(
                manifest, raw, args.passes, args.batch, args.precision, args.device
            )
        else:
            parser.error("Unknown experiment type.")
    np.savez_compressed(args.output / "outcomes.npz", **values)
    write(
        args.output / "summary.json",
        {"protocol": record, "rates": summary_flags(values)},
    )
    print("Decoded the same saved records with all selected methods.")


if __name__ == "__main__":
    main()
