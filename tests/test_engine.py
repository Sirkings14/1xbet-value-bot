import csv
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import main as engine

from main import (
    append_signal,
    build_signal_id,
    devig_shin,
    find_target_price,
    fractional_kelly,
    is_event_eligible,
    market_groups,
    market_is_fresh,
    normalise_name,
    probability_spread_pp,
    send_telegram,
    should_alert,
    unique_book_snapshots,
    valid_market_signature,
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
            {"book": "pinnacle", "prices": {
                "Over 2.5": 1.91, "Under 2.5": 1.95,
                "Over 3.5": 2.60, "Under 3.5": 1.50,
            }},
            {"book": "betfair_ex_uk", "prices": {
                "Over 2.5": 1.88, "Under 2.5": 2.00,
                "Over 3.5": 2.55, "Under 3.5": 1.52,
            }},
        ]
        groups = market_groups(markets, "totals")
        self.assertEqual(len(groups), 2)
        self.assertTrue(all(len(names) == 2 for names in groups))
        self.assertTrue(all(len(snapshots) == 2 for snapshots in groups.values()))

    def test_totals_point_format_is_canonicalised(self):
        self.assertEqual(normalise_name("Over", "2.50"), "Over 2.5")

    def test_soccer_h2h_requires_three_way_market(self):
        self.assertFalse(valid_market_signature("soccer_epl", "h2h", {"Home FC", "Away FC"}))
        self.assertTrue(valid_market_signature("soccer_epl", "h2h", {"Home FC", "Draw", "Away FC"}))

    def test_totals_signature_requires_over_and_under(self):
        self.assertTrue(valid_market_signature("soccer_epl", "totals", {"Over 2.5", "Under 2.5"}))
        self.assertFalse(valid_market_signature("soccer_epl", "totals", {"Over 2.5", "Over 3.5"}))

    def test_duplicate_book_observations_are_excluded(self):
        outcomes = {"Home FC", "Draw", "Away FC"}
        snapshots = [
            {"book": "pinnacle", "prices": {"Home FC": 2.0, "Draw": 3.0, "Away FC": 4.0}},
            {"book": "pinnacle", "prices": {"Home FC": 2.1, "Draw": 3.0, "Away FC": 3.9}},
            {"book": "betfair_ex_uk", "prices": {"Home FC": 2.0, "Draw": 3.0, "Away FC": 4.0}},
        ]
        unique, duplicates = unique_book_snapshots(snapshots, outcomes)
        self.assertEqual([row["book"] for row in unique], ["betfair_ex_uk"])
        self.assertEqual(duplicates, ["pinnacle"])

    def test_probability_spread_is_reported_in_percentage_points(self):
        samples = {"Home": [0.50, 0.54], "Away": [0.50, 0.46]}
        self.assertAlmostEqual(probability_spread_pp(samples), 4.0)

    def test_market_freshness_requires_recent_timestamp(self):
        now = datetime(2026, 10, 13, 6, 0, tzinfo=timezone.utc)
        self.assertTrue(market_is_fresh(
            {"last_update": (now - timedelta(minutes=5)).isoformat()}, now=now
        ))
        self.assertFalse(market_is_fresh(
            {"last_update": (now - timedelta(minutes=20)).isoformat()}, now=now
        ))
        self.assertFalse(market_is_fresh({}, now=now))

    def test_signal_id_is_stable_and_price_sensitive(self):
        first = build_signal_id("evt-1", "h2h", "Home FC", 2.10, 0.50)
        same = build_signal_id("evt-1", "h2h", "Home FC", 2.10, 0.50)
        changed = build_signal_id("evt-1", "h2h", "Home FC", 2.15, 0.50)
        self.assertEqual(first, same)
        self.assertNotEqual(first, changed)

    def test_append_signal_migrates_schema_and_deduplicates_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signals.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["timestamp", "event_id", "status"])
                writer.writeheader()
                writer.writerow({"timestamp": "2026-10-01T00:00:00+00:00", "event_id": "old-event", "status": ""})

            row = {
                "timestamp": "2026-10-13T06:00:00+00:00",
                "event_id": "evt-1",
                "sport": "soccer_epl",
                "market": "h2h",
                "selection": "Home FC",
                "offered_odds": 2.1,
                "fair_odds": 2.0,
                "fair_probability": 0.5,
                "ev_percent": 5.0,
                "stake_pct": 0.5,
                "sharp_books": 2,
                "sharp_sample_count": 2,
                "signal_id": "abc123",
                "target_book": "onexbet",
                "sharp_book_keys": "betfair_ex_uk|pinnacle",
                "sharp_probability_spread_pp": 1.2,
            }
            self.assertTrue(append_signal(row, path=path))
            self.assertFalse(append_signal(row, path=path))
            with path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                saved = list(reader)
                self.assertIn("signal_id", reader.fieldnames)
                self.assertIn("target_book", reader.fieldnames)
            self.assertEqual(len(saved), 2)
            self.assertEqual(saved[0]["event_id"], "old-event")
            self.assertEqual(saved[1]["signal_id"], "abc123")

    def test_missing_telegram_configuration_is_not_reported_as_delivery(self):
        output = StringIO()
        with patch.object(engine, "TELEGRAM_TOKEN", None), patch.object(engine, "TELEGRAM_CHAT_ID", None):
            with redirect_stdout(output):
                result = send_telegram("test alert")
        self.assertIsNone(result)
        self.assertIn("not configured", output.getvalue().lower())

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
