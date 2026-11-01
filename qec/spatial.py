"""Spatial surface-code geometry and numerical CNN reference.

No cloud service, notebook execution, training, or hardware programming.
Float parameters and integer HEX exports are included under models/2d.
"""

from __future__ import annotations
import hashlib
import json
from pathlib import Path

import numpy as np
import pymatching
from scipy.fft import rfft2, irfft2, next_fast_len
from scipy.sparse import csc_matrix
from scipy.stats import beta

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
MODEL = ROOT / "models/2d/float_parameters.npz"
HEX = ROOT / "models/2d"
SHAPES = [(4, 9), (9, 9), (9, 2)]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_weights(kind):
    weights, biases = [], []
    if kind == "float":
        with np.load(MODEL, allow_pickle=False) as archive:
            for layer in range(3):
                weights.append(archive[f"weight_{layer}"].astype(np.float64))
                biases.append(archive[f"bias_{layer}"].astype(np.float64))
    elif kind == "integer":
        for layer, (ci, co) in enumerate(SHAPES):
            lines = (HEX / f"L{layer}_weights_par.hex").read_text().split()
            assert len(lines) == 121
            w = np.empty((co, ci, 11, 11), dtype=np.int64)
            for k, line in enumerate(lines):
                word = int(line, 16)
                for o in range(co):
                    for i in range(ci):
                        v = (word >> (8 * (o * ci + i))) & 255
                        w[o, i, k // 11, k % 11] = v - 256 if v >= 128 else v
            b = [int(v, 16) for v in (HEX / f"L{layer}_bias.hex").read_text().split()]
            b = np.array([v - (1 << 48) if v >= (1 << 47) else v for v in b], np.int64)
            assert len(b) == co
            weights.append(w)
            biases.append(b)
    else:
        raise ValueError(kind)
    return weights, biases


class Geometry:
    """Unrotated patch; vectors are data-qubit raster order, not all map sites."""

    def __init__(self, d):
        self.d, self.n = d, 2 * d - 1
        self.sites = [
            (r, c) for r in range(self.n) for c in range(self.n) if r % 2 == c % 2
        ]
        self.nd = len(self.sites)
        self.rr, self.cc = np.array(self.sites).T
        idx = {s: i for i, s in enumerate(self.sites)}
        self.zchecks = [
            (r, c) for r in range(0, self.n, 2) for c in range(1, self.n, 2)
        ]
        self.xchecks = [
            (r, c) for r in range(1, self.n, 2) for c in range(0, self.n, 2)
        ]
        self.supports, self.H = [], []
        for checks in [self.zchecks, self.xchecks]:
            supports = np.full((len(checks), 4), self.nd, dtype=int)
            row, col = [], []
            for j, (r, c) in enumerate(checks):
                for k, site in enumerate(
                    [(r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)]
                ):
                    if site in idx:
                        supports[j, k] = idx[site]
                        row.append(j)
                        col.append(idx[site])
            self.supports.append(supports)
            self.H.append(
                csc_matrix(
                    (np.ones(len(row), np.uint8), (row, col)),
                    shape=(len(checks), self.nd),
                )
            )
        self.lz = np.array([idx[(r, 0)] for r in range(0, self.n, 2)])
        self.lx = np.array([idx[(0, c)] for c in range(0, self.n, 2)])
        self.boundary = np.zeros((2, self.n, self.n), np.float64)
        self.boundary[0, 0, ::2] = self.boundary[0, -1, ::2] = 1
        self.boundary[1, ::2, 0] = self.boundary[1, ::2, -1] = 1
        assert self.nd == d * d + (d - 1) * (d - 1)

    def syndromes(self, residual):
        padded = np.pad(residual, ((0, 0), (0, 0), (0, 1)))
        return [
            np.bitwise_xor.reduce(padded[:, axis, support], axis=-1)
            for axis, support in enumerate(self.supports)
        ]

    def inputs(self, residual):
        sz, sx = self.syndromes(residual)
        board = np.zeros((len(residual), 4, self.n, self.n), np.float64)
        xr, xc = np.array(self.xchecks).T
        zr, zc = np.array(self.zchecks).T
        board[:, 0, xr, xc] = sx
        board[:, 1, zr, zc] = sz
        board[:, 2:] = self.boundary
        return board, (sz, sx)

    def outcomes(self, residual):
        sz, sx = self.syndromes(residual)
        nonclear = np.any(sz, axis=1) | np.any(sx, axis=1)
        lz = np.bitwise_xor.reduce(residual[:, 0, self.lz], axis=1).astype(bool)
        lx = np.bitwise_xor.reduce(residual[:, 1, self.lx], axis=1).astype(bool)
        return {
            "fail": nonclear | lz | lx,
            "nonclear": nonclear,
            "logical_z_flip": lz,
            "logical_x_flip": lx,
        }


class FFTReference:
    """Linear cross-correlation in float64; integer layers round before bias.

    Integer reconstruction is guarded by <1e-3 distance from an integer and
    direct int64 convolution checks. This is CPU reference work, not FPGA time.
    """

    def __init__(self, kind, n, workers=2):
        self.kind, self.workers = kind, workers
        self.weights, self.biases = load_weights(kind)
        self.n = n
        self.f = next_fast_len(n + 10)
        self.spectra = [
            rfft2(
                w[:, :, ::-1, ::-1].astype(np.float64),
                s=(self.f, self.f),
                workers=workers,
            )
            for w in self.weights
        ]
        self.max_integer_round_error = 0.0
        self.max_direct_error = 0.0
        self.direct_values_checked = 0
        self.smallest_float_logit = float("inf")

    def forward(self, x, check=False, return_layers=False):
        stages = []
        for layer, (w, b, wf) in enumerate(
            zip(self.weights, self.biases, self.spectra)
        ):
            xf = rfft2(x.astype(np.float64), s=(self.f, self.f), workers=self.workers)
            yf = np.einsum("nihw,oihw->nohw", xf, wf, optimize=True)
            raw = irfft2(yf, s=(self.f, self.f), workers=self.workers)[
                :, :, 5 : 5 + self.n, 5 : 5 + self.n
            ]
            if self.kind == "integer":
                rounded = np.rint(raw)
                error = float(np.max(np.abs(raw - rounded)))
                self.max_integer_round_error = max(error, self.max_integer_round_error)
                if error >= 1e-3:
                    raise ArithmeticError(
                        f"Integer FFT reconstruction not sufficiently close: {error}"
                    )
                y = rounded.astype(np.int64) + b[None, :, None, None]
            else:
                y = raw + b[None, :, None, None]
            if check:
                xp = np.pad(x, ((0, 0), (0, 0), (5, 5), (5, 5)))
                points = [
                    (0, 0),
                    (0, self.n - 1),
                    (self.n // 2, self.n // 2),
                    (self.n - 1, 0),
                    (self.n - 1, self.n - 1),
                ]
                for shot in sorted(set([0, len(x) - 1])):
                    for r, c in points:
                        block = xp[shot, :, r : r + 11, c : c + 11]
                        if self.kind == "integer":
                            exact = (
                                np.sum(
                                    block.astype(np.int64)[None] * w,
                                    axis=(1, 2, 3),
                                    dtype=np.int64,
                                )
                                + b
                            )
                            assert np.array_equal(exact, y[shot, :, r, c]), (
                                layer,
                                shot,
                                r,
                                c,
                            )
                        else:
                            exact = np.sum(block[None] * w, axis=(1, 2, 3)) + b
                            error = float(np.max(np.abs(exact - y[shot, :, r, c])))
                            self.max_direct_error = max(error, self.max_direct_error)
                            assert np.allclose(
                                exact, y[shot, :, r, c], atol=1e-9, rtol=1e-10
                            )
                        self.direct_values_checked += len(b)
            if layer < 2:
                y = np.maximum(y, 0)
            x = y
            if return_layers:
                stages.append(x.copy())
        if self.kind == "float":
            self.smallest_float_logit = min(
                self.smallest_float_logit, float(np.min(np.abs(x)))
            )
        return (x, stages) if return_layers else x


def mwpm(residual, geo, matchers):
    syn = geo.syndromes(residual)
    corrections = np.stack(
        [m.decode_batch(np.ascontiguousarray(s)) for m, s in zip(matchers, syn)], axis=1
    ).astype(np.uint8)
    corrected = residual ^ corrections
    assert not geo.outcomes(corrected)["nonclear"].any(), "MWPM failed to clear checks"
    return corrected


def wilson(k, n, z=1.959963984540054):
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return 0.0 if k == 0 else max(0.0, mid - half), (
        1.0 if k == n else min(1.0, mid + half)
    )


def summary_flags(flags):
    result = {}
    for name, flag in flags.items():
        n, k = len(flag), int(np.sum(flag))
        lo, hi = wilson(k, n)
        result[name] = {"count": k, "n": n, "rate": k / n, "lo95": lo, "hi95": hi}
    return result


def exact_interval(k, n, confidence=0.975):
    """Equal-tailed Clopper-Pearson binomial interval, including 0/n endpoints."""
    tail = (1 - confidence) / 2
    lo = 0.0 if k == 0 else float(beta.ppf(tail, k, n - k + 1))
    hi = 1.0 if k == n else float(beta.ppf(1 - tail, k + 1, n - k))
    return lo, hi
