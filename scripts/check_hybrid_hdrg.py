"""Check complete CNN/HDRG frames against integer convolution and spatial HDRG."""

import argparse
import csv
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

import numpy as np
from simulate_arty import ROOT, find_tool, run
from simulate_distances import load_parameters, predict

sys.path.insert(0, str(ROOT))
from qec.hdrg import SurfaceHDRG
from qec.spatial import Geometry


def hex_bits(bits):
    return format(int.from_bytes(np.packbits(bits, bitorder="little"), "little"), "x")


def syndromes(geometry, samples, seed, noise_rate=None):
    if noise_rate is None:
        n = len(geometry.zchecks)
        zero = np.zeros(n, np.uint8)
        yield "empty", "", zero, zero
        # One interior fired check has a long path to the boundary.
        middle = zero.copy()
        middle[n // 2] = 1
        yield "isolated_z_check", "", middle, zero
        yield "all_checks", "", np.ones(n, np.uint8), np.ones(n, np.uint8)
        yield (
            "alternate_checks",
            "",
            np.arange(n, dtype=np.uint32) % 2,
            (np.arange(n) % 2) ^ 1,
        )
    rng = np.random.default_rng(np.random.SeedSequence([seed, geometry.d]))
    rates = (0.01, 0.05, 0.13, 0.20) if noise_rate is None else (noise_rate,)
    for p in rates:
        for i in range(samples):
            # Equal-probability X, Y and Z on a faulty site.
            u = rng.random(geometry.nd)
            x = (u < 2 * p / 3).astype(np.uint8)
            z = ((u >= p / 3) & (u < p)).astype(np.uint8)
            sz, sx = [(h @ mask) % 2 for h, mask in zip(geometry.H, (x, z))]
            yield f"pauli_{i}", p, sz, sx


def write_vectors(d, samples, seed, out, noise_rate=None):
    geometry, decoder = Geometry(d), SurfaceHDRG(d)
    weights = load_parameters(ROOT / "models/2d")
    shape = (4, 2 * d - 1, 2 * d - 1)
    cases = []
    raw_syndromes = []
    with (
        (out / "cnn_inputs.hex").open("w", encoding="ascii") as inputs,
        (out / "expected.hex").open("w", encoding="ascii") as expected,
        (out / "residual_syndromes.hex").open("w", encoding="ascii") as residuals,
    ):
        count = 4 + 4 * samples if noise_rate is None else samples
        expected.write(f"{count}\n")
        for label, p, sz, sx in syndromes(geometry, samples, seed, noise_rate):
            raw_syndromes.append(f"{hex_bits(sz)} {hex_bits(sx)}\n")
            board = np.zeros(shape, dtype=np.int64)
            board[0, 1::2, ::2] = sx.reshape(d - 1, d)
            board[1, ::2, 1::2] = sz.reshape(d, d - 1)
            board[2:] = geometry.boundary
            image = np.pad(sum(channel << i for i, channel in enumerate(board)), 5)
            inputs.writelines(f"{v:x}\n" for v in image.ravel())
            local = predict(board, weights)[:, geometry.rr, geometry.cc].astype(
                np.uint8
            )
            if not (sz.any() or sx.any()):
                local.fill(0)
            residual = [
                s ^ ((h @ mask) % 2) for s, h, mask in zip((sz, sx), geometry.H, local)
            ]
            # The standalone HDRG bench takes Z checks first, then X checks.
            print(*map(hex_bits, residual), file=residuals)
            global_masks = decoder.decode_batch([s[None, :] for s in residual])[0]
            final = local ^ global_masks
            for s, h, mask in zip((sz, sx), geometry.H, final):
                if not np.array_equal(s, (h @ mask) % 2):
                    raise RuntimeError("Software correction left fired checks")
            expected.write(" ".join(map(hex_bits, [*local, *residual, *final])) + "\n")
            cases.append(
                {
                    "distance": d,
                    "case": label,
                    "physical_error_probability": p,
                    "fired": int(sz.sum() + sx.sum()),
                    "residual_fired": sum(int(s.sum()) for s in residual),
                }
            )
    # The standalone checker accepts this file for a same-record timing comparison.
    (out / "raw_syndromes.hex").write_text("".join(raw_syndromes), encoding="ascii")
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distance", type=int, default=5, choices=range(3, 34, 2))
    parser.add_argument("--samples-per-rate", type=int, default=4)
    parser.add_argument(
        "--noise-rate",
        type=float,
        help="Run only independent inputs at this physical error probability",
    )
    parser.add_argument("--seed", type=int, default=330913)
    parser.add_argument(
        "--simulator", choices=["verilator", "xsim", "iverilog"], default="verilator"
    )
    parser.add_argument("--cmake", default="cmake")
    parser.add_argument(
        "--tool-dir", type=Path, help="Vivado or Icarus simulator bin directory"
    )
    parser.add_argument(
        "--build-dir", type=Path, help="CMake build directory for this distance"
    )
    args = parser.parse_args()
    if not 0 <= args.samples_per_rate <= 1000:
        parser.error("samples-per-rate must be in [0,1000]")
    if args.noise_rate is not None and (
        not 0 <= args.noise_rate <= 1 or args.samples_per_rate == 0
    ):
        parser.error(
            "a single-rate run needs at least one sample and a probability in [0,1]"
        )
    d = args.distance
    out = ROOT / "build" / f"hybrid_hdrg_d{d}_{time.time_ns()}"
    out.mkdir(parents=True)
    print(f"Run directory: {out}", flush=True)
    cases = write_vectors(d, args.samples_per_rate, args.seed, out, args.noise_rate)
    for f in (ROOT / "models/2d").glob("*.hex"):
        shutil.copyfile(f, out / f.name)
    example = ROOT / "examples/hybrid_hdrg"
    env = os.environ.copy()
    if args.simulator == "verilator":
        build = (args.build_dir or out / "compiled").resolve()
        run(
            [
                args.cmake,
                "-S",
                str(example),
                "-B",
                str(build),
                f"-DDISTANCE={d}",
                "-DCMAKE_BUILD_TYPE=Release",
            ],
            out,
            "configure.log",
            600,
            env,
        )
        run(
            [
                args.cmake,
                "--build",
                str(build),
                "--config",
                "Release",
                "--parallel",
                "4",
            ],
            out,
            "compile.log",
            1200,
            env,
        )
        name = "hybrid_hdrg.exe" if os.name == "nt" else "hybrid_hdrg"
        executable = next(
            (f for f in [build / name, build / "Release" / name] if f.is_file()), None
        )
        if not executable:
            raise RuntimeError(f"Missing simulation executable under {build}")
        run([str(executable)], out, "simulation.log", 7200, env)
    else:
        top = out / "test_top.sv"
        top.write_text(
            f"module test_top; tb_hybrid_hdrg #(.D({d})) t(); endmodule\n", "ascii"
        )
        sources = [
            *sorted((ROOT / "rtl/arty_2d").glob("*.v")),
            ROOT / "rtl/hdrg_stream/hdrg_stream.sv",
            ROOT / "rtl/hybrid_hdrg/cnn_hdrg.sv",
            example / "tb_hybrid_hdrg.sv",
            top,
        ]
        if args.tool_dir:
            env["PATH"] = (
                str(args.tool_dir.resolve()) + os.pathsep + env.get("PATH", "")
            )
        if args.simulator == "iverilog":
            commands = [
                (
                    "iverilog",
                    [
                        "-g2012",
                        "-s",
                        "test_top",
                        "-o",
                        "hybrid.vvp",
                        *map(str, sources),
                    ],
                    "compile.log",
                ),
                ("vvp", ["-n", "hybrid.vvp"], "simulation.log"),
            ]
        else:
            commands = [
                ("xvlog", ["--sv", *map(str, sources)], "compile.stdout.log"),
                ("xelab", ["test_top", "-s", "hybrid"], "elaborate.stdout.log"),
                ("xsim", ["hybrid", "-R"], "simulation.log"),
            ]
        for exe, argv, log in commands:
            run([find_tool(exe, args.tool_dir), *argv], out, log, 7200, env)
    log = (out / "simulation.log").read_text("utf8", errors="replace")
    rows = re.findall(
        r"^HYBRID (\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+)$", log, re.MULTILINE
    )
    if len(rows) != len(cases) or f"HYBRID_PASS {len(cases)}" not in log.splitlines():
        raise RuntimeError(f"Missing verified frames. Inspect {out}")
    for i, (case, row) in enumerate(zip(cases, rows)):
        distance, index, cnn, handoff, hdrg, added, total = map(int, row)
        if (
            (distance, index) != (d, i)
            or added != handoff + hdrg + 1
            or total != cnn + added
        ):
            raise RuntimeError("Invalid completion timestamps")
        case.update(
            cnn_cycles=cnn,
            read_and_accept_cycles=handoff,
            hdrg_cycles=hdrg,
            added_cycles=added,
            total_cycles=total,
        )
    with (out / "results.csv").open("w", encoding="utf8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=cases[0])
        writer.writeheader()
        writer.writerows(cases)
    (out / "run.json").write_text(
        json.dumps(
            {
                "seed": args.seed,
                "distance": d,
                "noise_rate": args.noise_rate,
                "samples_per_rate": args.samples_per_rate,
                "simulator": args.simulator,
                "frames": len(cases),
            },
            indent=2,
        )
        + "\n",
        "utf8",
    )
    times = [row["added_cycles"] for row in cases]
    print(
        f"PASS: {len(cases)} frames at d={d}. Observed CNN-to-correction time: {min(times)}–{max(times)} clocks."
    )


if __name__ == "__main__":
    main()
