"""Exercise the 2D CNN, decision RAM, residual and combined masks together."""

import argparse
import os
import re
import shutil
import time
from pathlib import Path

import numpy as np
from check_cleanup_d3 import expected_tables, pack
from simulate_arty import ROOT, find_tool, run
from simulate_distances import load_parameters, predict

from qec.spatial import Geometry


def vectors(count, folder):
    geom = Geometry(3)
    weights = load_parameters(ROOT / "models/2d")
    tables = expected_tables()
    ids = [0, 4095, *(1 << bit for bit in range(12)), 0x555, 0xAAA]
    rng = np.random.default_rng(713)
    ids.extend(rng.choice(np.setdiff1d(np.arange(4096), ids), 16, replace=False))
    inputs, answers = [], []
    for syndrome in ids[: count // 2]:
        sz, sx = int(syndrome) >> 6, int(syndrome) & 63
        board = np.zeros((4, 5, 5), dtype=np.int64)
        for channel, checks, word in [(0, geom.xchecks, sx), (1, geom.zchecks, sz)]:
            for k, (r, c) in enumerate(checks):
                board[channel, r, c] = (word >> k) & 1
        board[2:] = geom.boundary
        padded = np.pad(sum(plane << i for i, plane in enumerate(board)), 5)
        local = predict(board, weights)[:, geom.rr, geom.cc].astype(np.uint8)
        if syndrome == 0:
            local.fill(0)
        residuals = [
            s ^ pack((h @ mask) % 2) for s, h, mask in zip((sz, sx), geom.H, local)
        ]
        lx, lz = map(pack, local)
        for mode in (0, 1):
            fx = lx ^ tables[0][64 * mode + residuals[0]]
            fz = lz ^ tables[1][64 * mode + residuals[1]]
            inputs.extend(f"{n:x}\n" for n in padded.ravel())
            packed = (mode << 52) | (lx << 39) | (lz << 26) | (fx << 13) | fz
            answers.append(f"{packed:014x}\n")
    (folder / "inputs.hex").write_text("".join(inputs), encoding="ascii")
    (folder / "expected.hex").write_text("".join(answers), encoding="ascii")
    (folder / "frames.txt").write_text(f"{count}\n", encoding="ascii")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulator", choices=["xsim", "iverilog"], default="xsim")
    parser.add_argument("--tool-dir", type=Path)
    parser.add_argument(
        "--frames",
        type=int,
        choices=range(2, 65, 2),
        default=64,
        metavar="EVEN",
        help="two modes per input, 2 to 64 frames",
    )
    args = parser.parse_args()
    out = ROOT / "build" / f"hybrid_d3_{time.time_ns()}"
    out.mkdir(parents=True)
    vectors(args.frames, out)
    for f in [
        *(ROOT / "models/2d").glob("*.hex"),
        *(ROOT / "rtl/cleanup_d3").glob("*.hex"),
    ]:
        shutil.copyfile(f, out / f.name)
    sources = [
        *sorted((ROOT / "rtl/arty_2d").glob("*.v")),
        ROOT / "rtl/cleanup_d3/cleanup_d3.v",
        ROOT / "rtl/hybrid_d3/cnn_cleanup_d3.v",
        ROOT / "examples/hybrid_d3/tb_hybrid.sv",
    ]
    files = [str(s) for s in sources]
    if args.simulator == "xsim":
        commands = [
            ["xvlog", "--sv", *files],
            ["xelab", "tb_hybrid", "-s", "hybrid"],
            ["xsim", "hybrid", "-R"],
        ]
    else:
        commands = [
            ["iverilog", "-g2012", "-s", "tb_hybrid", "-o", "hybrid.vvp", *files],
            ["vvp", "-n", "hybrid.vvp"],
        ]
    env = os.environ.copy()
    if args.tool_dir:
        env["PATH"] = str(args.tool_dir.resolve()) + os.pathsep + env.get("PATH", "")
    for name, *argv in commands:
        run(
            [find_tool(name, args.tool_dir), *argv],
            out,
            name + ".stdout.log",
            1200,
            env,
        )
    log = (out / (commands[-1][0] + ".stdout.log")).read_text(
        encoding="utf8", errors="replace"
    )
    rows = re.findall(
        r"^HYBRID_FRAME frame=(\d+) cnn_cycles=(\d+) added_cycles=(\d+) total_cycles=(\d+)$",
        log,
        re.MULTILINE,
    )
    if (
        [int(row[0]) for row in rows] != list(range(args.frames))
        or len({row[1:] for row in rows}) != 1
        or f"HYBRID_PASS frames={args.frames}" not in log.splitlines()
    ):
        raise RuntimeError(f"Incomplete simulation. See {out}")
    print(
        f"PASS: {len(rows)} complete frames; CNN, added and total cycles: {rows[0][1:]}. Logs: {out}"
    )


if __name__ == "__main__":
    main()
