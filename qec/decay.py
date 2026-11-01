"""Fit effective decay curves to the final-readout IBM experiments."""

from pathlib import Path
import argparse
import json
import numpy as np
from scipy.optimize import curve_fit
from .decode_ibm import idle_errors
from .io import write


def decay(time, offset, amplitude, lifetime, alpha):
    return offset + amplitude * (1 - np.exp(-((time / lifetime) ** alpha)))


def fit_decay(time, probability, free_alpha):
    """Use the same bounded offset/stretched-exponential model as the study."""
    time = np.asarray(time, dtype=float)
    probability = np.asarray(probability, dtype=float)
    if len(time) < 4 or len(time) != len(probability) or np.any(np.diff(time) <= 0):
        raise ValueError("At least four distinct increasing delay values are required.")
    if (
        not np.isfinite(time).all()
        or not np.isfinite(probability).all()
        or np.any(time < 0)
        or np.any((probability < 0) | (probability > 1))
    ):
        raise ValueError(
            "Delays and probabilities must be finite and physically valid."
        )
    initial = [
        probability[0],
        probability[-1] - probability[0],
        max(time[1], 5.0),
        1.5 if free_alpha else 1.0,
    ]
    low = [0, 0, 0.1, 0.2]
    high = [0.5, 1, 5000, 4]
    # Noisy traces can finish below their first value. Keep the starting point
    # inside the same fit bounds instead of failing before optimisation.
    initial = np.clip(initial, np.asarray(low) + 1e-10, np.asarray(high) - 1e-10)
    if free_alpha:
        params, covariance = curve_fit(
            decay, time, probability, p0=initial, bounds=(low, high), maxfev=20000
        )
    else:
        params, covariance = curve_fit(
            lambda t, e, a, T: decay(t, e, a, T, 1.0),
            time,
            probability,
            p0=initial[:3],
            bounds=(low[:3], high[:3]),
            maxfev=20000,
        )
        params = np.r_[params, 1.0]
    variance = float(covariance[2, 2])
    uncertainty_available = np.isfinite(variance) and variance >= 0
    return dict(
        offset=float(params[0]),
        amplitude=float(params[1]),
        time_us=float(params[2]),
        time_us_standard_error=(
            float(np.sqrt(variance)) if uncertainty_available else None
        ),
        fit_uncertainty_available=bool(uncertainty_available),
        alpha=float(params[3]),
        maximum_observed_delay_us=float(time[-1]),
        lifetime_beyond_observed_range=bool(params[2] > time[-1]),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inputs",
        type=Path,
        nargs="+",
        required=True,
        help="Completed idle-experiment folders.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    groups = {}
    for folder in args.inputs:
        manifest = json.loads((folder / "experiment.json").read_text(encoding="utf8"))
        if manifest["experiment"] != "idle" or manifest.get("preview"):
            parser.error("Only completed physical idle records belong in this fit.")
        config = manifest["configuration"]
        with np.load(folder / "measurements.npz", allow_pickle=False) as raw:
            errors = idle_errors(config, raw)
        key = (
            config["d"],
            config["basis"],
            config["prep"],
            config["dd"],
            config["placement"],
            manifest["backend"],
            json.dumps(manifest.get("layout"), sort_keys=True),
            config["prep_bits"],
        )
        groups.setdefault(key, []).append(
            (config["delay_us_actual"], float(np.mean(errors[:, 0] | errors[:, 1])))
        )
    rows = []
    for key, points in groups.items():
        points = sorted(points)
        time, probability = map(np.asarray, zip(*points))
        if len(points) < 4:
            continue
        result = fit_decay(time, probability, free_alpha=key[1] != "Z")
        rows.append(
            dict(
                distance=key[0],
                basis=key[1],
                prep=key[2],
                xy4=key[3],
                placement=key[4],
                backend=key[5],
                layout=json.loads(key[6]),
                delay_us=time.tolist(),
                probability=probability.tolist(),
                fit=result,
            )
        )
    write(
        args.output,
        {
            "fits": rows,
            "scope": "Effective fit parameters, not independent device T1/T2 calibration. Values beyond the observed delay range are extrapolated. Separate device, layout and preparation settings are fitted separately.",
        },
    )


if __name__ == "__main__":
    main()
