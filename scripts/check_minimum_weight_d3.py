"""Check the distance-three runtime minimum-weight solver against every physical mask."""
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
from qec.spatial import Geometry
from simulate_arty import find_tool, run


def minimum_masks(geometry, component):
    """Choose the first minimum in the hardware's free-bit order, by brute force."""
    checks = geometry.H[component]
    masks = np.arange(8192, dtype=int)
    bits = ((masks[:, None] >> np.arange(13)) & 1).astype(np.uint8)
    ids = ((checks @ bits.T).T % 2) @ (1 << np.arange(6))
    weights = bits.sum(axis=1)
    # Interior sites, including the horizontal midpoints of the top and bottom.
    # The Z engine transposes the patch before numbering the seven free bits.
    sites = [s for s in geometry.sites if s[1] not in (0, 4)]
    free_order = [geometry.sites.index((c, r) if component else (r, c)) for r, c in sites]
    free_id = bits[:, free_order] @ (1 << np.arange(7))
    best = np.empty(64, dtype=int)
    for syndrome in range(64):
        choices = masks[ids == syndrome]
        if len(choices) != 128:
            raise ValueError("Unexpected dimension for the distance-three check matrix")
        minima = choices[weights[choices] == weights[choices].min()]
        best[syndrome] = minima[np.argmin(free_id[minima])]

    # Use the same uniform edge weight as the p=0.13 spatial comparison.
    component_error = 2 * 0.13 / 3
    matching = pymatching.Matching(checks, weights=float(np.log((1-component_error)/component_error)))
    syndromes = ((np.arange(64)[:, None] >> np.arange(6)) & 1).astype(np.uint8)
    matching_bits = matching.decode_batch(syndromes)
    if not np.array_equal(matching_bits.sum(axis=1), weights[best]):
        raise ValueError("Software matching disagrees with the exhaustive minimum")
    change = matching_bits ^ bits[best]
    logical_sites = geometry.lz if component == 0 else geometry.lx
    different_masks = np.any(change, axis=1).sum()
    different_logicals = (change[:, logical_sites].sum(axis=1) % 2).sum()
    print(f"Component {component}: {different_masks}/64 masks differ from PyMatching, "
          f"{different_logicals}/64 differ in logical parity, all have equal minimum weight")
    return best


def check_log(path):
    geometry = Geometry(3)
    x_best, z_best = [minimum_masks(geometry, component) for component in (0, 1)]
    seen = set()
    previous_accept = None
    intervals, latencies = set(), set()
    with path.open(newline="") as stream:
        for row in csv.reader(stream):
            record, x_mask, z_mask, accepted, returned = map(int, row)
            if not 0 <= record < 4096 or record in seen:
                raise ValueError(f"Invalid or duplicate syndrome pair {record}")
            if (x_mask, z_mask) != (x_best[record >> 6], z_best[record & 63]):
                raise ValueError(f"Unexpected correction for syndrome pair {record}")
            latencies.add(returned - accepted)
            if previous_accept is not None:
                intervals.add(accepted - previous_accept)
            previous_accept = accepted
            seen.add(record)
    if len(seen) != 4096 or latencies != {21} or min(intervals, default=0) != 16:
        raise ValueError("Missing results or incorrect latency or input interval")
    print("PASS: all 4,096 mask pairs agree with exhaustive search; latency 21 clocks, "
          "minimum input interval 16 clocks")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", type=Path, metavar="RESPONSES")
    action.add_argument("--simulate", action="store_true")
    parser.add_argument("--tool-dir", type=Path)
    parser.add_argument("--simulator", choices=("xsim", "iverilog"), default="xsim")
    args = parser.parse_args()
    if args.check:
        check_log(args.check)
        return

    work = ROOT / "build" / f"minimum_weight_d3_{time.time_ns()}"
    work.mkdir(parents=True)
    sources = ["minimum_weight_d3.v", "tb_minimum_weight_d3.sv"]
    shutil.copy2(ROOT / "rtl/minimum_weight_d3" / sources[0], work)
    shutil.copy2(ROOT / "examples/minimum_weight_d3" / sources[1], work)
    if args.simulator == "iverilog":
        commands = [["iverilog", "-g2012", "-s", "tb_minimum_weight_d3", "-o", "mw.vvp", *sources],
                    ["vvp", "-n", "mw.vvp"]]
    else:
        commands = [["xvlog", "--sv", *sources], ["xelab", "tb_minimum_weight_d3", "-s", "mw"],
                    ["xsim", "mw", "-R"]]
    environment = os.environ.copy()
    if args.tool_dir:
        environment["PATH"] = str(args.tool_dir.resolve()) + os.pathsep + environment.get("PATH", "")
    print(f"Simulation logs: {work}", flush=True)
    for name, *arguments in commands:
        run([find_tool(name, args.tool_dir), *arguments], work, name + ".stdout.log", 300, environment)
    result = (work / (commands[-1][0] + ".stdout.log")).read_text(encoding="utf8", errors="replace")
    if "PASS: all 4096 records completed in 21 clocks" not in result:
        raise RuntimeError(f"RTL timing check failed. See {work}")
    check_log(work / "responses.csv")


if __name__ == "__main__":
    main()
