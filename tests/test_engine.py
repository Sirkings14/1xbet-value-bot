import unittest
from datetime import datetime, timezone, timedelta

from main import (
    devig_shin,
    find_target_price,
    fractional_kelly,
    is_event_eligible,
    market_groups,
    normalise_name,
    should_alert,
)


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

    def test_target_price_prefers_best_1xbet_alias(self):
        markets = [
            {"book": "1xbet", "prices": {"Home": 2.20}},
            {"book": "1x", "prices": {"Home": 2.25}},
        ]
        self.assertEqual(find_target_price(markets, "Home"), ("1x", 2.25))

    def test_market_groups_do_not_mix_totals_lines(self):
        markets = [
            {"book": "pinnacle", "prices": {"Over 2.5": 2.0, "Under 2.5": 1.8}},
            {"book": "betfair_ex_uk", "prices": {"Over 3.5": 2.5, "Under 3.5": 1.55}},
        ]
        groups = market_groups(markets, "totals")
        self.assertEqual(len(groups), 2)

    def test_alert_repeats_only_after_material_improvement(self):
        previous = {"offered_odds": 2.00, "ev_percent": 5.0}
        self.assertFalse(should_alert(previous, 2.02, 5.5))
        self.assertTrue(should_alert(previous, 2.05, 5.5))
        self.assertTrue(should_alert(previous, 2.00, 6.01))

    def test_stale_event_is_rejected(self):
        now = datetime(2026, 9, 14, 20, 0, tzinfo=timezone.utc)
        event = {"commence_time": (now - timedelta(minutes=1)).isoformat()}
        self.assertFalse(is_event_eligible(event, now=now))

    def test_future_event_is_eligible(self):
        now = datetime(2026, 9, 14, 20, 0, tzinfo=timezone.utc)
        event = {"commence_time": (now + timedelta(minutes=1)).isoformat()}
        self.assertTrue(is_event_eligible(event, now=now))

    def test_malformed_event_time_is_rejected(self):
        self.assertFalse(is_event_eligible({"commence_time": "not-a-date"}))


if __name__ == "__main__":
    unittest.main()
