"""Float64 CUDA FFT reference for the float and integer 2D CNNs.

This is local GPU simulation, not FPGA execution. Integer outputs are rounded
at each convolution before bias/ReLU with the same guard and direct checks as
the CPU reference. Float64 evaluation of float32 coefficients remains
distinct from native-float32 convolution. Validate on this runtime/device
before using the backend in production.
"""

from __future__ import annotations
import numpy as np
from scipy.fft import next_fast_len
from .spatial import load_weights


class TorchFFTReference:
    def __init__(self, kind, n, workers=2):
        import torch

        if kind not in ("float", "integer"):
            raise ValueError("Unknown representation")
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA backend unavailable")
        self.torch = torch
        self.kind = kind
        self.n = int(n)
        self.device = "cuda"
        self.weights, self.biases = load_weights(kind)
        self.f = next_fast_len(n + 10)
        self.spectra = [
            torch.fft.rfft2(
                torch.tensor(
                    w[:, :, ::-1, ::-1].copy(), dtype=torch.float64, device=self.device
                ),
                s=(self.f, self.f),
            )
            for w in self.weights
        ]
        self.gpu_biases = [
            torch.tensor(b, dtype=torch.float64, device=self.device)[
                None, :, None, None
            ]
            for b in self.biases
        ]
        self.max_integer_round_error = 0.0
        self.max_direct_error = 0.0
        self.direct_values_checked = 0
        self.smallest_float_logit = float("inf")
        self.max_absolute_integer = 0.0

    def forward(self, x, check=False, return_layers=False):
        torch = self.torch
        x = torch.tensor(np.asarray(x), dtype=torch.float64, device=self.device)
        stages = []
        with torch.no_grad():
            for layer, (w, b, wf, gb) in enumerate(
                zip(self.weights, self.biases, self.spectra, self.gpu_biases)
            ):
                before = x
                xf = torch.fft.rfft2(x, s=(self.f, self.f))
                yf = torch.einsum("nihw,oihw->nohw", xf, wf)
                raw = torch.fft.irfft2(yf, s=(self.f, self.f))[
                    :, :, 5 : 5 + self.n, 5 : 5 + self.n
                ]
                if self.kind == "integer":
                    rounded = torch.round(raw)
                    error = float(torch.max(torch.abs(raw - rounded)).item())
                    self.max_integer_round_error = max(
                        self.max_integer_round_error, error
                    )
                    if error >= 1e-3:
                        raise ArithmeticError(
                            f"GPU integer FFT reconstruction guard failed: {error}"
                        )
                    y = rounded + gb
                    maximum = float(torch.max(torch.abs(y)).item())
                    self.max_absolute_integer = max(self.max_absolute_integer, maximum)
                    if maximum >= 2**52:
                        raise ArithmeticError(
                            "Integer values exceed conservative float64 exact-representation range"
                        )
                else:
                    y = raw + gb
                if check:
                    bx = before.detach().cpu().numpy()
                    by = y.detach().cpu().numpy()
                    if self.kind == "integer":
                        bx = bx.astype(np.int64)
                        by = by.astype(np.int64)
                    xp = np.pad(bx, ((0, 0), (0, 0), (5, 5), (5, 5)))
                    points = [
                        (0, 0),
                        (0, self.n - 1),
                        (self.n // 2, self.n // 2),
                        (self.n - 1, 0),
                        (self.n - 1, self.n - 1),
                    ]
                    for shot in sorted(set([0, len(bx) - 1])):
                        for r, c in points:
                            block = xp[shot, :, r : r + 11, c : c + 11]
                            exact = (
                                np.sum(
                                    block[None] * w,
                                    axis=(1, 2, 3),
                                    dtype=(
                                        np.int64
                                        if self.kind == "integer"
                                        else np.float64
                                    ),
                                )
                                + b
                            )
                            error = float(np.max(np.abs(exact - by[shot, :, r, c])))
                            self.max_direct_error = max(self.max_direct_error, error)
                            if self.kind == "integer":
                                if not np.array_equal(exact, by[shot, :, r, c]):
                                    raise AssertionError(
                                        (
                                            "GPU integer direct mismatch",
                                            layer,
                                            shot,
                                            r,
                                            c,
                                        )
                                    )
                            elif not np.allclose(
                                exact, by[shot, :, r, c], atol=1e-9, rtol=1e-10
                            ):
                                raise AssertionError(
                                    (
                                        "GPU float direct mismatch",
                                        layer,
                                        shot,
                                        r,
                                        c,
                                        error,
                                    )
                                )
                            self.direct_values_checked += len(b)
                x = torch.clamp(y, min=0) if layer < 2 else y
                if return_layers:
                    stage = x.detach().cpu().numpy().copy()
                    stages.append(
                        stage.astype(np.int64) if self.kind == "integer" else stage
                    )
            if self.kind == "float":
                self.smallest_float_logit = min(
                    self.smallest_float_logit, float(torch.min(torch.abs(x)).item())
                )
            result = x.detach().cpu().numpy()
            if self.kind == "integer":
                result = result.astype(np.int64)
        return (result, stages) if return_layers else result
