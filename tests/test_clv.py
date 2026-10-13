import unittest
from datetime import datetime, timedelta, timezone

from clv import capture_signal, clv_percent, parse_time, snapshot_due


class ClvTests(unittest.TestCase):
    def test_positive_clv_when_later_price_shortens(self):
        self.assertAlmostEqual(clv_percent(2.20, 2.00), 10.0, places=6)

    def test_negative_clv_when_later_price_drifts(self):
        self.assertLess(clv_percent(2.00, 2.20), 0.0)

    def test_invalid_odds_rejected(self):
        with self.assertRaises(ValueError):
            clv_percent(1.0, 2.0)

    def test_snapshot_window(self):
        now = datetime(2026, 9, 14, 20, 0, tzinfo=timezone.utc)
        row = {"commence_time": (now + timedelta(minutes=5)).isoformat()}
        self.assertTrue(snapshot_due(row, now=now))
        row["commence_time"] = (now + timedelta(minutes=20)).isoformat()
        self.assertFalse(snapshot_due(row, now=now))

    def test_parse_time_requires_timezone(self):
        self.assertIsNotNone(parse_time("2026-09-14T20:00:00Z"))
        self.assertIsNone(parse_time("2026-09-14T20:00:00"))

    def test_capture_clv_uses_fresh_sharp_book_consensus(self):
        update_time = datetime.now(timezone.utc).isoformat()
        event = {
            "id": "evt-1",
            "sport_key": "soccer_epl",
            "home_team": "Home FC",
            "away_team": "Away FC",
            "bookmakers": [
                {
                    "key": "pinnacle",
                    "last_update": update_time,
                    "markets": [{
                        "key": "h2h",
                        "last_update": update_time,
                        "outcomes": [
                            {"name": "Home FC", "price": 2.00},
                            {"name": "Draw", "price": 3.20},
                            {"name": "Away FC", "price": 4.00},
                        ],
                    }],
                },
                {
                    "key": "betfair_ex_uk",
                    "last_update": update_time,
                    "markets": [{
                        "key": "h2h",
                        "last_update": update_time,
                        "outcomes": [
                            {"name": "Home FC", "price": 2.10},
                            {"name": "Draw", "price": 3.10},
                            {"name": "Away FC", "price": 3.80},
                        ],
                    }],
                },
                {
                    "key": "onexbet",
                    "last_update": update_time,
                    "markets": [{
                        "key": "h2h",
                        "last_update": update_time,
                        "outcomes": [
                            {"name": "Home FC", "price": 2.30},
                            {"name": "Draw", "price": 3.10},
                            {"name": "Away FC", "price": 3.20},
                        ],
                    }],
                },
            ],
        }
        signal = {
            "timestamp": "2026-10-13T06:00:00+00:00",
            "event_id": "evt-1",
            "sport": "soccer_epl",
            "market": "h2h",
            "selection": "Home FC",
            "offered_odds": "2.20",
        }

        snapshot = capture_signal(signal, event)
        self.assertIsNotNone(snapshot)
        self.assertAlmostEqual(snapshot["reference_odds"], 2.05)
        self.assertEqual(snapshot["reference_book_count"], 2)
        self.assertEqual(snapshot["reference_book_keys"], "betfair_ex_uk|pinnacle")
        self.assertGreater(snapshot["clv_percent"], 0)


if __name__ == "__main__":
    unittest.main()
