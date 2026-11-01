"""Run small resident (Icarus) or distributed (Verilator) RTL examples."""
from pathlib import Path
import argparse
import csv
import json
import os
import shutil
import signal
import subprocess
import time
import zlib


ROOT = Path(__file__).resolve().parents[1]


def tool(name):
    found = shutil.which(name)
    if found is None:
        raise SystemExit(f"Cannot find {name!r}. Add the executable to PATH or pass its path.")
    return Path(found).resolve()


def run(command, work, name, timeout, environment):
    """Keep logs and terminate the process tree if the time allowance expires."""
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    with (work / f"{name}.log").open("w", encoding="utf8") as log:
        process = subprocess.Popen([str(arg) for arg in command], cwd=work,
                                   env=environment, stdout=log,
                                   stderr=subprocess.STDOUT, **options)
        try:
            code = process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=log, stderr=subprocess.STDOUT, check=False)
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            reason = "was interrupted" if isinstance(error, KeyboardInterrupt) else f"exceeded {timeout} seconds"
            raise SystemExit(f"{name} {reason}; see {work / (name + '.log')}")
    if code:
        raise SystemExit(f"{name} failed with exit code {code}; see {work / (name + '.log')}")
    return (work / f"{name}.log").read_text(encoding="utf8", errors="replace")


def packet_intervals(path):
    """Time the input-to-result boundary from the trace, not the legacy label.

    The testbench's elapsed_ns marker has historical formatting and includes
    reset/drain boundaries. Here the endpoint is first accepted HOST record to
    the last accepted final-layer RETURN, using the explicit picosecond field.
    """
    frames = {}
    previous = -1
    with path.open(newline="", encoding="utf8") as stream:
        for row in csv.DictReader(stream):
            stamp = int(row["time_ps"])
            event = row["event"].strip()  # Verilog pads the shorter HOST label.
            if stamp < previous:
                raise ValueError("Packet trace is not monotone in time")
            previous = stamp
            packet = int(row["packet_hex"], 16)
            body = packet & ((1 << 280) - 1)
            if packet >> 312 != 1 or ((packet >> 280) & 0xffffffff) != zlib.crc32(body.to_bytes(35, "little")):
                raise ValueError("Invalid packet version or CRC in trace")
            frame = frames.setdefault(int(row["frame"]), {"first": None, "last": None, "final_sites": set()})
            if event == "HOST" and frame["first"] is None:
                frame["first"] = stamp
            if event == "RETURN" and ((packet >> 232) & 3) == 3:
                coordinate = ((packet >> 192) & 65535, (packet >> 208) & 65535)
                if coordinate in frame["final_sites"]:
                    raise ValueError("Duplicate final coordinate")
                frame["final_sites"].add(coordinate)
                frame["last"] = stamp
    if set(frames) != set(range(6)):
        raise ValueError("Expected six completed input frames")
    intervals = []
    for frame in frames.values():
        if frame["final_sites"] != {(x, y) for y in range(5) for x in range(5)}:
            raise ValueError("Incomplete final map")
        if frame["first"] is None or frame["last"] is None:
            raise ValueError("Missing timing endpoint")
        intervals.append((frame["last"] - frame["first"]) / 1_000_000)
    return intervals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("design", choices=("resident", "distributed"), nargs="?", default="resident")
    parser.add_argument("--policy", choices=("primary", "fixed32"), default="primary",
                        help="Distributed port service: independent ports or fixed aggregate capacity")
    parser.add_argument("--iverilog", default="iverilog")
    parser.add_argument("--vvp", default="vvp")
    parser.add_argument("--cmake", default="cmake")
    parser.add_argument("--verilator-root", type=Path,
                        help="Verilator installation containing verilator-config.cmake")
    parser.add_argument("--timeout-seconds", type=int, default=900,
                        help="Maximum wall time for each compile/run stage")
    parser.add_argument("--output", type=Path, help="New output directory; never overwrites an existing run")
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    if args.design == "resident" and args.policy != "primary":
        parser.error("--policy applies only to the distributed example")
    example = ROOT / "examples" / f"{args.design}_2d_small"
    work = (args.output or ROOT / "build" / f"{args.design}_{time.time_ns()}").resolve()
    work.mkdir(parents=True, exist_ok=False)
    for path in example.glob("*.hex"):
        shutil.copyfile(path, work / path.name)
    environment = os.environ.copy()
    print(f"Compiling {args.design}; logs and outputs: {work}", flush=True)
    if args.design == "resident":
        compiler, runtime = tool(args.iverilog), tool(args.vvp)
        sources = sorted((ROOT / "rtl/resident_2d").glob("*.sv"))
        sources += sorted((ROOT / "rtl/hardwired_2d").glob("*.v"))
        sources.append(example / "tb.sv")
        # Explicit executable paths may need adjacent runtime DLLs on Windows.
        environment["PATH"] = os.pathsep.join([str(compiler.parent), str(runtime.parent), environment.get("PATH", "")])
        run([compiler, "-g2012", "-s", "tb", "-o", work / "simulation.vvp", *sources],
            work, "compile", args.timeout_seconds, environment)
        command = [runtime, work / "simulation.vvp"]
    else:
        cmake = tool(args.cmake)
        if args.verilator_root:
            environment["VERILATOR_ROOT"] = str(args.verilator_root.resolve())
        build = work / "compiled"
        run([cmake, "-S", example, "-B", build, "-DCMAKE_BUILD_TYPE=Release"],
            work, "configure", args.timeout_seconds, environment)
        run([cmake, "--build", build, "--config", "Release", "--parallel", "2"],
            work, "compile", args.timeout_seconds, environment)
        candidates = [build / "simulation", build / "simulation.exe", build / "Release/simulation.exe"]
        executable = next((path for path in candidates if path.is_file()), None)
        if executable is None:
            raise SystemExit(f"Cannot find the compiled simulation under {build}")
        command = [executable]
    arguments = [] if args.design == "resident" else ["+SCENARIO=0", "+FABRIC_NS=10", "+CORE_NS=10",
                                                      f"+SERVICE_DIV={4 if args.policy == 'fixed32' else 1}"]
    print("Running six test patterns...", flush=True)
    log = run([*command, *arguments], work, "simulate",
              args.timeout_seconds, environment)
    marker = "ALL_PASS" if args.design == "resident" else "SUITE_PASS frames=6 scenario=0 abort=0"
    frame_marker = "FRAME_PASS " if args.design == "resident" else "MULTIBOARD_PASS "
    if marker not in log or log.count(frame_marker) != 6 or "FATAL:" in log:
        raise SystemExit(f"The expected six-frame completion was not found; see {work / 'simulate.log'}")
    result = {"status": "PASS", "design": args.design, "frames": 6, "scope": "Small RTL implementation check; not physical board or decoder-quality validation"}
    if args.design == "distributed":
        result.update(policy=args.policy, packet_completion_us=packet_intervals(work / "packets.csv"))
    (work / "summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
