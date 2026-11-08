"""Generate the d=3 cleanup ROMs or check every result recorded by the RTL."""
import argparse
import csv
import os
from pathlib import Path
import shutil
import sys
import time

import numpy as np
import pymatching

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from qec.hdrg import SurfaceHDRG
from qec.spatial import Geometry
from simulate_arty import find_tool, run


def pack(bits):
    return sum(int(value) << i for i, value in enumerate(bits))


def expected_tables():
    geometry = Geometry(3)
    hdrg = SurfaceHDRG(3)
    tables = []
    bits = (np.arange(64)[:, None] >> np.arange(6)) & 1
    for component, checks in enumerate(geometry.H):
        # Use the uniform edge weight of the p=0.13 spatial comparison.
        p_component = 2 * 0.13 / 3
        matching = pymatching.Matching(checks, weights=float(np.log((1-p_component)/p_component)))
        minimum_weight = matching.decode_batch(bits.astype(np.uint8))
        clustered = hdrg.decoders[component].decode_batch(bits)
        corrections = np.vstack((minimum_weight, clustered))
        if not np.array_equal((checks @ corrections.T).T % 2, np.tile(bits, (2, 1))):
            raise ValueError(f"Component {component}: a correction leaves a fired check")

        # Independently enumerate the 8192 physical masks in this component.
        # Equal-weight matching solutions can choose different paths.
        masks = ((np.arange(8192)[:, None] >> np.arange(13)) & 1).astype(np.uint8)
        syndrome_ids = ((checks @ masks.T).T % 2) @ (1 << np.arange(6))
        minima = np.full(64, 14)
        np.minimum.at(minima, syndrome_ids, masks.sum(axis=1).astype(int))
        if not np.array_equal(minimum_weight.sum(axis=1), minima):
            raise ValueError(f"Component {component}: MWPM output exceeds the minimum weight")
        tables.append([pack(correction) for correction in corrections])
    return tables


def simulate(output, tool_dir, simulator):
    output.mkdir(parents=True, exist_ok=False)
    design = ROOT / "rtl/cleanup_d3"
    for name in ("cleanup_d3.v", "x_corrections.hex", "z_corrections.hex"):
        shutil.copyfile(design / name, output / name)
    shutil.copyfile(ROOT / "examples/cleanup_d3/tb_cleanup.sv", output / "tb_cleanup.sv")
    sources = ["cleanup_d3.v", "tb_cleanup.sv"]
    if simulator == "iverilog":
        commands = [
            ["iverilog", "-g2012", "-s", "tb_cleanup", "-o", "cleanup.vvp", *sources],
            ["vvp", "-n", "cleanup.vvp"],
        ]
    else:
        commands = [
            ["xvlog", "--sv", *sources],
            ["xelab", "tb_cleanup", "-s", "cleanup"],
            ["xsim", "cleanup", "-R"],
        ]
    environment = os.environ.copy()
    if tool_dir:
        environment["PATH"] = str(tool_dir.resolve()) + os.pathsep + environment.get("PATH", "")
    for name, *arguments in commands:
        run([find_tool(name, tool_dir), *arguments], output, name + ".log", 300, environment)
    log = (output / (commands[-1][0] + ".log")).read_text(encoding="utf8", errors="replace")
    if "PASS: 8192 records, one cycle from acceptance to completion" not in log:
        raise RuntimeError(f"The RTL latency check failed. See {output}")
    return output / "responses.csv"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", type=Path, metavar="DIR", help="write the two ROM images")
    group.add_argument("--check", type=Path, metavar="CSV", help="check the RTL response log")
    group.add_argument("--simulate", action="store_true", help="run the RTL and check every response")
    parser.add_argument("--tool-dir", type=Path, help="directory containing simulator executables")
    parser.add_argument("--simulator", choices=("xsim", "iverilog"), default="xsim")
    args = parser.parse_args()
    if args.simulate:
        output = ROOT / "build" / f"cleanup_d3_{time.time_ns()}"
        args.check = simulate(output, args.tool_dir, args.simulator)
        print(f"RTL responses: {args.check}", flush=True)
    x_table, z_table = expected_tables()
    if args.write:
        args.write.mkdir(parents=True, exist_ok=True)
        for name, table in (("x", x_table), ("z", z_table)):
            (args.write / f"{name}_corrections.hex").write_text(
                "".join(f"{mask:04x}\n" for mask in table), encoding="ascii"
            )
        return

    seen = set()
    with args.check.open(newline="") as stream:
        for row in csv.reader(stream):
            record, x_mask, z_mask = map(int, row)
            if not 0 <= record < 8192 or record in seen:
                raise ValueError(f"Invalid or repeated input {record}")
            mode = (record >> 12) << 6
            if (x_mask, z_mask) != (x_table[mode | ((record >> 6) & 63)], z_table[mode | (record & 63)]):
                raise ValueError(f"Incorrect correction for input {record}")
            seen.add(record)
    if len(seen) != 8192:
        raise ValueError(f"Received {len(seen)} distinct inputs, expected 8192")
    print("PASS: all 4096 syndromes in each mode match the software corrections")


if __name__ == "__main__":
    main()
