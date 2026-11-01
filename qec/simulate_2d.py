"""Compare spatial CNN, HDRG and matching on identical depolarising-error trials."""

from pathlib import Path
import argparse
import hashlib
import json
import time
import numpy as np
import pymatching
import stim
from .spatial import Geometry, FFTReference, summary_flags, exact_interval, mwpm
from .spatial_cuda import TorchFFTReference
from .hdrg_fast import FastSurfaceHDRG
from .io import sha, write, protocol


def paired(a, b):
    w = int(np.sum(a & ~b))
    v = int(np.sum(~a & b))
    wl, wh = exact_interval(w, len(a))
    vl, vh = exact_interval(v, len(a))
    return dict(
        worse_only=w,
        better_only=v,
        difference=(w - v) / len(a),
        lo95=wl - vh,
        hi95=wh - vl,
    )


def flags(g, r):
    return g.outcomes(r)


def logical(g, r):
    return np.column_stack(
        [
            np.bitwise_xor.reduce(r[:, 0, g.lz], axis=1),
            np.bitwise_xor.reduce(r[:, 1, g.lx], axis=1),
        ]
    )


def correlated_model(g, p):
    # Three independent Pauli components at q reproduce the exclusive I/X/Y/Z
    # depolarizing channel exactly: p=3*q*(1-q). This is a model identity, not
    # three independent p/3 errors. Y joins the two CSS components by separator.
    q = (1 - np.sqrt(1 - 4 * p / 3)) / 2
    lines = []
    nz = g.H[0].shape[0]
    lz = set(g.lz)
    lx = set(g.lx)
    for j in range(g.nd):
        x = [f"D{i}" for i in g.H[0][:, j].nonzero()[0]] + (["L0"] if j in lz else [])
        z = [f"D{i+nz}" for i in g.H[1][:, j].nonzero()[0]] + (
            ["L1"] if j in lx else []
        )
        for targets in (x, z, x + ["^"] + z):
            lines.append(f"error({q:.17g}) " + " ".join(targets))
    return stim.DetectorErrorModel("\n".join(lines))


def check_correlated(g, p, m):
    # Confirm all single Pauli faults have the intended detector/observable IDs.
    dm = correlated_model(g, p)
    q = (1 - np.sqrt(1 - 4 * p / 3)) / 2
    assert abs(3 * q * (1 - q) - p) < 1e-14
    r = np.zeros((3 * g.nd, 2, g.nd), np.uint8)
    for j in range(g.nd):
        r[3 * j, 0, j] = 1
        r[3 * j + 1, 1, j] = 1
        r[3 * j + 2, :, j] = 1
    syn = np.concatenate(g.syndromes(r), axis=1)
    pred = m.decode_batch(syn, enable_correlations=True)
    assert np.array_equal(pred, logical(g, r)), "Single Pauli correlation graph check"
    return dict(
        single_pauli_cases=len(r),
        channel_identity_error=abs(3 * q * (1 - q) - p),
        dem_sha256=hashlib.sha256(str(dm).encode()).hexdigest(),
    )


