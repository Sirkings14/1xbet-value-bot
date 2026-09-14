import unittest
from datetime import datetime, timedelta, timezone

from clv import clv_percent, parse_time, snapshot_due


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


if __name__ == "__main__":
    unittest.main()
