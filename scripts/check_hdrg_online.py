"""Compare run-time HDRG hardware against the spatial decoder at d=3 or d=5."""
import argparse
from collections import Counter
from functools import lru_cache
from itertools import combinations
import os
from pathlib import Path
import random
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from qec.hdrg import SurfaceHDRG
from qec.spatial import Geometry
from simulate_arty import find_tool, run


def trials(distance):
    if distance == 3:
        # Coprime stride visits the entire joint input space in mixed order.
        return [(v >> 6, v & 63) for v in ((i * 2053) % 4096 for i in range(4096))]
    n = distance * (distance - 1)
    low_weight = [sum(1 << i for i in sites)
                  for count in range(3) for sites in combinations(range(n), count)]
    # Recheck the two longest-running component syndromes found by the
    # exhaustive distance-five simulation, separately and together.
    worst_z_checks, worst_x_checks = 330501, 148629
    records = [(0, 0), ((1 << n) - 1, (1 << n) - 1),
               (worst_z_checks, 0), (0, worst_x_checks), (worst_z_checks, worst_x_checks)]
    records.extend(zip(low_weight, reversed(low_weight)))
    rng = random.Random(541)
    for p in (0.1, 0.3, 0.5, 0.8):
        for _ in range(256):
            pair = [sum(1 << i for i in range(n) if rng.random() < p) for _ in range(2)]
            records.append(tuple(pair))
    return list(dict.fromkeys(records))


def reference_cycles(coords, history):
    # One final empty seed check, plus one scale-change edge between rounds.
    cycles = 1 + max(0, len(history) - 1)
    for level in history:
        unvisited = set(level["active"])
        while unvisited:
            seed = min(unvisited)
            frontier = {seed}
            unvisited.remove(seed)
            cycles += 3  # select the seed, detect a stable cluster, resolve it
            while frontier:
                new = {j for j in unvisited if any(
                    max(abs(coords[j][0]-coords[i][0]), abs(coords[j][1]-coords[i][1]))
                    <= level["delta"] for i in frontier)}
                if new:
                    cycles += 1  # one more growth edge per graph hop
                unvisited -= new
                frontier = new
    return cycles


def write_vectors(distance, records, path):
    geometry = Geometry(distance)
    software = SurfaceHDRG(distance)
    counts = Counter()

    @lru_cache(maxsize=None)
    def component(which, value):
        decoder = software.decoders[which]
        syndrome = np.array([(value >> i) & 1 for i in range(len(decoder.coords))], np.uint8)
        mask, history = decoder.decode(syndrome, trace=True)
        if not np.array_equal((geometry.H[which] @ mask) % 2, syndrome):
            raise ValueError("Software correction leaves a fired check")
        packed = sum(int(v) << i for i, v in enumerate(mask))
        return packed, reference_cycles(decoder.coords, history)

    with path.open("w", encoding="ascii") as stream:
        for z_checks, x_checks in records:
            x_mask, x_cycles = component(0, z_checks)
            z_mask, z_cycles = component(1, x_checks)
            latency = max(x_cycles, z_cycles)
            counts[latency] += 1
            print(f"{z_checks:x} {x_checks:x} {x_mask:x} {z_mask:x} {latency}", file=stream)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distance", type=int, choices=(3, 5), default=3)
    parser.add_argument("--simulator", choices=("xsim", "iverilog"), default="xsim")
    parser.add_argument("--tool-dir", type=Path)
    args = parser.parse_args()
    distance = args.distance
    output = ROOT / "build" / f"hdrg_online_d{distance}_{time.time_ns()}"
    output.mkdir(parents=True)
    records = trials(distance)
    counts = write_vectors(distance, records, output / "stimulus.txt")
    # An explicit top works with both simulators and avoids shell-specific
    # quoting of parameter overrides in Vivado's Windows launcher.
    (output / "test_top.sv").write_text(
        f"module test_top; tb_hdrg #(.D({distance})) trial(); endmodule\n", encoding="ascii")
    sources = [str(ROOT / "rtl/hdrg_online/hdrg_online.sv"),
               str(ROOT / "examples/hdrg_online/tb_hdrg.sv"), "test_top.sv"]
    if args.simulator == "iverilog":
        commands = [["iverilog", "-g2012", "-s", "test_top",
                     "-o", "hdrg.vvp", *sources], ["vvp", "-n", "hdrg.vvp"]]
    else:
        commands = [["xvlog", "--sv", *sources],
                    ["xelab", "test_top", "-s", "hdrg"],
                    ["xsim", "hdrg", "-R"]]
    env = os.environ.copy()
    if args.tool_dir:
        env["PATH"] = str(args.tool_dir.resolve()) + os.pathsep + env.get("PATH", "")
    for executable, *arguments in commands:
        run([find_tool(executable, args.tool_dir), *arguments], output, executable + ".stdout.log", 300, env)
    log = (output / (commands[-1][0] + ".stdout.log")).read_text(encoding="utf8", errors="replace")
    if f"PASS: {len(records)} HDRG records" not in log:
        raise RuntimeError(f"RTL did not finish the expected test sequence: {output}")
    print(f"PASS: {len(records)} joint syndromes at d={distance}")
    print(f"Cycles from accepted syndrome to both corrections: {min(counts)} to {max(counts)}")
    print(f"RTL responses: {output / 'responses.csv'}")


if __name__ == "__main__":
    main()