def run(cfg, out):
    tag = f"d{cfg['d']:02d}_p{round(cfg['p']*1e6):06d}"
    out.mkdir(parents=True, exist_ok=True)
    target = out / (tag + ".json")
    data = out / (tag + ".npz")
    if target.exists():
        old = json.loads(target.read_text())
        assert old["configuration"] == cfg and old["data_sha256"] == sha(data)
        return old
    t = time.perf_counter()
    d, n, p = cfg["d"], cfg["n"], cfg["p"]
    g = Geometry(d)
    hd = FastSurfaceHDRG(d)
    match = [
        pymatching.Matching(H, weights=float(np.log((1 - 2 * p / 3) / (2 * p / 3))))
        for H in g.H
    ]
    dem = correlated_model(g, p)
    cm = pymatching.Matching.from_detector_error_model(dem, enable_correlations=True)
    check = check_correlated(g, p, cm)
    rng = np.random.default_rng(
        np.random.SeedSequence([cfg["seed"], d, round(p * 1e6)])
    )
    engines = {
        k: (
            TorchFFTReference(k, g.n)
            if cfg["device"] == "cuda"
            else FFTReference(k, g.n)
        )
        for k in cfg["kinds"]
    }
    arrays = {"pauli": np.empty((n, g.nd), np.uint8)}
    collected = {}
    residuals = {}
    residual_counts = {}
    calibration = {}
    maxpass = cfg["passes"]
    batch = cfg["batch"]

    def keep(name, f):
        for k, v in f.items():
            collected.setdefault(name + "__" + k, []).append(v)

    for lo in range(0, n, batch):
        nb = min(batch, n - lo)
        u = rng.random((nb, g.nd))
        pa = np.zeros((nb, g.nd), np.uint8)
        pa[u < p / 3] = 1
        pa[(u >= p / 3) & (u < 2 * p / 3)] = 2
        pa[(u >= 2 * p / 3) & (u < p)] = 3
        arrays["pauli"][lo : lo + nb] = pa
        orig = np.stack([(pa == 1) | (pa == 2), (pa == 2) | (pa == 3)], axis=1).astype(
            np.uint8
        )
        syn = g.syndromes(orig)
        keep("mwpm", flags(g, mwpm(orig, g, match)))
        cleaned = orig ^ hd.decode_batch(syn)
        f = flags(g, cleaned)
        assert not f["nonclear"].any()
        keep("hdrg", f)
        cp = cm.decode_batch(np.concatenate(syn, axis=1), enable_correlations=True)
        keep("correlated_mwpm", {"fail": np.any(cp != logical(g, orig), axis=1)})
        for kind, e in engines.items():
            cur = orig.copy()
            for it in range(1, maxpass + 1):
                x, sy = g.inputs(cur)
                live = np.any(sy[0], axis=1) | np.any(sy[1], axis=1)
                if live.any():
                    cur[live] ^= (
                        e.forward(x[live], check=lo == 0)[:, :, g.rr, g.cc] > 0
                    ).astype(np.uint8)
                name = f"{kind}_cnn{it}"
                keep(name, flags(g, cur))
                residuals.setdefault(name, []).append(
                    np.packbits(cur, axis=-1, bitorder="little")
                )
                rs = g.syndromes(cur)
                residual_counts.setdefault(name, []).append(sum(s.sum(1) for s in rs))
                calibration[name] = calibration.get(
                    name, np.zeros((2, g.nd), np.int64)
                ) + cur.sum(0, dtype=np.int64)
                keep(name + "_mwpm", flags(g, mwpm(cur, g, match)))
                cleaned = cur ^ hd.decode_batch(rs)
                f = flags(g, cleaned)
                assert not f["nonclear"].any()
                keep(name + "_hdrg", f)
                cp = cm.decode_batch(
                    np.concatenate(rs, axis=1), enable_correlations=True
                )
                keep(
                    name + "_correlated_mwpm",
                    {"fail": np.any(cp != logical(g, cur), axis=1)},
                )
        if lo == 0 or (lo + nb) % 8192 == 0:
            print(
                json.dumps(
                    {
                        "condition": tag,
                        "completed": lo + nb,
                        "total": n,
                        "seconds": round(time.perf_counter() - t, 1),
                    }
                ),
                flush=True,
            )
    for name, parts in collected.items():
        arrays[name] = np.concatenate(parts)
    for name, parts in residuals.items():
        arrays[name + "__residual_packed"] = np.concatenate(parts)
    for name, parts in residual_counts.items():
        arrays[name + "__detector_count"] = np.concatenate(parts)
    for name, value in calibration.items():
        arrays[name + "__error_count"] = value
    methods = sorted(k[:-6] for k in arrays if k.endswith("__fail"))
    result = {
        "configuration": cfg,
        "data_qubits": g.nd,
        "results": {},
        "paired": {},
        "validation": check,
        "inference_guards": {},
        "wall_seconds": time.perf_counter() - t,
    }
    for m in methods:
        result["results"][m] = summary_flags(
            {
                k.split("__")[1]: v
                for k, v in arrays.items()
                if k.startswith(m + "__")
                and k.split("__")[1]
                in ("fail", "nonclear", "logical_x_flip", "logical_z_flip")
            }
        )
        baseline = (
            "correlated_mwpm"
            if m.endswith("_correlated_mwpm")
            else "hdrg" if m.endswith("_hdrg") else "mwpm"
        )
        if m != baseline:
            result["paired"][m] = {
                "baseline": baseline,
                **paired(arrays[m + "__fail"], arrays[baseline + "__fail"]),
            }
    for k, e in engines.items():
        result["inference_guards"][k] = dict(
            max_integer_round_error=e.max_integer_round_error,
            max_direct_error=e.max_direct_error,
            direct_values_checked=e.direct_values_checked,
            smallest_float_logit=e.smallest_float_logit if k == "float" else None,
        )
    np.savez_compressed(data, **arrays)
    result.update(data_file=data.name, data_sha256=sha(data))
    write(target, result)
    print(
        json.dumps(
            {
                "condition": tag,
                "status": "PASS",
                "seconds": round(result["wall_seconds"], 1),
            }
        ),
        flush=True,
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distances", nargs="+", type=int, default=[3, 5])
    parser.add_argument("--probabilities", nargs="+", type=float, default=[0.05, 0.1])
    parser.add_argument("--shots", type=int, default=128)
    parser.add_argument("--passes", type=int, default=1)
    parser.add_argument(
        "--precision", nargs="+", choices=["float", "integer"], default=["integer"]
    )
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if any(d < 3 or d % 2 == 0 for d in args.distances) or any(
        not 0 < p < 0.5 for p in args.probabilities
    ):
        parser.error(
            "Use odd distances >=3 and probabilities strictly between 0 and 0.5."
        )
    if min(args.shots, args.passes, args.batch) < 1 or args.seed < 0:
        parser.error(
            "Shots, passes and batch size must be positive; seed must be nonnegative."
        )
    if args.device == "cuda":
        from .networks import configure

        configure("cuda", 2)
    settings = vars(args).copy()
    settings.pop("output")
    record = protocol(args.output, settings)
    rows = [
        run(
            dict(
                d=d,
                p=p,
                n=args.shots,
                passes=args.passes,
                kinds=args.precision,
                device=args.device,
                batch=args.batch,
                seed=args.seed,
            ),
            args.output,
        )
        for d in args.distances
        for p in args.probabilities
    ]
    write(args.output / "summary.json", dict(protocol=record, conditions=rows))


if __name__ == "__main__":
    main()
