"""Run distance-five core checks with Vivado, or the 2D check with Icarus."""

import argparse
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time


ROOT = Path(__file__).resolve().parents[1]


def find_tool(name, directory):
    suffixes = (".bat", ".exe", ".cmd", "") if os.name == "nt" else ("",)
    for suffix in suffixes:
        candidate = directory / (name + suffix) if directory else None
        if candidate is not None and candidate.is_file():
            return str(candidate.resolve())
        found = shutil.which(name + suffix) if directory is None else None
        if found:
            return found
    raise FileNotFoundError(f"Cannot find {name}. Set PATH or use --tool-dir.")


def run(command, output, log_name, timeout, environment):
    # Logs stay beside the disposable simulation, not in the source tree.
    with (output / log_name).open("w", encoding="utf8") as log:
        process = subprocess.Popen(command, cwd=output, stdout=log,
                                   stderr=subprocess.STDOUT, env=environment,
                                   start_new_session=(os.name != "nt"))
        try:
            code = process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            # Stop the task-owned process tree, including compiler subprocesses.
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=30, check=False)
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=30)
            raise
    if code:
        raise RuntimeError(f"Command failed with exit code {code}; see {output / log_name}")


def require_pass(result, design):
    marker = f"SMOKE_PASS {design} d=5 cycles="
    if (not any(line.startswith(marker) for line in result.splitlines())
            or "FATAL:" in result.upper() or "ERROR:" in result.upper()):
        raise RuntimeError("Simulation did not report a clean fixture pass")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("design", choices=("2d", "3d"))
    parser.add_argument("--simulator", choices=("xsim", "iverilog"), default="xsim")
    parser.add_argument("--tool-dir", type=Path, help="Simulator bin directory")
    parser.add_argument("--timeout-seconds", type=int, default=600,
                        help="Maximum wall time for each compile/run stage")
    parser.add_argument("--output", type=Path, help="New output directory; never overwrites a run")
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    if args.simulator == "iverilog" and args.design != "2d":
        parser.error("The Icarus path is checked for 2D only; use xsim for 3D")
    names = ("iverilog", "vvp") if args.simulator == "iverilog" else ("xvlog", "xelab", "xsim")
    tools = {name: find_tool(name, args.tool_dir)
             for name in names}
    environment = os.environ.copy()
    # Explicit executable locations can need adjacent runtime DLLs on Windows.
    environment["PATH"] = os.pathsep.join(
        [*(str(Path(path).parent) for path in tools.values()), environment.get("PATH", "")]
    )
    output = (args.output or ROOT / "build" / f"smoke_arty_{args.design}_{time.time_ns()}").resolve()
    output.mkdir(parents=True, exist_ok=False)
    fixture = ROOT / "examples" / f"arty_{args.design}_d5"
    models = ROOT / "models" / args.design
    if args.design == "3d":
        models /= "t32"
    sources = sorted((ROOT / "rtl" / f"arty_{args.design}").glob("*.v"))
    sources.append(fixture / "tb_smoke.sv")
    for source in [*sources, *models.glob("L*.hex"), *fixture.glob("*.hex")]:
        shutil.copyfile(source, output / source.name)
    if args.simulator == "iverilog":
        commands = [
            ("compile.log", [tools["iverilog"], "-g2012", "-s", "tb_smoke",
                             "-o", "smoke.vvp", *[p.name for p in sources]]),
            ("simulate.log", [tools["vvp"], "-n", "smoke.vvp"]),
        ]
    else:
        commands = [
            ("compile.log", [tools["xvlog"], "--sv", *[p.name for p in sources]]),
            ("elaborate.log", [tools["xelab"], "--mt", "2", "--debug", "typical", "-O2",
                               "tb_smoke", "-s", "smoke"]),
            ("simulate.log", [tools["xsim"], "smoke", "-R"]),
        ]
    for log_name, command in commands:
        run(command, output, log_name, args.timeout_seconds, environment)
    result = (output / "simulate.log").read_text(encoding="utf8", errors="replace")
    try:
        require_pass(result, args.design)
    except RuntimeError as error:
        raise RuntimeError(f"{error}; see {output / 'simulate.log'}") from error
    for line in result.splitlines():
        if "SMOKE_PASS" in line:
            print(line)
    print(f"Logs: {output}")


if __name__ == "__main__":
    main()
