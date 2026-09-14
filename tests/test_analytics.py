import unittest

from analytics import build_report, max_losing_streak, summary


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"sport": "soccer_epl", "market": "h2h", "status": "win", "profit_units": "1.0", "stake_pct": "1.0", "offered_odds": "2.0", "ev_percent": "5", "fair_probability": "0.55"},
            {"sport": "soccer_epl", "market": "h2h", "status": "loss", "profit_units": "-1.0", "stake_pct": "1.0", "offered_odds": "2.2", "ev_percent": "6", "fair_probability": "0.50"},
            {"sport": "tennis_atp_us_open", "market": "totals", "status": "win", "profit_units": "0.5", "stake_pct": "0.5", "offered_odds": "3.0", "ev_percent": "8.5", "fair_probability": "0.38"},
            {"sport": "tennis_atp_us_open", "market": "totals", "status": "push", "profit_units": "0", "stake_pct": "0.5", "offered_odds": "1.8", "ev_percent": "4", "fair_probability": "0.56"},
        ]

    def test_summary(self):
        result = summary(self.rows)
        self.assertEqual(result["settled"], 4)
        self.assertEqual(result["wins"], 2)
        self.assertEqual(result["losses"], 1)
        self.assertEqual(result["pushes"], 1)
        self.assertAlmostEqual(result["profit_units"], 0.5)
        self.assertAlmostEqual(result["roi_percent"], 16.6667, places=3)

    def test_losing_streak(self):
        self.assertEqual(max_losing_streak(self.rows), 1)

    def test_report_contains_required_breakdowns(self):
        report = build_report(self.rows)
        sections = {row["section"] for row in report}
        self.assertEqual(sections, {"overall", "sport", "market", "ev_bucket", "odds_bucket"})

    def test_ev_buckets(self):
        report = build_report(self.rows)
        buckets = {row["group"] for row in report if row["section"] == "ev_bucket"}
        self.assertEqual(buckets, {"3-5", "5-8", "8+"})


if __name__ == "__main__":
    unittest.main()
