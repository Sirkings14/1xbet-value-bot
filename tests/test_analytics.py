import unittest
from datetime import datetime, timezone

from analytics import brier_score, build_report, calibration_buckets, max_drawdown, max_losing_streak, pending_summary, summary


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"sport": "soccer_epl", "market": "h2h", "status": "win", "profit_units": "1.0", "stake_pct": "1.0", "offered_odds": "2.0", "ev_percent": "5", "fair_probability": "0.55", "settled_at": "2026-09-01T10:00:00"},
            {"sport": "soccer_epl", "market": "h2h", "status": "loss", "profit_units": "-1.0", "stake_pct": "1.0", "offered_odds": "2.2", "ev_percent": "6", "fair_probability": "0.50", "settled_at": "2026-09-02T10:00:00"},
            {"sport": "tennis_atp_us_open", "market": "totals", "status": "win", "profit_units": "0.5", "stake_pct": "0.5", "offered_odds": "3.0", "ev_percent": "8.5", "fair_probability": "0.38", "settled_at": "2026-09-03T10:00:00"},
            {"sport": "tennis_atp_us_open", "market": "totals", "status": "push", "profit_units": "0", "stake_pct": "0.5", "offered_odds": "1.8", "ev_percent": "4", "fair_probability": "0.56", "settled_at": "2026-09-04T10:00:00"},
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

    def test_losing_streak_uses_settlement_order_not_signal_order(self):
        rows = [
            {"status": "loss", "settled_at": "2026-09-02T10:00:00+00:00"},
            {"status": "win", "settled_at": "2026-09-01T10:00:00+00:00"},
            {"status": "loss", "settled_at": "2026-09-03T10:00:00+00:00"},
        ]
        self.assertEqual(max_losing_streak(rows), 2)

    def test_drawdown(self):
        self.assertAlmostEqual(max_drawdown(self.rows), 1.0)

    def test_brier_score_excludes_pushes(self):
        expected = ((0.55 - 1.0) ** 2 + (0.50 - 0.0) ** 2 + (0.38 - 1.0) ** 2) / 3
        self.assertAlmostEqual(brier_score(self.rows), expected, places=6)

    def test_calibration_buckets(self):
        buckets = calibration_buckets(self.rows)
        self.assertEqual([row["group"] for row in buckets], ["0.2-0.4", "0.4-0.6"])
        self.assertEqual(buckets[0]["count"], 1)
        self.assertAlmostEqual(buckets[0]["actual_win_rate"], 1.0)
        self.assertAlmostEqual(buckets[1]["avg_predicted"], 0.525)

    def test_pending_summary_flags_overdue_kickoffs(self):
        now = datetime(2026, 10, 13, 7, 0, tzinfo=timezone.utc)
        rows = [
            {"status": "", "commence_time": "2026-10-12T07:00:00+00:00"},
            {"status": "", "commence_time": "2026-10-14T07:00:00+00:00"},
            {"status": "win", "commence_time": "2026-10-11T07:00:00+00:00"},
            {"status": "", "commence_time": "not-a-date"},
        ]
        result = pending_summary(rows, now=now)
        self.assertEqual(result["pending"], 3)
        self.assertEqual(result["overdue_pending"], 1)
        self.assertEqual(result["oldest_pending_age_hours"], 24.0)

    def test_report_contains_pending_counts_for_overall(self):
        now = datetime(2026, 10, 13, 7, 0, tzinfo=timezone.utc)
        rows = [
            dict(self.rows[0], status="", commence_time="2026-10-12T07:00:00+00:00"),
            dict(self.rows[1], commence_time="2026-09-01T07:00:00+00:00"),
        ]
        report = build_report(rows)
        overall = next(row for row in report if row["section"] == "overall")
        self.assertEqual(overall["pending"], 1)
        self.assertEqual(overall["overdue_pending"], 1)

    def test_report_contains_required_breakdowns(self):
        report = build_report(self.rows)
        sections = {row["section"] for row in report}
        self.assertEqual(sections, {"overall", "sport", "market", "signal_version", "ev_bucket", "odds_bucket"})
        overall = next(row for row in report if row["section"] == "overall")
        self.assertIn("max_drawdown_units", overall)
        self.assertIn("brier_score", overall)

    def test_report_separates_legacy_and_versioned_signal_cohorts(self):
        rows = [
            {"sport": "soccer_epl", "market": "h2h", "status": "win",
             "profit_units": "1.0", "stake_pct": "1.0", "offered_odds": "2.0",
             "ev_percent": "5", "fair_probability": "0.55",
             "settled_at": "2026-10-01T10:00:00+00:00"},
            {"sport": "soccer_epl", "market": "h2h", "status": "",
             "signal_version": "signal-integrity-v1", "profit_units": "",
             "stake_pct": "0.5", "offered_odds": "2.2", "ev_percent": "4",
             "fair_probability": "0.50", "commence_time": "2026-10-12T10:00:00+00:00"},
        ]
        report = build_report(rows)
        cohorts = {row["group"]: row for row in report if row["section"] == "signal_version"}
        self.assertIn("unknown", cohorts)
        self.assertIn("signal-integrity-v1", cohorts)
        self.assertEqual(cohorts["unknown"]["settled"], 1)
        self.assertEqual(cohorts["signal-integrity-v1"]["pending"], 1)

    def test_ev_buckets(self):
        report = build_report(self.rows)
        buckets = {row["group"] for row in report if row["section"] == "ev_bucket"}
        self.assertEqual(buckets, {"3-5", "5-8", "8+"})


if __name__ == "__main__":
    unittest.main()
