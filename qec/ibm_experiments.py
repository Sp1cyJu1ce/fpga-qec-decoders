"""Build IBM experiments locally, or explicitly submit/retrieve a QPU job."""

from pathlib import Path
import argparse
import json
import os
import numpy as np
from qiskit import qpy
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
from .ibm_device import (
    qubit_stats,
    twoq_edge_errors,
    select_idle_qubits,
    select_memory_patch,
)
from .ibm_idle import IdleExperiment
from . import ibm_memory
from .io import write


def runtime_service():
    """Read credentials only when an explicit online command needs them."""
    from qiskit_ibm_runtime import QiskitRuntimeService

    token = os.environ.get("QISKIT_IBM_TOKEN")
    if not token:
        raise RuntimeError(
            "Set QISKIT_IBM_TOKEN in your local environment before an online command."
        )
    return QiskitRuntimeService(
        channel="ibm_quantum_platform",
        token=token,
        instance=os.environ.get("QISKIT_IBM_INSTANCE") or None,
    )


def unpack(bit_array):
    """Column k is classical bit k; Qiskit strings list the highest bit first."""
    return np.asarray(
        [[int(bit) for bit in value[::-1]] for value in bit_array.get_bitstrings()],
        dtype=np.uint8,
    )


def collect_result(result, manifest, output):
    if len(result) != 1:
        raise ValueError("This command expects one circuit result per job.")
    data = result[0].data
    basis = manifest["configuration"]["basis"]
    if manifest["experiment"] == "memory":
        arrays = {"raw": unpack(data.creg)}
    elif basis == "BELL":
        arrays = {"mc": unpack(data.mc), "mt": unpack(data.mt)}
    else:
        arrays = {"m": unpack(data.m)}
    if any(len(value) != manifest["shots"] for value in arrays.values()):
        raise ValueError("Returned shot count differs from the submitted count.")
    np.savez_compressed(output / "measurements.npz", **arrays)
    manifest["state"] = "complete"
    write(output / "experiment.json", manifest)


