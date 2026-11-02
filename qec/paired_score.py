"""Paired score interval for the difference between two decoder failure rates.

The inputs are counts from the same trials, not two independently sampled rates.
This implements Tango's asymptotic score interval (Statistics in Medicine, 1998).
It leaves the conservative interval fields in the simulation commands unchanged.
"""

import argparse
import json
import math
from numbers import Integral, Real
from statistics import NormalDist, StatisticsError
from scipy.optimize import brentq


def paired_score_interval(worse_only, better_only, shots, confidence=0.95):
    """Return probability-difference limits for method A minus baseline B.

    worse_only counts A failures with B successes; better_only counts the reverse.
    Positive differences favour B. The remaining trials have matching outcomes.
    The interval is pointwise and asymptotic, not a simultaneous or exact bound.
    """
    counts = (worse_only, better_only, shots)
    if any(isinstance(value, bool) or not isinstance(value, Integral) for value in counts):
        raise ValueError("Discordant counts and shots must be integers")
    b, c, n = map(int, counts)
    if n <= 0 or n > 2**53 or min(b, c) < 0 or b + c > n:
        raise ValueError("Require positive shots <= 2**53 and nonnegative counts summing to at most shots")
    if (isinstance(confidence, bool) or not isinstance(confidence, Real)
            or not math.isfinite(confidence) or not 0 < confidence < 1):
        raise ValueError("Confidence must lie strictly between zero and one")
    try:
        z = NormalDist().inv_cdf((1 + confidence) / 2)
    except StatisticsError as error:
        raise ValueError("Confidence is too close to one for floating-point evaluation") from error

    def score(difference):
        # Fit the discordant probability with the rate difference held fixed.
        w = -b - c + (2 * n - b + c) * difference
        discriminant = w * w + 8 * n * c * difference * (1 - difference)
        other = (-w + math.sqrt(max(discriminant, 0.0))) / (4 * n)
        variance = n * (2 * other + difference * (1 - difference))
        residual = b - c - n * difference
        if variance <= 0:
            return math.copysign(math.inf, residual) if residual else 0.0
        return residual / math.sqrt(variance)

    estimate = (b - c) / n
    lower_edge, upper_edge = -1 + 1e-12, 1 - 1e-12
    if estimate <= lower_edge:
        lower = -1.0
    elif score(lower_edge) < z:
        lower = -1.0
    else:
        lower = brentq(lambda difference: score(difference) - z, lower_edge, estimate)
    if estimate >= upper_edge:
        upper = 1.0
    elif score(upper_edge) > -z:
        upper = 1.0
    else:
        upper = brentq(lambda difference: score(difference) + z, estimate, upper_edge)
    return lower, upper


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worse-only", type=int, required=True)
    parser.add_argument("--better-only", type=int, required=True)
    parser.add_argument("--shots", type=int, required=True)
    args = parser.parse_args()
    try:
        lower, upper = paired_score_interval(args.worse_only, args.better_only, args.shots)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(dict(
        interval_method="Tango asymptotic paired score", confidence=0.95,
        worse_only=args.worse_only, better_only=args.better_only, shots=args.shots,
        difference_pp=100 * (args.worse_only - args.better_only) / args.shots,
        lower_pp=100 * lower, upper_pp=100 * upper,
    ), indent=2))


if __name__ == "__main__":
    main()
