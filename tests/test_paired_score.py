"""Reference example and boundary checks for paired score intervals."""

import math
from statistics import NormalDist
import unittest
from qec.paired_score import paired_score_interval


class PairedScoreTests(unittest.TestCase):
    def test_published_worked_example(self):
        # The ratesci vignette's AS(Tango) example from Fagerland et al. (2014).
        lower, upper = paired_score_interval(1, 7, 21)
        self.assertAlmostEqual(lower, -0.5172, delta=5e-5)
        self.assertAlmostEqual(upper, -0.0260, delta=5e-5)

    def test_reversing_methods_reverses_interval(self):
        for b, c, n in ((0, 0, 1), (1, 7, 21), (3, 3, 12), (0, 10, 10),
                        (2347, 2581, 65536), (1, 0, 8192), (25, 75, 100)):
            with self.subTest(counts=(b, c, n)):
                lower, upper = paired_score_interval(b, c, n)
                reverse_lower, reverse_upper = paired_score_interval(c, b, n)
                self.assertAlmostEqual(lower, -reverse_upper, delta=1e-9)
                self.assertAlmostEqual(upper, -reverse_lower, delta=1e-9)
                self.assertLessEqual(lower, (b-c)/n)
                self.assertGreaterEqual(upper, (b-c)/n)
                self.assertTrue(-1 <= lower <= upper <= 1)

    def test_no_discordance_and_complete_discordance(self):
        n = 100
        z2 = NormalDist().inv_cdf(0.975)**2
        radius = z2 / (n + z2)
        lower, upper = paired_score_interval(0, 0, n)
        self.assertAlmostEqual(lower, -radius, delta=1e-10)
        self.assertAlmostEqual(upper, radius, delta=1e-10)
        lower, upper = paired_score_interval(n, 0, n)
        self.assertAlmostEqual(lower, (n-z2)/(n+z2), delta=1e-10)
        self.assertEqual(upper, 1)

    def test_invalid_counts_and_confidence(self):
        for counts in ((0, 0, 0), (-1, 0, 10), (6, 5, 10), (1.0, 0, 10),
                       (True, 0, 10), (0, 0, 2**53+1)):
            with self.subTest(counts=counts), self.assertRaises(ValueError):
                paired_score_interval(*counts)
        for confidence in (0, 1, -0.1, math.nan, math.inf, True, "0.95", 1-1e-16):
            with self.subTest(confidence=confidence), self.assertRaises(ValueError):
                paired_score_interval(1, 2, 10, confidence)


if __name__ == "__main__":
    unittest.main()
