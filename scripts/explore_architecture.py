"""Explore conditional FPGA resource and timing limits for one or more cases."""

from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qec.architecture import BUDGETS, DEVICE_DATA, GRIDS, explore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=("2D", "3D"), default="2D")
    parser.add_argument("--distances", type=int, nargs="+", default=[9])
    parser.add_argument("--boards", type=int, nargs="+", default=[1])
    parser.add_argument("--device", choices=tuple(DEVICE_DATA), default="VP1902")
    parser.add_argument("--budget", choices=tuple(BUDGETS), default="nominal")
    parser.add_argument("--clock-mhz", type=float, default=250)
    parser.add_argument(
        "--link-gbps",
        type=float,
        default=400,
        help="Bandwidth of each directed cardinal link",
    )
    parser.add_argument(
        "--mesh-budget-gbps",
        type=float,
        help="Optional total bandwidth shared by all directed mesh links",
    )
    parser.add_argument("--hop-ns", type=float, default=50)
    parser.add_argument(
        "--stream-rounds",
        type=int,
        default=0,
        help="3D temporal occupancy check; zero disables it",
    )
    parser.add_argument("--arrival-us", type=float, default=1.1)
    parser.add_argument(
        "--full-grid",
        action="store_true",
        help="Use the thesis distance grid and 1, 4, 16, 64 boards for this device",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("results/architecture.json")
    )
    args = parser.parse_args()
    distances = GRIDS[args.model] if args.full_grid else args.distances
    boards = [1, 4, 16, 64] if args.full_grid else args.boards
    results = []
    print(
        "Conditional architecture model: these are not measured FPGA timings.",
        flush=True,
    )
    for distance in distances:
        for board_count in boards:
            map_side = 2 * distance - 1 if args.model == "2D" else (distance + 3) // 2
            if args.full_grid and board_count > map_side * map_side:
                print(
                    f"Skipping d={distance}, {board_count} boards: empty tiles",
                    flush=True,
                )
                continue
            try:
                result = explore(
                    model=args.model,
                    distance=distance,
                    device=args.device,
                    boards=board_count,
                    resource_budget=args.budget,
                    clock_mhz=args.clock_mhz,
                    link_gbps=args.link_gbps,
                    hop_ns=args.hop_ns,
                    mesh_budget_gbps=args.mesh_budget_gbps,
                    stream_rounds=args.stream_rounds,
                    arrival_us=args.arrival_us,
                )
            except ValueError as error:
                parser.error(str(error))
            results.append(result)
            label = f"{args.model} d={distance}, {board_count} x {args.device}"
            if result["winners"]:
                finite = result["winners"]["finite"]["schedule"]["full_pass_us"]
                service = result["winners"]["service"]["schedule"]["steady_ii_us"]
                print(
                    f"{label}: finite {finite:.3f} us; best service {service:.3f} us",
                    flush=True,
                )
            else:
                print(f"{label}: no enumerated allocation fits", flush=True)
            # Save completed cases so an interrupted sweep still leaves useful results.
            args.output.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.output.with_suffix(args.output.suffix + ".tmp")
            temporary.write_text(
                json.dumps(results, indent=2) + "\n", encoding="utf-8"
            )
            temporary.replace(args.output)
    print(f"Saved {len(results)} case(s) to {args.output}")


if __name__ == "__main__":
    main()
