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
        good = "SMOKE_PASS 2d d=5 cycles=4377 raw_values=1620\n"
        runner.require_pass(good, "2d")
        for text in ("", good.replace("2d", "3d"), good + "FATAL: bad value\n",
                     "ERROR: bad value\n" + good):
            with self.subTest(log=text), self.assertRaises(RuntimeError):
                runner.require_pass(text, "2d")

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


if __name__ == "__main__":
    unittest.main()
