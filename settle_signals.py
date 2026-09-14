import csv
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
SIGNALS_FILE = Path("signals.csv")


def parse_score(event):
    scores = event.get("scores") or []
    result = {}
    for item in scores:
        try:
            result[str(item["name"])] = float(item["score"])
        except (KeyError, TypeError, ValueError):
            continue
    return result


def settle_selection(row, event):
    """Return win/loss/push/None for a supported paper signal."""
    if not event.get("completed"):
        return None
    scores = parse_score(event)
    home = event.get("home_team")
    away = event.get("away_team")
    if home not in scores or away not in scores:
        return None

    market = row.get("market")
    selection = row.get("selection", "")
    home_score, away_score = scores[home], scores[away]

    if market == "h2h":
        if home_score == away_score:
            winner = "Draw"
        elif home_score > away_score:
            winner = home
        else:
            winner = away
        return "win" if selection == winner else "loss"

    if market == "totals":
        match = re.fullmatch(r"(Over|Under)\s+([0-9]+(?:\.[05])?)", selection)
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


def settle_rows(rows, events_by_id):
    updated = []
    for row in rows:
        if row.get("status") in {"win", "loss", "push"}:
            updated.append(row)
            continue
        event = events_by_id.get(row.get("event_id"))
        result = settle_selection(row, event) if event else None
        if result:
            stake = float(row.get("stake_pct") or 0)
            odds = float(row.get("offered_odds") or 0)
            if result == "win":
                profit = stake * (odds - 1.0)
            elif result == "loss":
                profit = -stake
            else:
                profit = 0.0
            row["status"] = result
            row["profit_units"] = f"{profit:.6f}"
            row["settled_at"] = datetime.now(timezone.utc).isoformat()
        updated.append(row)
    return updated


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


def load_rows():
    if not SIGNALS_FILE.exists():
        return [], []
    with SIGNALS_FILE.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return rows, rows[0].keys() if rows else []


def save_rows(rows, fields):
    fields = list(fields)
    for extra in ("status", "profit_units", "settled_at"):
        if extra not in fields:
            fields.append(extra)
    with SIGNALS_FILE.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def report(rows):
    settled = [r for r in rows if r.get("status") in {"win", "loss", "push"}]
    wins = sum(r.get("status") == "win" for r in settled)
    losses = sum(r.get("status") == "loss" for r in settled)
    pushes = sum(r.get("status") == "push" for r in settled)
    profit = sum(float(r.get("profit_units") or 0) for r in settled)
    turnover = sum(float(r.get("stake_pct") or 0) for r in settled)
    roi = (profit / turnover * 100.0) if turnover else 0.0
    print(f"Settled: {len(settled)} | Wins: {wins} | Losses: {losses} | Pushes: {pushes}")
    print(f"Paper P/L: {profit:.4f} stake-pct units | ROI: {roi:.2f}%")


def main():
    rows, fields = load_rows()
    if not rows:
        print("No signals.csv yet; nothing to settle.")
        return

    sports = {r.get("sport", "") for r in rows if r.get("event_id")}
    events = {}
    for sport in sorted(sports):
        if not sport:
            continue
        try:
            for event in fetch_scores(sport):
                events[event.get("id")] = event
        except requests.RequestException as exc:
            print(f"{sport}: scores API error: {exc}")

    rows = settle_rows(rows, events)
    save_rows(rows, fields)
    report(rows)


if __name__ == "__main__":
    main()
