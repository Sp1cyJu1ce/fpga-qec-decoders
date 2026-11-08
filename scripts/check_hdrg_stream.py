"""Compare the FPGA cluster walker with both masks of the spatial HDRG decoder."""

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
from simulate_arty import ROOT, find_tool, run

sys.path.insert(0, str(ROOT))
from qec.hdrg import SurfaceHDRG
from qec.spatial import Geometry


def pack(bits):
    return int.from_bytes(np.packbits(bits, bitorder="little").tobytes(), "little")


def trials(d, samples):
    geometry = Geometry(d)
    n = d * (d - 1)
    if d == 3:
        return [("exhaustive", z, x) for z in range(1 << n) for x in range(1 << n)]
    full = (1 << n) - 1
    records = [("empty", 0, 0), ("full", full, full)]
    for bit in (0, n // 2 - 1, n // 2, n // 2 + d // 2, n - 1):
        records.append(("one event", 1 << bit, 1 << (n - 1 - bit)))
    for stride in (2, 3, d - 1, d + 1):
        pattern = sum(1 << i for i in range(0, n, stride))
        records.append(("regular", pattern, pattern))
    rng = np.random.default_rng(7411 + d)
    for p in (0.01, 0.05, 0.13, 0.17):
        for _ in range(samples):
            draws = rng.random(geometry.nd)
            x_error = draws < 2 * p / 3
            z_error = (draws >= p / 3) & (draws < p)
            syndrome = [
                pack((h @ e.astype(np.uint8)) % 2)
                for h, e in zip(geometry.H, (x_error, z_error))
            ]
            records.append((f"data error p={p}", *syndrome))
    for density in (0.1, 0.5, 0.9):
        for _ in range(samples):
            records.append(
                (
                    f"check density {density}",
                    pack(rng.random(n) < density),
                    pack(rng.random(n) < density),
                )
            )
    return records


def write_vectors(d, records, path):
    geometry = Geometry(d)
    decoders = SurfaceHDRG(d).decoders
    n = d * (d - 1)
    cache = {}
    with path.open("w", encoding="ascii") as stream:
        for _, z, x in records:
            expected = []
            for axis, syndrome in enumerate((z, x)):
                key = (axis, syndrome)
                if key not in cache:
                    bits = np.fromiter(
                        ((syndrome >> i) & 1 for i in range(n)), np.uint8
                    )
                    mask = decoders[axis].decode(bits)
                    if not np.array_equal((geometry.H[axis] @ mask) % 2, bits):
                        raise ValueError("Reference correction leaves an active check")
                    cache[key] = pack(mask)
                expected.append(cache[key])
            print(f"{z:x} {x:x} {expected[0]:x} {expected[1]:x}", file=stream)


def read_pairs(path, d):
    """Read the raw syndromes of a paired experiment, keeping duplicate frames."""
    records = []
    for line_number, line in enumerate(
        path.read_text(encoding="ascii").splitlines(), 1
    ):
        fields = line.partition("#")[0].split()
        if not fields:
            continue
        if len(fields) != 2:
            raise ValueError(f"{path}:{line_number}: expected Z checks then X checks")
        pair = [int(field, 16) for field in fields]
        if any(word < 0 or word.bit_length() > d * (d - 1) for word in pair):
            raise ValueError(f"{path}:{line_number}: syndrome outside patch")
        records.append(("paired input", *pair))
    if not records:
        raise ValueError(f"No syndrome pairs in {path}")
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distance", type=int, default=7)
    parser.add_argument(
        "--samples", type=int, default=12, help="samples per noise or density setting"
    )
    parser.add_argument("--simulator", choices=("xsim", "iverilog"), default="xsim")
    parser.add_argument("--tool-dir", type=Path)
    parser.add_argument(
        "--input-hex", type=Path, help="one pair of hex Z and X check words per line"
    )
    args = parser.parse_args()
    d = args.distance
    if not 3 <= d <= 33 or d % 2 == 0 or args.samples < 1:
        parser.error("choose an odd distance from 3 to 33 and a positive sample count")
    output = ROOT / "build" / f"hdrg_stream_d{d}_{time.time_ns()}"
    output.mkdir(parents=True)
    print(f"Run directory: {output}", flush=True)
    records = (
        read_pairs(args.input_hex, d) if args.input_hex else trials(d, args.samples)
    )
    write_vectors(d, records, output / "stimulus.txt")
    (output / "test_top.sv").write_text(
        f"module test_top; tb_hdrg_stream #(.D({d}),.NF({len(records)})) trial(); endmodule\n",
        encoding="ascii",
    )
    sources = [
        str(ROOT / "rtl/hdrg_stream/hdrg_stream.sv"),
        str(ROOT / "examples/hdrg_stream/tb_hdrg_stream.sv"),
        "test_top.sv",
    ]
    if args.simulator == "xsim":
        commands = [
            ["xvlog", "--sv", *sources],
            ["xelab", "test_top", "-s", "walker"],
            ["xsim", "walker", "-R"],
        ]
    else:
        commands = [
            ["iverilog", "-g2012", "-s", "test_top", "-o", "walker.vvp", *sources],
            ["vvp", "-n", "walker.vvp"],
        ]
    env = os.environ.copy()
    if args.tool_dir:
        env["PATH"] = str(args.tool_dir.resolve()) + os.pathsep + env.get("PATH", "")
    for name, *argv in commands:
        run(
            [find_tool(name, args.tool_dir), *argv],
            output,
            name + ".stdout.log",
            1800,
            env,
        )
    log = (output / (commands[-1][0] + ".stdout.log")).read_text(
        encoding="utf8", errors="replace"
    )
    if f"WALKER_PASS d={d} frames={len(records)}" not in log.splitlines():
        raise RuntimeError(f"Incomplete RTL simulation. See {output}")
    with (output / "responses.csv").open(newline="") as stream:
        rows = [tuple(map(int, row)) for row in csv.reader(stream)]
    if [row[0] for row in rows] != list(range(len(records))):
        raise RuntimeError("Missing or duplicated frame")
    cycles = np.array([row[1] for row in rows])
    summary = {
        "distance": d,
        "frames": len(records),
        "min_cycles": int(cycles.min()),
        "median_cycles": float(np.median(cycles)),
        "max_cycles": int(cycles.max()),
        "raw": [
            {"frame": i, "trial": label, "cycles": int(cycles[i])}
            for i, (label, _, _) in enumerate(records)
        ],
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf8"
    )
    print(
        f"PASS: d={d}, {len(records)} records; clocks {cycles.min()} to {cycles.max()}"
    )


if __name__ == "__main__":
    main()
