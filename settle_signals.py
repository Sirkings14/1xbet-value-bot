"""Settle research signals from scores API data or explicitly verified manual results.

Manual results are a fallback for events that have aged out of the scores API window.
They require an exact event ID, matching recorded teams, a cited source URL, and a
timezone-aware verification timestamp. This script never places wagers.
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
SIGNALS_FILE = Path(os.getenv("SIGNALS_FILE", "signals.csv"))
MANUAL_RESULTS_FILE = Path(os.getenv("MANUAL_RESULTS_FILE", "manual_results.csv"))
SETTLEMENT_FIELDS = (
    "status",
    "profit_units",
    "settled_at",
    "settlement_method",
    "settlement_source",
    "settlement_source_url",
    "settlement_verified_at",
)
TOTALS_RE = re.compile(r"^(Over|Under)\s+([0-9]+(?:\.[05])?)$")


def parse_event_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def parse_score(event):
    scores = event.get("scores") or []
    result = {}
    for item in scores:
        try:
            name = str(item["name"])
            score = float(item["score"])
        except (KeyError, TypeError, ValueError):
            continue
        if name and math.isfinite(score) and score >= 0:
            result[name] = score
    return result


def settle_selection(row, event):
    """Return win/loss/push/None for supported final-score paper signals."""
    if not event or not event.get("completed"):
        return None
    scores = parse_score(event)
    home = event.get("home_team")
    away = event.get("away_team")
    if home not in scores or away not in scores:
        return None

    market = str(row.get("market") or "").strip().lower()
    selection = str(row.get("selection") or "").strip()
    home_score, away_score = scores[home], scores[away]

    if market == "h2h":
        allowed = {str(home).casefold(), str(away).casefold(), "draw"}
        if selection.casefold() not in allowed:
            return None
        if home_score == away_score:
            # A draw only settles a 3-way soccer head-to-head market. For other
            # sports, an unexpected tie is unresolved rather than a false loss.
            if not str(row.get("sport") or "").lower().startswith("soccer_"):
                return None
            winner = "Draw"
        elif home_score > away_score:
            winner = home
        else:
            winner = away
        return "win" if selection.casefold() == str(winner).casefold() else "loss"

    if market == "totals":
        match = TOTALS_RE.fullmatch(selection)
        if not match:
            return None
        side, line_text = match.groups()
        line = float(line_text)
        total = home_score + away_score
        if math.isclose(total, line, abs_tol=1e-9):
            return "push"
        won = total > line if side == "Over" else total < line
        return "win" if won else "loss"

    return None


def _normalise_team(value):
    return " ".join(str(value or "").casefold().split())


def _valid_source_url(value):
    parsed = urlparse(str(value or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_manual_result(result, row_number=0):
    """Return explicit validation errors for one row in manual_results.csv."""
    errors = []
    prefix = f"row {row_number}: " if row_number else ""
    required = ("event_id", "home_team", "away_team", "home_score", "away_score",
                "completed", "source_name", "source_url", "verified_at")
    for field in required:
        if not str(result.get(field) or "").strip():
            errors.append(f"{prefix}missing {field}")

    event_id = str(result.get("event_id") or "").strip()
    home = str(result.get("home_team") or "").strip()
    away = str(result.get("away_team") or "").strip()
    if home and away and _normalise_team(home) == _normalise_team(away):
        errors.append(f"{prefix}home_team and away_team must be different")

    for field in ("home_score", "away_score"):
        raw = result.get(field)
        try:
            score = float(raw)
        except (TypeError, ValueError):
            score = math.nan
        if not math.isfinite(score) or score < 0:
            errors.append(f"{prefix}{field} must be a finite non-negative number")

    if str(result.get("completed") or "").strip().casefold() not in {"true", "1", "yes"}:
        errors.append(f"{prefix}completed must be true to permit settlement")
    if result.get("source_url") and not _valid_source_url(result["source_url"]):
        errors.append(f"{prefix}source_url must be an http(s) URL")
    verified_at = parse_event_time(result.get("verified_at"))
    if verified_at is None:
        errors.append(f"{prefix}verified_at must be an ISO timestamp with timezone")
    return errors


def load_manual_results(path=MANUAL_RESULTS_FILE):
    """Load only validated rows; duplicate event IDs are rejected rather than guessed."""
    path = Path(path)
    if not path.exists():
        return {}, []
    results = {}
    errors = []
    seen_event_ids = set()
    duplicate_ids = set()
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return {}, [f"{path}: missing CSV header"]
        missing = sorted(set((
            "event_id", "home_team", "away_team", "home_score", "away_score",
            "completed", "source_name", "source_url", "verified_at",
        )) - set(reader.fieldnames))
        if missing:
            return {}, [f"{path}: missing columns: {', '.join(missing)}"]
        for row_number, result in enumerate(reader, start=2):
            row_errors = validate_manual_result(result, row_number)
            event_id = str(result.get("event_id") or "").strip()
            if event_id and event_id in seen_event_ids:
                duplicate_ids.add(event_id)
                results.pop(event_id, None)
                errors.append(f"row {row_number}: duplicate event_id '{event_id}'; all rows for this event ignored")
                continue
            if event_id:
                seen_event_ids.add(event_id)
            if event_id in duplicate_ids:
                errors.append(f"row {row_number}: duplicate event_id '{event_id}'; row ignored")
            elif row_errors:
                errors.extend(row_errors)
            else:
                clean = dict(result)
                clean["event_id"] = event_id
                clean["home_team"] = str(result["home_team"]).strip()
                clean["away_team"] = str(result["away_team"]).strip()
                clean["home_score"] = float(result["home_score"])
                clean["away_score"] = float(result["away_score"])
                clean["verified_at"] = parse_event_time(result["verified_at"]).isoformat()
                results[event_id] = clean
    return results, errors


def manual_result_to_event(result):
    return {
        "id": result["event_id"],
        "completed": True,
        "home_team": result["home_team"],
        "away_team": result["away_team"],
        "scores": [
            {"name": result["home_team"], "score": result["home_score"]},
            {"name": result["away_team"], "score": result["away_score"]},
        ],
    }


def manual_result_matches_signal(row, result):
    """Require an exact event ID and the same home/away labels as the signal ledger."""
    expected = str(row.get("match") or "").strip()
    supplied = f"{result['home_team']} vs {result['away_team']}"
    return bool(expected) and _normalise_team(expected) == _normalise_team(supplied)


def manual_result_is_after_kickoff(row, result):
    """A manual score can only settle an event whose kickoff predates verification."""
    kickoff = parse_event_time(row.get("commence_time"))
    verified_at = parse_event_time(result.get("verified_at"))
    return kickoff is not None and verified_at is not None and kickoff < verified_at


def settle_rows(rows, events_by_id, manual_results=None, now=None):
    """Settle pending rows only; preserve settled records and append provenance fields."""
    manual_results = manual_results or {}
    now = now or datetime.now(timezone.utc)
    updated = []
    for original in rows:
        row = dict(original)
        if str(row.get("status") or "").strip().lower() in {"win", "loss", "push"}:
            updated.append(row)
            continue

        event_id = str(row.get("event_id") or "")
        event = events_by_id.get(event_id)
        result = settle_selection(row, event) if event else None
        settlement_method = "scores_api" if result else ""
        source_name = "The Odds API scores endpoint" if result else ""
        source_url = "https://the-odds-api.com/" if result else ""
        verified_at = now.isoformat() if result else ""

        if not result:
            manual = manual_results.get(event_id)
            if (
                manual
                and manual_result_matches_signal(row, manual)
                and manual_result_is_after_kickoff(row, manual)
            ):
                manual_event = manual_result_to_event(manual)
                result = settle_selection(row, manual_event)
                if result:
                    settlement_method = "manual_verified"
                    source_name = str(manual.get("source_name") or "").strip()
                    source_url = str(manual.get("source_url") or "").strip()
                    verified_at = str(manual.get("verified_at") or "").strip()

        if result:
            try:
                stake = float(row.get("stake_pct") or 0)
                odds = float(row.get("offered_odds") or 0)
            except (TypeError, ValueError):
                updated.append(row)
                continue
            if not math.isfinite(stake) or stake < 0 or not math.isfinite(odds) or odds <= 1:
                updated.append(row)
                continue
            if result == "win":
                profit = stake * (odds - 1.0)
            elif result == "loss":
                profit = -stake
            else:
                profit = 0.0
            row["status"] = result
            row["profit_units"] = f"{profit:.6f}"
            row["settled_at"] = now.isoformat()
            row["settlement_method"] = settlement_method
            row["settlement_source"] = source_name
            row["settlement_source_url"] = source_url
            row["settlement_verified_at"] = verified_at
        updated.append(row)
    return updated


def settlement_audit(rows, now=None):
    now = now or datetime.now(timezone.utc)
    settled = [row for row in rows if str(row.get("status") or "").strip().lower() in {"win", "loss", "push"}]
    pending = [
        row for row in rows
        if str(row.get("status") or "").strip().lower() not in {"win", "loss", "push"}
    ]
    overdue_ages = []
    for row in pending:
        commence = parse_event_time(row.get("commence_time"))
        if commence and commence < now:
            overdue_ages.append((now - commence).total_seconds() / 3600.0)
    return {
        "signals": len(rows),
        "settled": len(settled),
        "pending": len(pending),
        "overdue_pending": len(overdue_ages),
        "oldest_pending_age_hours": round(max(overdue_ages), 2) if overdue_ages else 0.0,
    }


def fetch_scores(sport_key):
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY is not configured")
    response = requests.get(
        f"{ODDS_API_BASE}/sports/{sport_key}/scores/",
        params={"apiKey": ODDS_API_KEY, "daysFrom": 3},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def load_rows(path=SIGNALS_FILE):
    path = Path(path)
    if not path.exists():
        return [], []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    return rows, fields


def save_rows(rows, fields, path=SIGNALS_FILE):
    path = Path(path)
    fields = list(fields)
    for extra in SETTLEMENT_FIELDS:
        if extra not in fields:
            fields.append(extra)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def report(rows, now=None):
    audit = settlement_audit(rows, now=now)
    settled = [r for r in rows if str(r.get("status") or "").strip().lower() in {"win", "loss", "push"}]
    wins = sum(str(r.get("status") or "").lower() == "win" for r in settled)
    losses = sum(str(r.get("status") or "").lower() == "loss" for r in settled)
    pushes = sum(str(r.get("status") or "").lower() == "push" for r in settled)
    profit = sum(float(r.get("profit_units") or 0) for r in settled)
    turnover = sum(float(r.get("stake_pct") or 0) for r in settled)
    roi = (profit / turnover * 100.0) if turnover else 0.0
    print(f"Signals: {audit['signals']} | Settled: {audit['settled']} | Pending: {audit['pending']}")
    print(f"Overdue pending: {audit['overdue_pending']} | Oldest pending age: {audit['oldest_pending_age_hours']:.2f} hours")
    print(f"Settled W/L/P: {wins}/{losses}/{pushes}")
    print(f"Paper P/L: {profit:.4f} stake-pct units | ROI: {roi:.2f}%")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Settle paper signals from completed scores.")
    parser.add_argument(
        "--manual-only",
        action="store_true",
        help="Use only the verified manual_results.csv backfill; do not call the scores API.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the resulting settlement summary without writing signals.csv.",
    )
    args = parser.parse_args(argv)

    rows, fields = load_rows()
    if not rows:
        print("No signals.csv yet; nothing to settle.")
        return 0

    manual_results, manual_errors = load_manual_results()
    for error in manual_errors:
        print(f"MANUAL_RESULTS_WARNING: {error}")

    events = {}
    if args.manual_only:
        print("Settlement mode: manual-only (scores API disabled).")
    else:
        sports = {r.get("sport", "") for r in rows if r.get("event_id")}
        for sport in sorted(sports):
            if not sport:
                continue
            try:
                for event in fetch_scores(sport):
                    if event.get("id"):
                        events[event.get("id")] = event
            except (requests.RequestException, RuntimeError, ValueError) as exc:
                print(f"{sport}: scores API unavailable: {exc}")

    rows = settle_rows(rows, events, manual_results=manual_results)
    if args.dry_run:
        print("Dry run: signals.csv was not modified.")
    else:
        save_rows(rows, fields)
        print(f"Updated settlement ledger: {SIGNALS_FILE}")
    report(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
