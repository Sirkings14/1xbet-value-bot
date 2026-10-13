"""Validate signal records before analytics and persistence.

This module is intentionally conservative: it checks data integrity but does not
reject a dataset merely because the sample is small. It is a research/paper-
trading guardrail and never places wagers.
"""

from __future__ import annotations

import csv
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


SIGNALS_FILE = Path(os.getenv("SIGNALS_FILE", "signals.csv"))
MAX_STAKE_PCT = float(os.getenv("MAX_STAKE_PCT", "2.0"))
EV_TOLERANCE_PP = float(os.getenv("EV_TOLERANCE_PP", "0.05"))
MAX_SHARP_PROB_SPREAD_PP = float(os.getenv("MAX_SHARP_PROB_SPREAD_PP", "8.0"))
SIGNAL_ID_RE = re.compile(r"^[0-9a-f]{24}$")
TOTALS_RE = re.compile(r"^(Over|Under)\s+\d+(?:\.\d+)?$")
VALID_STATUS = {"", "win", "loss", "push"}


def _text(row: dict[str, str], key: str) -> str:
    return (row.get(key) or "").strip()


def _number(row: dict[str, str], key: str) -> float | None:
    value = _text(row, key)
    if not value:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _integer(row: dict[str, str], key: str) -> int | None:
    value = _text(row, key)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _valid_timestamp(value: str) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def validate_row(row: dict[str, str], row_number: int) -> list[str]:
    errors: list[str] = []
    required = ("event_id", "sport", "market", "selection")
    for field in required:
        if not _text(row, field):
            errors.append(f"row {row_number}: missing {field}")

    odds = _number(row, "offered_odds")
    fair_odds = _number(row, "fair_odds")
    fair_probability = _number(row, "fair_probability")
    ev_percent = _number(row, "ev_percent")
    stake_pct = _number(row, "stake_pct")
    sharp_books = _integer(row, "sharp_books")
    sharp_sample_count = _integer(row, "sharp_sample_count")

    if odds is None or odds <= 1:
        errors.append(f"row {row_number}: offered_odds must be finite and > 1")
    if fair_odds is None or fair_odds <= 1:
        errors.append(f"row {row_number}: fair_odds must be finite and > 1")
    if fair_probability is None or not 0 < fair_probability < 1:
        errors.append(f"row {row_number}: fair_probability must be between 0 and 1")
    if ev_percent is None:
        errors.append(f"row {row_number}: ev_percent must be finite")
    elif odds is not None and fair_probability is not None:
        expected_ev = (fair_probability * odds - 1.0) * 100.0
        if abs(expected_ev - ev_percent) > EV_TOLERANCE_PP:
            errors.append(
                f"row {row_number}: ev_percent inconsistent with fair_probability and offered_odds"
            )

    if stake_pct is None or not 0 < stake_pct <= MAX_STAKE_PCT:
        errors.append(f"row {row_number}: stake_pct must be > 0 and <= {MAX_STAKE_PCT:g}")
    if sharp_books is None or sharp_books < 2:
        errors.append(f"row {row_number}: sharp_books must be an integer >= 2")
    if sharp_sample_count is None or sharp_sample_count < 2:
        errors.append(f"row {row_number}: sharp_sample_count must be an integer >= 2")
    elif sharp_books is not None and sharp_sample_count < sharp_books:
        errors.append(f"row {row_number}: sharp_sample_count cannot be below sharp_books")

    signal_id = _text(row, "signal_id")
    if signal_id:
        if not SIGNAL_ID_RE.fullmatch(signal_id):
            errors.append(f"row {row_number}: signal_id must be a 24-character lowercase hex ID")
        if not _text(row, "target_book"):
            errors.append(f"row {row_number}: identified signal requires target_book")
        if not _valid_timestamp(_text(row, "target_quote_updated_at")):
            errors.append(f"row {row_number}: identified signal requires timezone-aware target_quote_updated_at")
        book_keys = [item for item in _text(row, "sharp_book_keys").split("|") if item]
        if len(book_keys) < 2 or len(set(book_keys)) != len(book_keys):
            errors.append(f"row {row_number}: identified signal requires unique sharp_book_keys")
        elif sharp_books is not None and len(book_keys) != sharp_books:
            errors.append(f"row {row_number}: sharp_book_keys count does not match sharp_books")

        sharp_quotes = [item for item in _text(row, "sharp_quote_timestamps").split("|") if item]
        quote_map = {}
        for item in sharp_quotes:
            if "=" not in item:
                errors.append(f"row {row_number}: malformed sharp_quote_timestamps entry")
                continue
            book, timestamp = item.split("=", 1)
            if not book or book in quote_map:
                errors.append(f"row {row_number}: sharp quote timestamps must have unique bookmaker keys")
                continue
            quote_map[book] = timestamp
            if not _valid_timestamp(timestamp):
                errors.append(f"row {row_number}: sharp quote timestamp for '{book}' must be timezone-aware")
        if set(quote_map) != set(book_keys):
            errors.append(f"row {row_number}: sharp quote timestamp keys must match sharp_book_keys")

        spread = _number(row, "sharp_probability_spread_pp")
        if spread is None or spread < 0:
            errors.append(f"row {row_number}: identified signal requires finite non-negative probability spread")
        elif spread > MAX_SHARP_PROB_SPREAD_PP:
            errors.append(
                f"row {row_number}: probability spread exceeds {MAX_SHARP_PROB_SPREAD_PP:g} percentage-point guard"
            )

    market = _text(row, "market").lower()
    selection = _text(row, "selection")
    if market == "totals":
        if not TOTALS_RE.fullmatch(selection):
            errors.append(f"row {row_number}: totals selection must look like 'Over 2.5' or 'Under 3.0'")
    elif market == "h2h" and TOTALS_RE.fullmatch(selection):
        errors.append(f"row {row_number}: h2h selection cannot be a totals line")

    status = _text(row, "status").lower()
    if status not in VALID_STATUS:
        errors.append(f"row {row_number}: invalid status '{status}'")
    if status in {"win", "loss", "push"}:
        profit = _number(row, "profit_units")
        if profit is None:
            errors.append(f"row {row_number}: settled row requires finite profit_units")
        if not _valid_timestamp(_text(row, "settled_at")):
            errors.append(f"row {row_number}: settled row requires ISO timestamp settled_at")

        method = _text(row, "settlement_method")
        if method and method not in {"scores_api", "manual_verified"}:
            errors.append(f"row {row_number}: invalid settlement_method '{method}'")
        if signal_id and not method:
            errors.append(f"row {row_number}: identified settled signal requires settlement_method")
        if method == "manual_verified":
            if not _text(row, "settlement_source"):
                errors.append(f"row {row_number}: manual settlement requires settlement_source")
            source_url = _text(row, "settlement_source_url")
            if not source_url.lower().startswith(("http://", "https://")):
                errors.append(f"row {row_number}: manual settlement requires an http(s) source URL")
            if not _valid_timestamp(_text(row, "settlement_verified_at")):
                errors.append(f"row {row_number}: manual settlement requires timezone-aware settlement_verified_at")
        elif method == "scores_api":
            if not _text(row, "settlement_source"):
                errors.append(f"row {row_number}: API settlement requires settlement_source")
            if not _valid_timestamp(_text(row, "settlement_verified_at")):
                errors.append(f"row {row_number}: API settlement requires timezone-aware settlement_verified_at")

    return errors


def validate_file(path: Path = SIGNALS_FILE) -> list[str]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return ["signals.csv: missing CSV header"]
        required = {"event_id", "sport", "market", "selection", "offered_odds", "fair_odds", "fair_probability", "ev_percent", "stake_pct", "sharp_books", "sharp_sample_count"}
        missing = sorted(required - set(reader.fieldnames))
        if missing:
            return [f"signals.csv: missing columns: {', '.join(missing)}"]
        errors: list[str] = []
        seen_signal_ids: set[str] = set()
        for row_number, row in enumerate(reader, start=2):
            errors.extend(validate_row(row, row_number))
            signal_id = _text(row, "signal_id")
            if signal_id:
                if signal_id in seen_signal_ids:
                    errors.append(f"row {row_number}: duplicate signal_id '{signal_id}'")
                seen_signal_ids.add(signal_id)
        return errors


def main() -> int:
    if not SIGNALS_FILE.exists():
        print("No signals to validate.")
        return 0
    errors = validate_file(SIGNALS_FILE)
    if errors:
        print(f"Signal quality gate FAILED: {len(errors)} issue(s).")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Signal quality gate PASSED: {SIGNALS_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
