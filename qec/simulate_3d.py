"""Compare space-time decoders on shared finite-memory circuit trials."""

from pathlib import Path
import argparse
import gc
import json
import time
import numpy as np
import pymatching
import stim
import torch
from scipy.stats import beta
from .memory import Geometry3D, memory_circuit, space_time_syndrome
from .networks import configure, FloatCNN, IntegerCNN
from .io import sha, write, protocol


def rate(a):
    n = len(a)
    k = int(np.sum(a))
    z = 1.959963984540054
    den = 1 + z * z / n
    mid = (k / n + z * z / (2 * n)) / den
    half = z * np.sqrt(k / n * (1 - k / n) / n + z * z / (4 * n * n)) / den
    return dict(
        count=k, n=n, rate=k / n, lo95=max(0, mid - half), hi95=min(1, mid + half)
    )


def paired(a, b):
    n = len(a)
    w = int(np.sum(a & ~b))
    v = int(np.sum(~a & b))
    ci = lambda k: (
        0 if k == 0 else float(beta.ppf(0.0125, k, n - k + 1)),
        1 if k == n else float(beta.ppf(0.9875, k + 1, n - k)),
    )
    wl, wh = ci(w)
    vl, vh = ci(v)
    return dict(
        worse_only=w, better_only=v, difference=(w - v) / n, lo95=wl - vh, hi95=wh - vl
    )


def circuit_for(cfg):
    d, R, b, p = cfg["d"], cfg["rounds"], cfg["basis"], cfg["pprime"]
    c, slots = memory_circuit(d, R, b, p)
    if cfg.get("noise", "full") == "full":
        return c, 0
    assert cfg["noise"] == "remove_four_data_idles"
    # Controlled ablation only. Keep gates, detectors, readout, reset and CX-slot
    # idles fixed; remove precisely four full-data idle channels per round.
    out = stim.Circuit()
    ticks = 0
    started = False
    removed = 0
    dq = list(range(d * d))
    for ins in c:
        ts = ins.targets_copy()
        if ins.name == "R" and ts and ts[0].value >= d * d:
            started = True
        if (
            ins.name == "DEPOLARIZE1"
            and started
            and ticks < 8 * R
            and ticks % 8 in (0, 1, 6, 7)
        ):
            # Stim merges adjacent same-parameter channels, e.g. X-ancilla H noise
            # and data idle noise. Remove only the data targets from a merged gate.
            data_targets = [
                t.value for t in ts if t.is_qubit_target and t.value < d * d
            ]
            if data_targets:
                assert sorted(data_targets) == dq
                kept = [t for t in ts if not (t.is_qubit_target and t.value < d * d)]
                if kept:
                    out.append(ins.name, kept, ins.gate_args_copy())
                removed += 1
                continue
        out.append(ins)
        if ins.name == "TICK":
            ticks += 1
    assert removed == 4 * R and ticks == 8 * R
    assert (
        out.num_detectors == c.num_detectors
        and out.num_observables == c.num_observables
    )
    return out, removed