def preview_backend():
    """A nominal grid for local circuit checks, not a measured IBM device."""
    from qiskit.providers.fake_provider import GenericBackendV2

    edges = []
    for r in range(12):
        for c in range(10):
            q = 10 * r + c
            if c < 9:
                edges.extend([(q, q + 1), (q + 1, q)])
            if r < 11:
                edges.extend([(q, q + 10), (q + 10, q)])
    return GenericBackendV2(
        120,
        basis_gates=["id", "rz", "sx", "x", "cx"],
        coupling_map=edges,
        dt=4e-9,
        seed=2026,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment", choices=["idle", "memory"])
    parser.add_argument("--distance", type=int, default=5)
    parser.add_argument("--basis", choices=["X", "Z", "BELL"], default="Z")
    parser.add_argument(
        "--prep",
        choices=["zeros", "ones", "plus", "minus", "random", "bell"],
        default="zeros",
    )
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--delay-us", type=float, default=0)
    parser.add_argument("--xy4", action="store_true")
    parser.add_argument(
        "--placement", choices=["geometric", "calibration"], default="geometric"
    )
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--shots", type=int, default=4096)
    parser.add_argument("--backend", default="ibm_miami")
    online = parser.add_mutually_exclusive_group()
    online.add_argument(
        "--submit", action="store_true", help="Submit one circuit to the selected QPU."
    )
    online.add_argument(
        "--retrieve",
        action="store_true",
        help="Retrieve the job recorded in the output folder.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output
    if args.retrieve:
        manifest = json.loads((output / "experiment.json").read_text(encoding="utf8"))
        if manifest.get("preview") or not manifest.get("job_id"):
            parser.error("This folder does not contain a submitted QPU job.")
        job = runtime_service().job(manifest["job_id"])
        collect_result(job.result(), manifest, output)
        print(
            "Measurements saved locally. No credentials are written to the result files."
        )
        return
    if output.exists() and any(output.iterdir()):
        parser.error(
            "Use an empty output folder so an existing experiment is not overwritten."
        )
    if (
        args.distance < 2
        or args.shots < 1
        or args.seed < 0
        or not np.isfinite(args.delay_us)
        or args.delay_us < 0
    ):
        parser.error("Invalid distance, shot count, seed or delay.")
    if args.experiment == "memory" and (
        args.distance < 3
        or args.distance % 2 == 0
        or args.rounds < 1
        or args.basis == "BELL"
    ):
        parser.error(
            "Repeated memory checks require odd distance >=3, positive rounds, and X or Z basis."
        )
    if args.experiment == "idle" and args.prep == "minus":
        parser.error("Use basis X with prep ones for an idle minus-state preparation.")
    if (args.basis == "BELL") != (args.prep == "bell"):
        parser.error("Bell basis and Bell preparation must be selected together.")
    if args.experiment == "memory" and (
        args.xy4 or args.delay_us or args.placement != "geometric"
    ):
        parser.error(
            "XY4, delay and calibration-only placement belong to the idle experiment."
        )

    service = runtime_service() if args.submit else None
    backend = service.backend(args.backend) if service else preview_backend()
    t1, t2, readout = qubit_stats(backend)
    edge_errors = twoq_edge_errors(backend)
    medians = {
        "t1": float(np.nanmedian(t1)),
        "t2": float(np.nanmedian(t2)),
        "ro": float(np.nanmedian(readout)),
        "e2q": float(np.median(list(edge_errors.values()))) if edge_errors else 0.0,
    }
    config = {
        "d": args.distance,
        "basis": args.basis,
        "prep": args.prep,
        "tag": f"{args.experiment}_d{args.distance}_{args.basis}_{args.prep}",
    }
    if args.experiment == "idle":
        data, partners = select_idle_qubits(
            backend, args.distance, args.placement == "geometric", args.basis == "BELL"
        )
        # Larger calibration-selected patches use their own distance-dependent
        # preparation seed, matching the original experiment definition.
        seed = (
            args.seed + args.distance if args.placement == "calibration" else args.seed
        )
        experiment = IdleExperiment(args.distance, data, partners, seed)
        config.update(delay_us=args.delay_us, dd=args.xy4, placement=args.placement)
        circuit = experiment.build(config, backend)
        layout = {"data_qubits": data, "partner_qubits": partners}
    else:
        mapping = select_memory_patch(backend, args.distance)
        config["rounds"] = args.rounds
        ibm_memory.PREP_BITS.clear()
        ibm_memory.SEED = args.seed
        circuit = ibm_memory.build_circuit(config, backend.num_qubits, mapping)
        layout = {
            "sites": [
                {"site": list(site), "qubit": int(q)} for site, q in mapping.items()
            ]
        }

    output.mkdir(parents=True, exist_ok=True)
    with (output / "circuit.qpy").open("wb") as handle:
        qpy.dump(circuit, handle)
    manifest = {
        "experiment": args.experiment,
        "configuration": config,
        "shots": args.shots,
        "backend": args.backend if args.submit else "nominal_rectangular_preview",
        "preview": not args.submit,
        "layout": layout,
        "medians": medians,
        "seed": args.seed,
        "state": "built",
    }
    write(output / "experiment.json", manifest)
    if not args.submit:
        print(
            "Circuit built locally; no IBM account was opened and no QPU job was submitted."
        )
        return
    from qiskit_ibm_runtime import SamplerV2

    manager = generate_preset_pass_manager(
        optimization_level=1,
        backend=backend,
        initial_layout=list(range(backend.num_qubits)),
    )
    isa = manager.run(circuit)
    with (output / "submitted_circuit.qpy").open("wb") as handle:
        qpy.dump(isa, handle)
    sampler = SamplerV2(mode=backend)
    sampler.options.dynamical_decoupling.enable = False
    job = sampler.run([isa], shots=args.shots)
    # Keep job identity locally for retrieval. The repository excludes this file.
    manifest.update(state="submitted", job_id=job.job_id())
    write(output / "experiment.json", manifest)
    print(
        "Job submitted. Run the command with --retrieve and the same output folder to collect it."
    )


if __name__ == "__main__":
    main()
