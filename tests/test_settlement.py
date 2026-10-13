import csv
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import settle_signals as settlement_module

from settle_signals import (
    load_manual_results,
    manual_result_matches_signal,
    settle_rows,
    settle_selection,
    settlement_audit,
    validate_manual_result,
)


class SettlementTests(unittest.TestCase):
    def setUp(self):
        self.event = {
            "id": "evt-1",
            "completed": True,
            "home_team": "Home FC",
            "away_team": "Away FC",
            "scores": [
                {"name": "Home FC", "score": "2"},
                {"name": "Away FC", "score": "1"},
            ],
        }
        self.signal = {
            "event_id": "evt-1",
            "match": "Home FC vs Away FC",
            "commence_time": "2026-10-12T16:30:00+00:00",
            "market": "h2h",
            "selection": "Home FC",
            "offered_odds": "2.10",
            "stake_pct": "1.0",
            "status": "",
        }
        self.manual = {
            "event_id": "evt-1",
            "home_team": "Home FC",
            "away_team": "Away FC",
            "home_score": 2,
            "away_score": 1,
            "completed": "true",
            "source_name": "Official competition results",
            "source_url": "https://example.com/official-results/evt-1",
            "verified_at": "2026-10-13T06:00:00+00:00",
        }

    def test_h2h_home_win(self):
        self.assertEqual(settle_selection(self.signal, self.event), "win")

    def test_h2h_away_loss(self):
        row = dict(self.signal, selection="Away FC")
        self.assertEqual(settle_selection(row, self.event), "loss")

    def test_h2h_draw(self):
        event = dict(self.event)
        event["scores"] = [{"name": "Home FC", "score": 1}, {"name": "Away FC", "score": 1}]
        self.assertEqual(settle_selection(dict(self.signal, sport="soccer_epl", selection="Draw"), event), "win")

    def test_unknown_h2h_selection_is_not_assumed_to_be_a_loss(self):
        self.assertIsNone(settle_selection(dict(self.signal, selection="Home"), self.event))

    def test_tied_score_in_non_soccer_h2h_remains_unresolved(self):
        event = dict(self.event)
        event["scores"] = [{"name": "Home FC", "score": 1}, {"name": "Away FC", "score": 1}]
        self.assertIsNone(settle_selection(dict(self.signal, sport="tennis_atp", selection="Home FC"), event))

    def test_totals(self):
        self.assertEqual(settle_selection(dict(self.signal, market="totals", selection="Over 2.5"), self.event), "win")
        self.assertEqual(settle_selection(dict(self.signal, market="totals", selection="Under 2.5"), self.event), "loss")

    def test_incomplete_event_is_not_settled(self):
        event = dict(self.event, completed=False)
        self.assertIsNone(settle_selection(self.signal, event))

    def test_non_finite_scores_are_ignored(self):
        event = dict(self.event)
        event["scores"] = [{"name": "Home FC", "score": "nan"}, {"name": "Away FC", "score": "1"}]
        self.assertIsNone(settle_selection(self.signal, event))

    def test_manual_result_requires_source_and_timezone_aware_verification(self):
        self.assertEqual(validate_manual_result(self.manual), [])
        invalid = dict(self.manual, source_url="javascript:alert(1)", verified_at="2026-10-13T06:00:00")
        errors = validate_manual_result(invalid)
        self.assertTrue(any("source_url" in error for error in errors))
        self.assertTrue(any("verified_at" in error for error in errors))

    def test_repository_backfill_contains_five_source_attributed_results(self):
        path = Path(__file__).resolve().parent.parent / "manual_results.csv"
        results, errors = load_manual_results(path)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 5)
        self.assertIn("22c9b2c43cbe1334381269550dab78e6", results)
        self.assertIn("0d9ff865c4599c72a746da08260279b3", results)

    def test_actual_repository_ledger_backfills_only_the_five_overdue_rows(self):
        root = Path(__file__).resolve().parent.parent
        with (root / "signals.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        manual, errors = load_manual_results(root / "manual_results.csv")
        self.assertEqual(errors, [])

        settled = settle_rows(
            rows,
            events_by_id={},
            manual_results=manual,
            now=datetime(2026, 10, 13, 7, 15, tzinfo=timezone.utc),
        )
        results_by_id = {
            row["event_id"]: row
            for row in settled
            if row.get("status") in {"win", "loss", "push"}
        }
        self.assertEqual(len(results_by_id), 5)
        self.assertEqual(len(settled) - len(results_by_id), 8)
        self.assertEqual(results_by_id["22c9b2c43cbe1334381269550dab78e6"]["status"], "win")
        self.assertEqual(results_by_id["da3d3fec3e0b6b939910427a76ecfb76"]["status"], "loss")
        self.assertEqual(results_by_id["5866dae1e72b96ab74508039cd3dcca7"]["status"], "loss")
        self.assertEqual(results_by_id["02e6c0ccb11bb7ce78989ce49d67110b"]["status"], "loss")
        self.assertEqual(results_by_id["0d9ff865c4599c72a746da08260279b3"]["status"], "win")
        self.assertTrue(all(row["settlement_method"] == "manual_verified" for row in results_by_id.values()))
        self.assertTrue(all(row["settlement_source_url"].startswith("https://") for row in results_by_id.values()))

    def test_manual_csv_loads_valid_result(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manual_results.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(self.manual))
                writer.writeheader()
                writer.writerow(self.manual)
            results, errors = load_manual_results(path)
            self.assertEqual(errors, [])
            self.assertIn("evt-1", results)
            self.assertEqual(results["evt-1"]["home_score"], 2.0)

    def test_duplicate_manual_event_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manual_results.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(self.manual))
                writer.writeheader()
                writer.writerow(self.manual)
                writer.writerow(self.manual)
            results, errors = load_manual_results(path)
            self.assertNotIn("evt-1", results)
            self.assertTrue(any("duplicate event_id" in error for error in errors))

    def test_manual_result_must_match_signal_teams(self):
        self.assertTrue(manual_result_matches_signal(self.signal, self.manual))
        wrong = dict(self.manual, away_team="Different FC")
        self.assertFalse(manual_result_matches_signal(self.signal, wrong))

    def test_manual_result_verification_must_follow_kickoff(self):
        signal = dict(self.signal, commence_time="2026-10-14T16:30:00+00:00")
        rows = settle_rows(
            [signal],
            {},
            {"evt-1": self.manual},
            now=datetime(2026, 10, 15, 7, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(rows[0]["status"], "")

    def test_verified_manual_result_settles_pending_signal_with_provenance(self):
        now = datetime(2026, 10, 13, 7, 0, tzinfo=timezone.utc)
        rows = settle_rows([self.signal], {}, {"evt-1": self.manual}, now=now)
        self.assertEqual(rows[0]["status"], "win")
        self.assertAlmostEqual(float(rows[0]["profit_units"]), 1.1)
        self.assertEqual(rows[0]["settlement_method"], "manual_verified")
        self.assertEqual(rows[0]["settlement_source"], "Official competition results")
        self.assertEqual(rows[0]["settlement_source_url"], self.manual["source_url"])
        self.assertEqual(rows[0]["settlement_verified_at"], self.manual["verified_at"])

    def test_manual_only_cli_does_not_call_scores_api_and_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            signals_path = root / "signals.csv"
            manual_path = root / "manual_results.csv"
            fields = list(self.signal)
            with signals_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow(self.signal)
            with manual_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(self.manual))
                writer.writeheader()
                writer.writerow(self.manual)
            original = signals_path.read_text(encoding="utf-8")
            output = StringIO()
            with patch.object(settlement_module, "SIGNALS_FILE", signals_path), \\
                 patch.object(settlement_module, "MANUAL_RESULTS_FILE", manual_path), \\
                 patch.object(settlement_module, "fetch_scores") as scores_api:
                with redirect_stdout(output):
                    result = settlement_module.main(["--manual-only", "--dry-run"])
            self.assertEqual(result, 0)
            scores_api.assert_not_called()
            self.assertEqual(signals_path.read_text(encoding="utf-8"), original)
            self.assertIn("manual-only", output.getvalue())
            self.assertIn("Dry run", output.getvalue())

    def test_api_result_takes_precedence_over_manual_fallback(self):
        now = datetime(2026, 10, 13, 7, 0, tzinfo=timezone.utc)
        api_event = dict(self.event)
        api_event["scores"] = [{"name": "Home FC", "score": 0}, {"name": "Away FC", "score": 1}]
        rows = settle_rows([self.signal], {"evt-1": api_event}, {"evt-1": self.manual}, now=now)
        self.assertEqual(rows[0]["status"], "loss")
        self.assertEqual(rows[0]["settlement_method"], "scores_api")

    def test_settled_rows_are_not_overwritten(self):
        settled = dict(self.signal, status="loss", profit_units="-1.000000", settled_at="2026-10-12T00:00:00+00:00", settlement_method="manual_verified")
        rows = settle_rows([settled], {"evt-1": self.event}, {"evt-1": self.manual})
        self.assertEqual(rows[0]["status"], "loss")
        self.assertEqual(rows[0]["profit_units"], "-1.000000")
        self.assertEqual(rows[0]["settlement_method"], "manual_verified")

    def test_audit_counts_past_kickoffs_still_pending(self):
        now = datetime(2026, 10, 13, 7, 0, tzinfo=timezone.utc)
        rows = [
            dict(self.signal, commence_time="2026-10-12T07:00:00+00:00"),
            dict(self.signal, event_id="future", commence_time="2026-10-14T07:00:00+00:00"),
            dict(self.signal, event_id="settled", status="win"),
        ]
        audit = settlement_audit(rows, now=now)
        self.assertEqual(audit["signals"], 3)
        self.assertEqual(audit["settled"], 1)
        self.assertEqual(audit["pending"], 2)
        self.assertEqual(audit["overdue_pending"], 1)
        self.assertEqual(audit["oldest_pending_age_hours"], 24.0)


if __name__ == "__main__":
    unittest.main()
