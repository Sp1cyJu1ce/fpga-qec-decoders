"""Check the independent distance-33 result against three completed simulator runs."""

import argparse
import csv
import json
from pathlib import Path


RESULTS = Path(__file__).resolve().parents[1] / "results" / "independent_cleanup_d33_frames.csv"
FRAMES = 32


def csv_records(path):
    with path.open(newline="", encoding="ascii") as stream:
        return list(csv.DictReader(stream))


def hex_words(line):
    return tuple(int(word, 16) for word in line.split())


def read_xsim(directory):
    # The testbench writes (frame, clocks) only after both correction masks match.
    log = (directory / "xsim.stdout.log").read_text(encoding="utf8").splitlines()
    if f"WALKER_PASS d=33 frames={FRAMES}" not in log:
        raise ValueError(f"Incomplete distance-33 XSim run in {directory}")
    with (directory / "responses.csv").open(newline="") as stream:
        responses = [(int(i), int(t)) for i, t in csv.reader(stream)]
    if [i for i, _ in responses] != list(range(FRAMES)):
        raise ValueError(f"Missing or out-of-order XSim frame in {directory}")
    stimulus = (directory / "stimulus.txt").read_text(encoding="ascii").splitlines()
    return [(hex_words(line), ticks) for line, (_, ticks) in zip(stimulus, responses, strict=True)]


def check(connected, raw_run, residual_run, published):
    settings = json.loads((connected / "run.json").read_text(encoding="utf8"))
    expected_settings = dict(distance=33, seed=405731, samples_per_rate=FRAMES,
                             noise_rate=0.13, simulator="verilator", frames=FRAMES)
    if any(settings.get(k) != v for k, v in expected_settings.items()):
        raise ValueError("Use the independent 32-frame run at distance 33, seed 405731")
    log = (connected / "simulation.log").read_text(encoding="utf8").splitlines()
    if f"HYBRID_PASS {FRAMES}" not in log:
        raise ValueError("The connected testbench did not pass all 32 frames")

    raw = (connected / "raw_syndromes.hex").read_text(encoding="ascii").splitlines()
    count, *vectors = (connected / "expected.hex").read_text(encoding="ascii").splitlines()
    if int(count) != FRAMES:
        raise ValueError("Unexpected connected test vector count")
    connected_rows = csv_records(connected / "results.csv")
    inputs = zip(raw, vectors, connected_rows, read_xsim(raw_run),
                 read_xsim(residual_run), csv_records(published), strict=True)
    for i, (original, vector, row, raw_record, residual_record, reference) in enumerate(inputs):
        raw_inputs, raw_clocks = raw_record
        residual_inputs, residual_clocks = residual_record
        local_x, local_z, check_z, check_x, final_x, final_z = hex_words(vector)
        if raw_inputs[:2] != hex_words(original):
            raise ValueError(f"Frame {i}: raw XSim checks differ from the connected input")
        # Undo the final XOR to recover the masks expected of the standalone engine.
        if residual_inputs != (check_z, check_x, final_x ^ local_x, final_z ^ local_z):
            raise ValueError(f"Frame {i}: residual checks or correction masks differ")
        if row["case"] != f"pauli_{i}" or int(row["hdrg_cycles"]) != residual_clocks:
            raise ValueError(f"Frame {i}: connected frame order or HDRG timing differs")
        measured = dict(row, seed=str(settings["seed"]), raw_xsim_cycles=str(raw_clocks),
                        residual_xsim_cycles=str(residual_clocks))
        if measured != reference:
            fields = sorted(k for k in measured.keys() | reference.keys()
                            if measured.get(k) != reference.get(k))
            raise ValueError(f"Frame {i}: differs from {published.name} in {', '.join(fields)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("connected", type=Path, help="Verilator CNN/HDRG run directory")
    parser.add_argument("raw", type=Path, help="XSim HDRG run on the original checks")
    parser.add_argument("residual", type=Path, help="XSim HDRG run on the CNN residuals")
    parser.add_argument("--csv", type=Path, default=RESULTS, help="published per-frame results")
    args = parser.parse_args()
    try:
        check(args.connected, args.raw, args.residual, args.csv)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Check failed: {error}\n")
    print(f"PASS: all {FRAMES} frames match {args.csv.name}.")