def run(cfg, out, engines):
    name = f"{cfg['noise']}_d{cfg['d']:02d}_{cfg['basis']}_r{cfg['rounds']}_p{round(cfg['pprime']*1e6):06d}"
    out.mkdir(parents=True, exist_ok=True)
    target = out / (name + ".json")
    data = out / (name + ".npz")
    if target.exists():
        row = json.loads(target.read_text())
        assert row["configuration"] == cfg and row["data_sha256"] == sha(data)
        return row
    start = time.perf_counter()
    d, n = cfg["d"], cfg["n"]
    g = Geometry3D(d, cfg["rounds"], cfg["basis"])
    c, removed = circuit_for(cfg)
    dem = c.detector_error_model(decompose_errors=True)
    weighted = pymatching.Matching.from_detector_error_model(dem)
    correlated = pymatching.Matching.from_detector_error_model(
        dem, enable_correlations=True
    )
    seed = int(
        np.random.SeedSequence(
            [cfg["seed"], d, ord(cfg["basis"]), round(cfg["pprime"] * 1e6)]
        ).generate_state(1, dtype=np.uint64)[0]
    )
    sampler = c.compile_detector_sampler(seed=seed)
    (out / (name + ".stim")).write_text(str(c))
    (out / (name + ".dem")).write_text(str(dem))
    saved = {}
    parts = {}
    preserve = {}

    def add(name, v):
        parts.setdefault(name, []).append(v)

    def record(name, v):
        preserve.setdefault(name, []).append(v)

    for lo in range(0, n, cfg["batch"]):
        nb = min(cfg["batch"], n - lo)
        det, ob = sampler.sample(nb, separate_observables=True)
        det = det.astype(np.uint8)
        truth = ob[:, 0].astype(np.uint8)
        xs = g.volume(det)
        syn = g.measured_detectors(xs[:, 8:])
        record("detectors_packed", np.packbits(det, axis=1, bitorder="little"))
        record("observed_logical", truth)
        for key in ["hdrg", "mwpm_pcm"]:
            add(key + "__failure", g.global_decode(syn, key) != truth)
        add("mwpm_dem__failure", weighted.decode_batch(det)[:, 0] != truth)
        add(
            "correlated_dem__failure",
            correlated.decode_batch(det, enable_correlations=True)[:, 0] != truth,
        )
        for kind in cfg["kinds"]:
            e = engines[kind]
            cur = xs.copy()
            total = np.zeros_like(xs)
            for it in range(1, cfg["passes"] + 1):
                active = np.any(cur[:, 8:], axis=(1, 2, 3, 4))
                if active.any():
                    edges = e.predict(cur[active]) & g.edge_mask[None]
                    total[active] ^= edges
                    cur[active, 8:] = (
                        xs[active, 8:] ^ space_time_syndrome(total[active], g.pres)
                    ) & g.available[None]
                name0 = f"{kind}_cnn{it}"
                res = g.full_detectors(cur[:, 8:])
                rsyn = g.measured_detectors(cur[:, 8:])
                parity = g.edge_logical(total)
                add(name0 + "__failure", parity != truth)
                add(
                    name0 + "__strict_failure", (parity != truth) | np.any(rsyn, axis=1)
                )
                record(
                    name0 + "__residual_packed",
                    np.packbits(res, axis=1, bitorder="little"),
                )
                record(name0 + "__correction_parity", parity)
                record(name0 + "__residual_count", res.sum(1).astype(np.uint32))
                for key in ["hdrg", "mwpm_pcm"]:
                    add(
                        name0 + "_" + key + "__failure",
                        (parity ^ g.global_decode(rsyn, key)) != truth,
                    )
                add(
                    name0 + "_mwpm_dem__failure",
                    (parity ^ weighted.decode_batch(res)[:, 0]) != truth,
                )
                add(
                    name0 + "_correlated_dem__failure",
                    (
                        parity
                        ^ correlated.decode_batch(res, enable_correlations=True)[:, 0]
                    )
                    != truth,
                )
        if lo == 0 or (lo + nb) % 256 == 0:
            print(
                json.dumps(
                    {
                        "condition": name,
                        "completed": lo + nb,
                        "total": n,
                        "seconds": round(time.perf_counter() - start, 1),
                    }
                ),
                flush=True,
            )
    for k, v in {**parts, **preserve}.items():
        saved[k] = np.concatenate(v)
    results = {k[:-9]: rate(v) for k, v in saved.items() if k.endswith("__failure")}
    pairs = {}
    for m in results:
        base = (
            "correlated_dem"
            if m.endswith("_correlated_dem")
            else (
                "mwpm_dem"
                if m.endswith("_mwpm_dem")
                else "hdrg" if m.endswith("_hdrg") else "mwpm_pcm"
            )
        )
        if m != base:
            pairs[m] = {
                "baseline": base,
                **paired(saved[m + "__failure"], saved[base + "__failure"]),
            }
    np.savez_compressed(data, **saved)
    row = dict(
        configuration=cfg,
        stim_seed=seed,
        results=results,
        paired=pairs,
        detector_count=c.num_detectors,
        removed_data_idle_channels=removed,
        circuit_file=name + ".stim",
        circuit_sha256=sha(out / (name + ".stim")),
        dem_file=name + ".dem",
        dem_sha256=sha(out / (name + ".dem")),
        data_file=data.name,
        data_sha256=sha(data),
        wall_seconds=time.perf_counter() - start,
    )
    write(target, row)
    g.spatial_path.cache_clear()
    del g
    gc.collect()
    torch.cuda.empty_cache() if torch.cuda.is_available() else None
    print(
        json.dumps(
            {
                "condition": name,
                "status": "PASS",
                "seconds": round(row["wall_seconds"], 1),
            }
        ),
        flush=True,
    )
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distances", nargs="+", type=int, default=[3])
    parser.add_argument("--rounds", type=int, help="Defaults to the code distance.")
    parser.add_argument("--basis", nargs="+", choices=["X", "Z"], default=["X", "Z"])
    parser.add_argument("--probabilities", nargs="+", type=float, default=[0.005])
    parser.add_argument("--shots", type=int, default=64)
    parser.add_argument("--passes", type=int, default=1)
    parser.add_argument(
        "--precision", nargs="+", choices=["float", "integer"], default=["integer"]
    )
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument(
        "--noise", choices=["full", "remove_four_data_idles"], default="full"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if any(d < 3 or d % 2 == 0 for d in args.distances) or any(
        not 0 < p < 0.5 for p in args.probabilities
    ):
        parser.error(
            "Use odd distances >=3 and probabilities strictly between 0 and 0.5."
        )
    if (
        min(args.shots, args.passes, args.batch, args.threads) < 1
        or (args.rounds is not None and args.rounds < 1)
        or args.seed < 0
    ):
        parser.error("Counts must be positive and the seed nonnegative.")
    configure(args.device, args.threads)
    settings = vars(args).copy()
    settings.pop("output")
    record = protocol(args.output, settings)
    engines = {
        k: (FloatCNN(args.device) if k == "float" else IntegerCNN(args.device))
        for k in args.precision
    }
    rows = [
        run(
            dict(
                d=d,
                rounds=d if args.rounds is None else args.rounds,
                basis=b,
                pprime=p,
                n=args.shots,
                passes=args.passes,
                kinds=args.precision,
                noise=args.noise,
                batch=args.batch,
                seed=args.seed,
            ),
            args.output,
            engines,
        )
        for d in args.distances
        for b in args.basis
        for p in args.probabilities
    ]
    write(args.output / "summary.json", dict(protocol=record, conditions=rows))


if __name__ == "__main__":
    main()
