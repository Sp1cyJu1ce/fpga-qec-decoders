"""Check that a failed or incomplete simulator run cannot look like a pass."""

from contextlib import redirect_stderr
import importlib.util
from io import StringIO
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("simulate_arty", ROOT / "scripts/simulate_arty.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
distance_spec = importlib.util.spec_from_file_location(
    "simulate_distances", ROOT / "scripts/simulate_distances.py")
distances = importlib.util.module_from_spec(distance_spec)
sys.modules.setdefault("simulate_arty", runner)
distance_spec.loader.exec_module(distances)


class RunnerTests(unittest.TestCase):
    def test_failed_process_preserves_log_and_raises(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            with self.assertRaisesRegex(RuntimeError, "exit code 7"):
                runner.run([sys.executable, "-c", "print('SMOKE_PASS'); raise SystemExit(7)"],
                           output, "failure.log", 10, os.environ.copy())
            self.assertIn("SMOKE_PASS", (output / "failure.log").read_text())

    def test_timeout_stops_process(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(subprocess.TimeoutExpired):
                runner.run([sys.executable, "-c", "import time; time.sleep(30)"],
                           Path(folder), "timeout.log", 0.1, os.environ.copy())

    def test_pass_requires_correct_design_and_no_fatal(self):
        cases = {
            "2d": "SMOKE_PASS 2d d=5 cycles=4377 raw_values=1620 activations_and_decisions=1620\n",
            "3d": "SMOKE_PASS 3d d=5 tco=32 cycles=223603 raw_values=38016 activations_and_decisions=38016\n",
        }
        for design, good in cases.items():
            runner.require_pass(good, design)
            for text in ("", cases["3d" if design == "2d" else "2d"],
                         good.replace("d=5", "d=7"), good.replace("tco=32 ", ""),
                         good.replace("cycles=", "cycles=1"), good + "FATAL: bad value\n",
                         "ERROR: bad value\n" + good):
                if text == good:
                    continue
                with self.subTest(design=design, log=text), self.assertRaises(RuntimeError):
                    runner.require_pass(text, design)

    def test_invalid_options_do_not_open_tools_or_outputs(self):
        for arguments in (["3d", "--simulator", "iverilog"], ["2d", "--timeout-seconds", "0"]):
            with self.subTest(arguments=arguments), patch("sys.argv", ["simulate_arty", *arguments]), \
                    patch.object(runner, "find_tool") as tool, redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as error:
                    runner.main()
                self.assertEqual(error.exception.code, 2)
                tool.assert_not_called()

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            marker = output / "keep.txt"
            marker.write_text("existing run")
            arguments = ["simulate_arty", "2d", "--simulator", "iverilog", "--output", folder]
            with patch("sys.argv", arguments), patch.object(runner, "find_tool", return_value=sys.executable):
                with self.assertRaises(FileExistsError):
                    runner.main()
            self.assertEqual(marker.read_text(), "existing run")
            self.assertEqual(list(output.iterdir()), [marker])


class DistanceRunnerTests(unittest.TestCase):
    def test_all_frames_and_counts_are_required(self):
        lines = [f"DISTANCE_FRAME frame={i} d=5 cycles=4377 decision_bits=162 mismatches=0"
                 for i in range(3)]
        good = "\n".join([*lines, "DISTANCE_PASS frames=3", ""])
        distances.require_pass(good, 5, 3, 4377)
        invalid = [good.replace(lines[1] + "\n", ""), good + "ERROR: missing ROM\n",
                   good.replace("cycles=4377", "cycles=4376"),
                   good.replace("decision_bits=162", "decision_bits=160"),
                   good.replace("mismatches=0", "mismatches=1"),
                   good.replace("DISTANCE_PASS frames=3", "")]
        for text in invalid:
            with self.subTest(log=text), self.assertRaises(RuntimeError):
                distances.require_pass(text, 5, 3, 4377)

    def test_numpy_reference_matches_retained_layer_values(self):
        import numpy as np

        fixture = ROOT / "examples/arty_2d_d5"
        packed = np.array([int(word, 16) for word in
                           (fixture / "input.hex").read_text().split()], dtype=np.int64)
        packed = packed.reshape(19, 19)[5:-5, 5:-5]
        activation = np.stack([(packed >> channel) & 1 for channel in range(4)])
        parameters = distances.load_parameters(ROOT / "models/2d")
        for layer, ((weights, bias), width) in enumerate(zip(parameters, (16, 24, 36)), 1):
            raw = distances.convolution(activation, weights, bias)
            words = [int(word, 16) for word in
                     (fixture / f"layer{layer}_raw.hex").read_text().split()]
            expected = np.array([[distances.signed((word >> (channel * width)) &
                                                  ((1 << width) - 1), width)
                                  for word in words] for channel in range(len(bias))])
            np.testing.assert_array_equal(raw.reshape(len(bias), -1), expected)
            activation = np.maximum(raw, 0) if layer < 3 else raw > 0
        decisions = [int(word, 16) for word in (fixture / "decisions.hex").read_text().split()]
        np.testing.assert_array_equal((activation[0] + 2 * activation[1]).ravel(), decisions)

    def test_invalid_distance_options_do_not_start_tools(self):
        for args in (["--frames", "0"], ["--distances", "5", "5"], ["--seed", "-1"]):
            with self.subTest(args=args), patch("sys.argv", ["simulate_distances", *args]), \
                    patch.object(distances, "find_tool") as tool, redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as error:
                    distances.main()
                self.assertEqual(error.exception.code, 2)
                tool.assert_not_called()


if __name__ == "__main__":
    unittest.main()
