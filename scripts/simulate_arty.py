"""Run the small distance-five core checks with Vivado's four-state simulator."""

import argparse
import os
from pathlib import Path
import shutil
import signal
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def find_tool(name, directory):
    suffixes = (".bat", "") if os.name == "nt" else ("",)
    for suffix in suffixes:
        candidate = directory / (name + suffix) if directory else None
        if candidate is not None and candidate.is_file():
            return str(candidate.resolve())
        found = shutil.which(name + suffix) if directory is None else None
        if found:
            return found
    raise FileNotFoundError(f"Cannot find {name}. Set PATH or use --tool-dir.")


def run(command, output, log_name):
    # Logs stay beside the disposable simulation, not in the source tree.
    with (output / log_name).open("w", encoding="utf8") as log:
        process = subprocess.Popen(command, cwd=output, stdout=log,
                                   stderr=subprocess.STDOUT,
                                   start_new_session=(os.name != "nt"))
        try:
            code = process.wait(timeout=600)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            # Vivado's Windows launchers spawn child processes. Stop the whole
            # task-owned tree on timeout rather than leaving a simulator running.
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=30, check=False)
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=30)
            raise
    if code:
        raise RuntimeError(f"Command failed; see {output / log_name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("design", choices=("2d", "3d"))
    parser.add_argument("--tool-dir", type=Path, help="Vivado bin directory")
    parser.add_argument("--output", type=Path, help="New output directory")
    args = parser.parse_args()
    tools = {name: find_tool(name, args.tool_dir)
             for name in ("xvlog", "xelab", "xsim")}
    output = (args.output or ROOT / "build" / f"smoke_arty_{args.design}").resolve()
    output.mkdir(parents=True, exist_ok=False)
    fixture = ROOT / "examples" / f"arty_{args.design}_d5"
    models = ROOT / "models" / args.design
    if args.design == "3d":
        models /= "t32"
    sources = sorted((ROOT / "rtl" / f"arty_{args.design}").glob("*.v"))
    sources.append(fixture / "tb_smoke.sv")
    for source in [*sources, *models.glob("L*.hex"), *fixture.glob("*.hex")]:
        shutil.copyfile(source, output / source.name)
    run([tools["xvlog"], "--sv", *[p.name for p in sources]], output, "compile.log")
    run([tools["xelab"], "--mt", "2", "--debug", "typical", "-O2",
         "tb_smoke", "-s", "smoke"], output, "elaborate.log")
    run([tools["xsim"], "smoke", "-R"], output, "simulate.log")
    result = (output / "simulate.log").read_text(encoding="utf8", errors="replace")
    if "SMOKE_PASS" not in result or "Fatal:" in result or "ERROR:" in result:
        raise RuntimeError(f"Simulation did not pass; see {output / 'simulate.log'}")
    for line in result.splitlines():
        if "SMOKE_PASS" in line:
            print(line)
    print(f"Logs: {output}")


if __name__ == "__main__":
    main()
