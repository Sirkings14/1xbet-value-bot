import math
import unittest

from main import devig_shin, fractional_kelly, normalise_name


class EngineTests(unittest.TestCase):
    def test_shin_probabilities_sum_to_one(self):
        probs = devig_shin([2.10, 3.40, 3.60])
        self.assertEqual(len(probs), 3)
        self.assertAlmostEqual(sum(probs), 1.0, places=8)
        self.assertTrue(all(0.0 < p < 1.0 for p in probs))

    def test_shin_rejects_invalid_market(self):
        self.assertEqual(devig_shin([1.0]), [])
        self.assertEqual(devig_shin([0.5, 2.0]), [])

    def test_kelly_is_zero_without_edge(self):
        self.assertEqual(fractional_kelly(0.25, 4.0), 0.0)

    def test_kelly_is_capped(self):
        stake = fractional_kelly(0.90, 4.0)
        self.assertGreater(stake, 0.0)
        self.assertLessEqual(stake, 2.0)

    def test_name_with_point(self):
        self.assertEqual(normalise_name("Over", 2.5), "Over 2.5")
        self.assertEqual(normalise_name("Home"), "Home")


if __name__ == "__main__":
    unittest.main()
