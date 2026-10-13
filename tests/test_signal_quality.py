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

    def test_signal_provenance_fields_are_validated(self):
        row = self.valid_row()
        row["sharp_books"] = "2"
        row["sharp_sample_count"] = "2"
        row.update({
            "signal_id": "a" * 24,
            "target_book": "onexbet",
            "sharp_book_keys": "betfair_ex_uk|pinnacle",
            "sharp_probability_spread_pp": "2.5",
        })
        self.assertEqual(validate_row(row, 2), [])

        row["sharp_book_keys"] = "pinnacle|pinnacle"
        errors = validate_row(row, 2)
        self.assertTrue(any("unique sharp_book_keys" in error for error in errors))

    def test_signal_with_excessive_probability_disagreement_is_rejected(self):
        row = self.valid_row()
        row["sharp_books"] = "2"
        row["sharp_sample_count"] = "2"
        row.update({
            "signal_id": "b" * 24,
            "target_book": "onexbet",
            "sharp_book_keys": "betfair_ex_uk|pinnacle",
            "sharp_probability_spread_pp": "9.5",
        })
        errors = validate_row(row, 2)
        self.assertTrue(any("probability spread exceeds" in error for error in errors))

    def test_duplicate_signal_ids_are_rejected_in_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signals.csv"
            row = self.valid_row()
            row["sharp_books"] = "2"
            row["sharp_sample_count"] = "2"
            row.update({
                "signal_id": "c" * 24,
                "target_book": "onexbet",
                "sharp_book_keys": "betfair_ex_uk|pinnacle",
                "sharp_probability_spread_pp": "1.0",
            })
            fields = list(row)
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow(row)
                writer.writerow(row)
            errors = validate_file(path)
            self.assertTrue(any("duplicate signal_id" in error for error in errors))

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

    def test_identified_settled_signal_requires_settlement_provenance(self):
        row = self.valid_row()
        row["sharp_books"] = "2"
        row["sharp_sample_count"] = "2"
        row.update({
            "signal_id": "d" * 24,
            "target_book": "onexbet",
            "sharp_book_keys": "betfair_ex_uk|pinnacle",
            "sharp_probability_spread_pp": "1.2",
            "status": "win",
            "profit_units": "1.0",
            "settled_at": "2026-10-13T06:00:00+00:00",
            "settlement_method": "manual_verified",
            "settlement_source": "Official results",
            "settlement_source_url": "https://example.com/results",
            "settlement_verified_at": "2026-10-13T05:55:00+00:00",
        })
        self.assertEqual(validate_row(row, 2), [])
        row["settlement_source_url"] = "javascript:bad()"
        self.assertTrue(any("http(s) source URL" in error for error in validate_row(row, 2)))

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
