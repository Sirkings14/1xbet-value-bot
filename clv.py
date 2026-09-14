"""Paper-trading closing-line-value (CLV) capture and reporting.

A signal's original price is compared with a later pre-start market snapshot.
This is measurement only: it never places wagers and never invents a closing
price when a suitable snapshot is unavailable.
"""

from __future__ import annotations

import csv
import math
import os
from datetime import datetime, timezone
from pathlib import Path

SIGNALS_FILE = Path(os.getenv("SIGNALS_FILE", "signals.csv"))
CLV_FILE = Path(os.getenv("CLV_FILE", "clv_snapshots.csv"))
CLV_MINUTES_BEFORE_START = float(os.getenv("CLV_MINUTES_BEFORE_START", "5"))


def parse_time(value: str):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def clv_percent(signal_odds: float, reference_odds: float) -> float:
    """Return return-based CLV: later implied price vs signal price.

    Positive means the later market price became less generous to the bettor
    than the original signal price, which is the desired direction for a
    back-selection signal.
    """
    signal = float(signal_odds)
    reference = float(reference_odds)
    if signal <= 1 or reference <= 1 or not math.isfinite(signal) or not math.isfinite(reference):
        raise ValueError("odds must be finite and > 1")
    return round((signal / reference - 1.0) * 100.0, 6)


def snapshot_due(row, now=None) -> bool:
    start = parse_time(row.get("commence_time", ""))
    if start is None:
        return False
    now = now or datetime.now(timezone.utc)
    minutes = (start - now).total_seconds() / 60.0
    return 0 <= minutes <= CLV_MINUTES_BEFORE_START


def build_snapshot(signal, reference_odds, captured_at=None):
    """Build a CLV record from an observed pre-start price."""
    signal_odds = float(signal["offered_odds"])
    reference_odds = float(reference_odds)
    captured = captured_at or datetime.now(timezone.utc).isoformat()
    return {
        "timestamp": captured,
        "signal_timestamp": signal.get("timestamp", ""),
        "event_id": signal.get("event_id", ""),
        "sport": signal.get("sport", ""),
        "market": signal.get("market", ""),
        "selection": signal.get("selection", ""),
        "signal_odds": signal_odds,
        "reference_odds": reference_odds,
        "clv_percent": clv_percent(signal_odds, reference_odds),
    }


def save_snapshots(rows, path=CLV_FILE):
    fields = ["timestamp", "signal_timestamp", "event_id", "sport", "market", "selection", "signal_odds", "reference_odds", "clv_percent"]
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def load_snapshots(path=CLV_FILE):
    if not Path(path).exists():
        return []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


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
    return {
        "snapshots": len(values),
        "avg_clv_percent": round(sum(values) / len(values), 6),
        "positive_clv_percent": round(sum(v > 0 for v in values) / len(values) * 100.0, 4),
    }


def main():
    rows = load_snapshots()
    result = summary(rows)
    print(f"CLV snapshots: {result['snapshots']}")
    print(f"Average CLV: {result['avg_clv_percent']:.4f}% | Positive CLV: {result['positive_clv_percent']:.2f}%")


if __name__ == "__main__":
    main()
