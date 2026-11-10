"""Check the banked 2D core at distances 5, 9, 17 and 33 with Icarus."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time

import numpy as np

from simulate_arty import ROOT, find_tool, run


# These are core simulation configurations, not four placed board designs.
# DSP directives change placement choices, not the integer operations.
CASES = {5: (4, 4377), 9: (4, 12561), 17: (4, 41601), 33: (6, 103894)}


def signed(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def load_parameters(folder):
    """Unpack the same fixed coefficients read by the RTL ROMs."""
    layers = []
    for layer, (ci, co) in enumerate(((4, 9), (9, 9), (9, 2))):
        words = (folder / f"L{layer}_weights_par.hex").read_text().split()
        biases = (folder / f"L{layer}_bias.hex").read_text().split()
        if len(words) != 121 or len(biases) != co:
            raise ValueError("Unexpected coefficient dimensions")
        weights = np.empty((co, ci, 11, 11), dtype=np.int64)
        for tap, word in enumerate(words):
            value = int(word, 16)
            if not 0 <= value < 1 << (8 * ci * co):
                raise ValueError("Weight word exceeds its packed width")
            for out in range(co):
                for channel in range(ci):
                    weights[out, channel, tap // 11, tap % 11] = signed(
                        (value >> (8 * (out * ci + channel))) & 255, 8)
        packed_biases = [int(word, 16) for word in biases]
        if any(not 0 <= value < 1 << 48 for value in packed_biases):
            raise ValueError("Bias word exceeds 48 bits")
        layers.append((weights, np.array([signed(v, 48) for v in packed_biases],
                                         dtype=np.int64)))
    return layers


def convolution(inputs, weights, bias):
    """Direct integer convolution, independent of the RTL's lane schedule."""
    _, height, width = inputs.shape
    padded = np.pad(inputs, ((0, 0), (5, 5), (5, 5)))
    result = np.zeros((len(bias), height, width), dtype=np.int64)
    for row in range(11):
        for column in range(11):
            result += np.einsum("oi,ihw->ohw", weights[:, :, row, column],
                                padded[:, row:row + height, column:column + width])
    return result + bias[:, None, None]


def predict(inputs, parameters):
    activations = inputs
    for layer, (weights, bias) in enumerate(parameters):
        raw = convolution(activations, weights, bias)
        activations = np.maximum(raw, 0) if layer < 2 else raw > 0
    return activations


def make_vectors(distance, frames, seed, parameters):
    """Exercise sparse and dense inputs at the usual syndrome locations.

    Independent syndrome bits are useful hardware stimuli. They are not
    samples from a physical error model and do not measure decoding accuracy.
    """
    side = 2 * distance - 1
    rng = np.random.default_rng(seed)
    inputs, decisions = [], []
    for frame in range(frames):
        x = np.zeros((4, side, side), dtype=np.int64)
        density = (0.05, 0.30, 0.50)[frame % 3]
        x[0, 1::2, ::2] = rng.random((distance - 1, distance)) < density
        x[1, ::2, 1::2] = rng.random((distance, distance - 1)) < density
        x[2, 0, ::2] = x[2, -1, ::2] = 1
        x[3, ::2, 0] = x[3, ::2, -1] = 1
        output = predict(x, parameters)
        packed = sum(x[channel] << channel for channel in range(4))
        inputs.extend(int(value) for value in np.pad(packed, 5).ravel())
        decisions.extend(int(value) for value in (output[0] + 2 * output[1]).ravel())
    return inputs, decisions


def require_pass(text, distance, frames, cycles):
    expected_bits = 2 * (2 * distance - 1) ** 2
    records = re.findall(r"^DISTANCE_FRAME frame=(\d+) d=(\d+) cycles=(\d+) "
                         r"decision_bits=(\d+) mismatches=(\d+)$", text, re.MULTILINE)
    expected = [(str(i), str(distance), str(cycles), str(expected_bits), "0")
                for i in range(frames)]
    if (records != expected or f"DISTANCE_PASS frames={frames}" not in text.splitlines()
            or "ERROR:" in text.upper() or "FATAL:" in text.upper()):
        raise RuntimeError("Missing, incomplete or failed distance check")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distances", type=int, nargs="+", choices=CASES, default=list(CASES))
    parser.add_argument("--frames", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20261102)
    parser.add_argument("--tool-dir", type=Path, help="Directory containing iverilog and vvp")
    parser.add_argument(
        "--timeout-seconds", type=int, default=1800,
        help="time limit for each compile or simulation in seconds (default: %(default)s)",
    )
    parser.add_argument("--output", type=Path, help="New directory; existing runs are never overwritten")
    args = parser.parse_args()
    if args.frames < 1 or args.timeout_seconds < 1 or args.seed < 0:
        parser.error("frames and timeout must be positive; seed must be non-negative")
    if len(set(args.distances)) != len(args.distances):
        parser.error("each distance must appear only once")
    tools = {name: find_tool(name, args.tool_dir) for name in ("iverilog", "vvp")}
    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join(
        [*(str(Path(path).parent) for path in tools.values()), environment.get("PATH", "")])
    output = (args.output or ROOT / "build" / f"distances_{time.time_ns()}").resolve()
    output.mkdir(parents=True, exist_ok=False)
    parameters = load_parameters(ROOT / "models/2d")
    sources = [*sorted((ROOT / "rtl/arty_2d").glob("*.v")),
               ROOT / "examples/arty_2d_distances/tb_distances.sv"]
    summaries = []
    for distance in args.distances:
        case = output / f"d{distance}"
        case.mkdir()
        p2, cycles = CASES[distance]
        for source in [*sources, *sorted((ROOT / "models/2d").glob("L*.hex"))]:
            shutil.copyfile(source, case / source.name)
        vectors = make_vectors(distance, args.frames, args.seed + distance, parameters)
        for name, words in zip(("inputs.hex", "decisions.hex"), vectors):
            (case / name).write_text("".join(f"{word:x}\n" for word in words), encoding="ascii")
        command = [tools["iverilog"], "-g2012", "-s", "tb_distances", "-o", "simulation.vvp",
                   f"-Ptb_distances.D={distance}", f"-Ptb_distances.P2={p2}",
                   f"-Ptb_distances.NF={args.frames}", f"-Ptb_distances.EXPECT={cycles}",
                   *[source.name for source in sources]]
        run(command, case, "compile.log", args.timeout_seconds, environment)
        run([tools["vvp"], "-n", "simulation.vvp"], case, "simulate.log",
            args.timeout_seconds, environment)
        log = (case / "simulate.log").read_text(encoding="utf8", errors="replace")
        require_pass(log, distance, args.frames, cycles)
        summary = {"distance": distance, "lanes": [11, p2, 6], "dsp_l2": 1, "dsp_l3": 6,
                   "frames": args.frames, "seed": args.seed + distance, "cycles": cycles,
                   "decision_bits": 2 * (2 * distance - 1) ** 2 * args.frames,
                   "input_sha256": hashlib.sha256((case / "inputs.hex").read_bytes()).hexdigest(),
                   "decisions_sha256": hashlib.sha256((case / "decisions.hex").read_bytes()).hexdigest()}
        summaries.append(summary)
        print(f"d={distance}: {args.frames} frames, {cycles} clocks, "
              f"{summary['decision_bits']} prediction bits agree", flush=True)
    (output / "summary.json").write_text(json.dumps(summaries, indent=2) + "\n", encoding="utf8")
    print(f"Logs: {output}")


if __name__ == "__main__":
    main()
