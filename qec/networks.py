"""Space-time CNN inference with floating-point and exact integer arithmetic."""

from __future__ import annotations
from pathlib import Path
import os
import re
import numpy as np
import torch
import torch.nn.functional as F

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "models/3d/float_parameters.npz"
INTEGER_MODEL = ROOT / "models/3d/integer_parameters.npz"


def configure(device="cpu", threads=4):
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    torch.set_num_threads(threads)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.use_deterministic_algorithms(True)
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable")


class FloatCNN(torch.nn.Module):
    def __init__(self, device="cpu"):
        super().__init__()
        self.conv1 = torch.nn.Conv3d(12, 128, 3, padding=1)
        self.conv2 = torch.nn.Conv3d(128, 128, 3, padding=1)
        self.conv3 = torch.nn.Conv3d(128, 128, 3, padding=1)
        self.conv4 = torch.nn.Conv3d(140, 12, 3, padding=1)
        with np.load(MODEL, allow_pickle=False) as archive:
            self.load_state_dict(
                {key: torch.from_numpy(archive[key].copy()) for key in archive.files}
            )
        self.eval().to(device)
        self.device = device

    def forward(self, x):
        h = F.relu(self.conv1(x))
        h = F.relu(self.conv2(h))
        h = F.relu(self.conv3(h))
        # Match the original float sigmoid and strict probability threshold.
        return torch.sigmoid(self.conv4(torch.cat([h, x], 1))) > 0.5

    @torch.no_grad()
    def predict(self, x):
        return (
            self(torch.as_tensor(x, dtype=torch.float32, device=self.device))
            .cpu()
            .numpy()
            .astype(np.uint8)
        )


def load_integer():
    with np.load(INTEGER_MODEL, allow_pickle=False) as archive:
        weights = [archive[f"weight_{i}"].copy() for i in range(5)]
        biases = [archive[f"bias_{i}"].copy() for i in range(4)]
        mult = [archive[f"multiplier_{i}"].copy() for i in range(3)]
        shifts = archive["shifts"].astype(int).tolist()
    return weights, biases, mult, shifts


def arithmetic_contract():
    """Conservative absolute-sum bounds independent of distance/input pattern."""
    weights, biases, multipliers, shifts = load_integer()
    layers = []
    for i in range(3):
        xmax = 1 if i == 0 else 255
        bound = np.abs(weights[i]).sum((1, 2, 3, 4)) * xmax + np.abs(biases[i])
        product = bound * multipliers[i] + (1 << (shifts[i] - 1))
        assert bound.max() < 2**25 and product.max() < 2**47
        layers.append(
            {
                "layer": i + 1,
                "max_absolute_accumulator": int(bound.max()),
                "requant_25bit_input_safe": True,
                "max_multiply_plus_round": int(product.max()),
                "signed_48bit_safe": True,
            }
        )
    output = (
        255 * np.abs(weights[3]).sum((1, 2, 3, 4))
        + np.abs(weights[4]).sum((1, 2, 3, 4))
        + np.abs(biases[3])
    )
    assert output.max() < 2**31
    return {
        "hidden": layers,
        "output_including_skip_absolute_bound": int(output.max()),
        "signed_32bit_output_safe": True,
        "scope": "Conservative absolute-weight-sum bounds for fixed integer parameters, binary input and uint8 hidden activations. Applies at any tensor size; zero padding cannot increase the bound. Finite RTL vectors still separately validate layout/control.",
    }


class IntegerCNN:
    """Exact-integer GEMM decomposition, no re-quantization of model weights.

    Im2col products are integer-valued float32. Every chunk's absolute sum
    is bounded below 2**22, hence products and any partial sums are exactly
    representable at float32 precision. TF32/reduced precision are disabled.
    Chunks and biases are accumulated in int64; activation requantization is
    integer round-shift/clamp. Full per-channel worst-case bounds are saved.
    The float64 reference and RTL golden outputs independently validate
    finite vectors. These are software calculations, not FPGA timing measurements.
    """

    def __init__(self, device="cpu", reference=False):
        self.device = device
        self.reference = reference
        wn, bn, mn, self.shifts = load_integer()
        self.wn = wn
        self.bn = bn
        self.mn = mn
        self.weights = [
            torch.as_tensor(
                w, dtype=torch.float64 if reference else torch.float32, device=device
            )
            for w in wn
        ]
        self.biases = [
            torch.as_tensor(b, dtype=torch.int64, device=device)[
                None, :, None, None, None
            ]
            for b in bn
        ]
        self.mult = [
            torch.as_tensor(m, dtype=torch.int64, device=device)[
                None, :, None, None, None
            ]
            for m in mn
        ]
        self.chunks = []
        self.bounds = []
        for li, w in enumerate(wn):
            xmax = 1 if li in (0, 4) else 255
            flat = w.reshape(w.shape[0], -1)
            per = np.abs(flat).max(axis=0) * xmax
            segments = []
            start = 0
            cost = 0
            for k, b in enumerate(per):
                if cost + int(b) >= 2**22 and k > start:
                    segments.append((start, k))
                    start = k
                    cost = 0
                cost += int(b)
            segments.append((start, len(per)))
            self.chunks.append(segments)
            self.bounds.append(
                {
                    "layer": li,
                    "xmax": xmax,
                    "absolute_dot_bound": int(np.max(np.abs(flat).sum(1)) * xmax),
                    "chunks": segments,
                    "max_chunk_absolute_bound": max(
                        int(np.max(np.abs(flat[:, a:b]).sum(1)) * xmax)
                        for a, b in segments
                    ),
                }
            )
        self.peak_noninteger_deviation = 0.0

    def conv(self, x, index):
        w = self.weights[index]
        if self.reference:
            result = F.conv3d(x.to(torch.float64), w, padding=1)
            return result.to(torch.int64)
        xp = F.pad(x.to(torch.float32), (1, 1, 1, 1, 1, 1))
        # Match torch kernel order (channel, kt, kr, kc).
        cols = (
            xp.unfold(2, 3, 1)
            .unfold(3, 3, 1)
            .unfold(4, 3, 1)
            .permute(0, 2, 3, 4, 1, 5, 6, 7)
            .contiguous()
        )
        shp = cols.shape[:4]
        cols = cols.reshape(-1, w.shape[1] * 27)
        flat = w.reshape(w.shape[0], -1)
        out = torch.zeros((len(cols), len(w)), dtype=torch.int64, device=self.device)
        for a, b in self.chunks[index]:
            v = cols[:, a:b] @ flat[:, a:b].T
            # This must be exactly integer; errors signal backend contract failure.
            if not torch.equal(v, v.round()):
                raise ArithmeticError("Integer GEMM produced fractional values")
            out += v.to(torch.int64)
        return out.reshape(*shp, len(w)).permute(0, 4, 1, 2, 3)

    @torch.no_grad()
    def forward(self, x, return_layers=False):
        x = torch.as_tensor(x, dtype=torch.float32, device=self.device)
        h = x
        layers = []
        for i in range(3):
            a = self.conv(h, i) + self.biases[i]
            h = torch.clamp(
                (torch.clamp(a, min=0) * self.mult[i] + (1 << (self.shifts[i] - 1)))
                >> self.shifts[i],
                max=255,
            )
            layers.append(h)
        logits = self.conv(h, 3) + self.conv(x, 4) + self.biases[3]
        layers.append(logits)
        return layers if return_layers else logits > 0

    def predict(self, x):
        return self.forward(x).cpu().numpy().astype(np.uint8)
