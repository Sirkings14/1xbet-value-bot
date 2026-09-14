"""Capture pre-start 1xBet prices and measure paper-trading CLV."""

from __future__ import annotations

import csv
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from main import extract_bookmaker_markets, find_target_price

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
CLV_MINUTES_BEFORE_START = float(os.getenv("CLV_MINUTES_BEFORE_START", "10"))
SIGNALS_FILE = Path(os.getenv("SIGNALS_FILE", "signals.csv"))
CLV_FILE = Path(os.getenv("CLV_FILE", "clv_snapshots.csv"))


def parse_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def clv_percent(signal_odds, reference_odds):
    signal = float(signal_odds)
    reference = float(reference_odds)
    if signal <= 1 or reference <= 1 or not math.isfinite(signal) or not math.isfinite(reference):
        raise ValueError("odds must be finite and > 1")
    return round((signal / reference - 1.0) * 100.0, 6)


def snapshot_due(row, now=None):
    start = parse_time(row.get("commence_time", ""))
    if start is None:
        return False
    now = now or datetime.now(timezone.utc)
    minutes = (start - now).total_seconds() / 60.0
    return 0 <= minutes <= CLV_MINUTES_BEFORE_START


def load_signals():
    if not SIGNALS_FILE.exists():
        return []
    with SIGNALS_FILE.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_snapshots():
    if not CLV_FILE.exists():
        return []
    with CLV_FILE.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def save_snapshots(rows):
    fields = ["snapshot_id", "timestamp", "signal_timestamp", "event_id", "sport", "market", "selection", "signal_odds", "reference_odds", "clv_percent"]
    with CLV_FILE.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def fetch_sport(sport_key):
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY is not configured")
    response = requests.get(
        f"{ODDS_API_BASE}/sports/{sport_key}/odds/",
        params={"apiKey": ODDS_API_KEY, "regions": "eu,uk", "markets": "h2h,totals", "oddsFormat": "decimal"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def build_event_index(events):
    return {event.get("id"): event for event in events if event.get("id")}


def capture_signal(signal, event):
    market_key = signal.get("market")
    if market_key not in {"h2h", "totals"}:
        return None
    _, retail = extract_bookmaker_markets(event, market_key)
    target = find_target_price(retail, signal.get("selection", ""))
    if not target:
        return None
    _, reference = target
    signal_odds = float(signal.get("offered_odds"))
    return {
        "snapshot_id": f"{signal.get('timestamp','')}|{signal.get('event_id','')}|{market_key}|{signal.get('selection','')}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "signal_timestamp": signal.get("timestamp", ""),
        "event_id": signal.get("event_id", ""),
        "sport": signal.get("sport", ""),
        "market": market_key,
        "selection": signal.get("selection", ""),
        "signal_odds": signal_odds,
        "reference_odds": float(reference),
        "clv_percent": clv_percent(signal_odds, reference),
    }


def capture():
    signals = [row for row in load_signals() if snapshot_due(row)]
    if not signals:
        print("No signals currently inside the CLV capture window.")
        return
    existing = load_snapshots()
    existing_ids = {row.get("snapshot_id") for row in existing}
    events_by_id = {}
    for sport in sorted({row.get("sport", "") for row in signals if row.get("sport")}):
        try:
            events_by_id.update(build_event_index(fetch_sport(sport)))
        except requests.RequestException as exc:
            print(f"{sport}: CLV API error: {exc}")
    captured = 0
    for signal in signals:
        event = events_by_id.get(signal.get("event_id"))
        if not event or event.get("completed") is True:
            continue
        snapshot_id = f"{signal.get('timestamp','')}|{signal.get('event_id','')}|{signal.get('market','')}|{signal.get('selection','')}"
        if snapshot_id in existing_ids:
            continue
        try:
            snapshot = capture_signal(signal, event)
        except (TypeError, ValueError):
            snapshot = None
        if snapshot:
            existing.append(snapshot)
            existing_ids.add(snapshot["snapshot_id"])
            captured += 1
    save_snapshots(existing)
    print(f"CLV snapshots captured: {captured}")


def summary(rows):
    values = []
    for row in rows:
        try:
            value = float(row.get("clv_percent", ""))
            if math.isfinite(value):
                values.append(value)
        except (TypeError, ValueError):
            continue
    if not values:
        return {"snapshots": 0, "avg_clv_percent": 0.0, "positive_clv_percent": 0.0}
    return {"snapshots": len(values), "avg_clv_percent": round(sum(values) / len(values), 6), "positive_clv_percent": round(sum(v > 0 for v in values) / len(values) * 100.0, 4)}


def main():
    capture()
    result = summary(load_snapshots())
    print(f"CLV snapshots: {result['snapshots']}")
    print(f"Average CLV: {result['avg_clv_percent']:.4f}% | Positive CLV: {result['positive_clv_percent']:.2f}%")


if __name__ == "__main__":
    main()
