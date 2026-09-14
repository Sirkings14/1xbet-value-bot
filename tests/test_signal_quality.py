import csv
import tempfile
import unittest
from pathlib import Path

from signal_quality import validate_file, validate_row


class SignalQualityTests(unittest.TestCase):
    def valid_row(self):
        return {
            "event_id": "evt-1",
            "sport": "soccer_epl",
            "market": "h2h",
            "selection": "Home FC",
            "offered_odds": "2.10",
            "fair_odds": "1.90",
            "fair_probability": "0.5263157895",
            "ev_percent": "10.52631579",
            "stake_pct": "1.25",
            "sharp_books": "3",
            "sharp_sample_count": "3",
            "status": "",
            "profit_units": "",
            "settled_at": "",
        }

    def test_valid_row_passes(self):
        self.assertEqual(validate_row(self.valid_row(), 2), [])

    def test_impossible_odds_rejected(self):
        row = self.valid_row()
        row["offered_odds"] = "1"
        self.assertTrue(any("offered_odds" in e for e in validate_row(row, 2)))

    def test_ev_mismatch_rejected(self):
        row = self.valid_row()
        row["ev_percent"] = "99"
        self.assertTrue(any("ev_percent inconsistent" in e for e in validate_row(row, 2)))

    def test_stake_over_cap_rejected(self):
        row = self.valid_row()
        row["stake_pct"] = "2.01"
        self.assertTrue(any("stake_pct" in e for e in validate_row(row, 2)))

    def test_invalid_status_and_profit_rejected(self):
        row = self.valid_row()
        row["status"] = "maybe"
        self.assertTrue(any("invalid status" in e for e in validate_row(row, 2)))
        row["status"] = "win"
        row["profit_units"] = "not-a-number"
        row["settled_at"] = "2026-09-14T22:00:00+00:00"
        self.assertTrue(any("profit_units" in e for e in validate_row(row, 2)))

    def test_malformed_totals_selection_rejected(self):
        row = self.valid_row()
        row["market"] = "totals"
        row["selection"] = "Over"
        self.assertTrue(any("totals selection" in e for e in validate_row(row, 2)))

    def test_valid_push_row_passes(self):
        row = self.valid_row()
        row["market"] = "totals"
        row["selection"] = "Over 2.5"
        row["status"] = "push"
        row["profit_units"] = "0"
        row["settled_at"] = "2026-09-14T22:00:00+00:00"
        self.assertEqual(validate_row(row, 2), [])

    def test_missing_file_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(validate_file(Path(directory) / "missing.csv"), [])

    def test_missing_column_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signals.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["event_id"])
                writer.writeheader()
                writer.writerow({"event_id": "evt-1"})
            errors = validate_file(path)
            self.assertTrue(any("missing columns" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
