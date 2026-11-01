"""Explore magnitude pruning on the distance-33 defect input geometry."""

from pathlib import Path
import argparse
import numpy as np
from scipy.fft import rfft2
from .defect_inputs import defect_error_boards
from .spatial import FFTReference
from .io import write, protocol


def evaluate(inputs, errors, thresholds, batch=8):
    """Report one-pass residual pixels and agreement, without global cleanup.

    Biases remain unchanged. This command uses the portable float64 convolution
    reference, so it repeats the procedure rather than claiming bitwise identity
    with the original unseeded Keras notebook execution.
    """
    n = inputs.shape[1]
    arrays = np.moveaxis(inputs, -1, 1)
    truth = np.moveaxis(errors, -1, 1).astype(np.uint8)
    engine = FFTReference("float", n)
    original = [weight.copy() for weight in engine.weights]
    baseline = np.concatenate(
        [
            (engine.forward(arrays[start : start + batch]) > 0).astype(np.uint8)
            for start in range(0, len(arrays), batch)
        ]
    )
    rows = []
    for threshold in thresholds:
        engine.weights = [
            np.where(np.abs(weight) < threshold, 0, weight) for weight in original
        ]
        engine.spectra = [
            rfft2(
                weight[:, :, ::-1, ::-1], s=(engine.f, engine.f), workers=engine.workers
            )
            for weight in engine.weights
        ]
        prediction = np.concatenate(
            [
                (engine.forward(arrays[start : start + batch]) > 0).astype(np.uint8)
                for start in range(0, len(arrays), batch)
            ]
        )
        removed = sum(
            int(np.count_nonzero(np.abs(weight) < threshold)) for weight in original
        )
        total = sum(weight.size for weight in original)
        rows.append(
            {
                "threshold": float(threshold),
                "sparsity_percent": 100 * removed / total,
                "residual_pixels": int(np.sum(truth ^ prediction)),
                "agreement_percent": 100 * float(np.mean(prediction == baseline)),
                "baseline_residual_pixels": int(np.sum(truth ^ baseline)),
            }
        )
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shots", type=int, default=200)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--probability", type=float, default=0.10)
    parser.add_argument(
        "--thresholds",
        type=float,
        nargs="+",
        default=[0, 0.001, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2],
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        min(args.shots, args.batch) < 1
        or args.seed < 0
        or any(not np.isfinite(x) or x < 0 for x in args.thresholds)
        or not 0 <= args.probability <= 1
    ):
        parser.error("Invalid shot count, batch, seed or pruning threshold.")
    settings = vars(args).copy()
    settings.pop("output")
    record = protocol(args.output, settings)
    inputs, errors, defects = defect_error_boards(
        args.probability, args.shots, 33, 3, 3, seed=args.seed
    )
    np.savez_compressed(
        args.output / "inputs.npz",
        inputs=inputs,
        errors=errors,
        defects=np.asarray(defects),
    )
    rows = evaluate(inputs, errors, args.thresholds, args.batch)
    write(
        args.output / "summary.json",
        {
            "protocol": record,
            "results": rows,
            "scope": "Residual pixels after one neural pass. No global decoder or sparse FPGA implementation is tested.",
        },
    )


if __name__ == "__main__":
    main()
