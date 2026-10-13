import csv
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SIGNALS_FILE = Path("signals.csv")
CLV_FILE = Path("clv_snapshots.csv")
REPORT_FILE = Path("analysis_report.csv")


def _num(row, key, default=0.0):
    try:
        return float(row.get(key, default) or default)
    except (TypeError, ValueError):
        return float(default)


def load_signals(path=SIGNALS_FILE):
    if not Path(path).exists():
        return []
    with Path(path).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_clv(path=CLV_FILE):
    if not Path(path).exists():
        return []
    with Path(path).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def settled_rows(rows):
    return [r for r in rows if str(r.get("status", "")).strip().lower() in {"win", "loss", "push"}]


def _parse_aware_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def pending_summary(rows, now=None):
    """Count unresolved signals and identify those whose scheduled kickoff has passed."""
    now = now or datetime.now(timezone.utc)
    pending = [
        row for row in rows
        if str(row.get("status", "")).strip().lower() not in {"win", "loss", "push"}
    ]
    overdue_ages = []
    for row in pending:
        kickoff = _parse_aware_time(row.get("commence_time", ""))
        if kickoff is not None and kickoff < now:
            overdue_ages.append((now - kickoff).total_seconds() / 3600.0)
    return {
        "pending": len(pending),
        "overdue_pending": len(overdue_ages),
        "oldest_pending_age_hours": round(max(overdue_ages), 2) if overdue_ages else 0.0,
    }


def binary_rows(rows):
    return [r for r in settled_rows(rows) if str(r.get("status", "")).lower() in {"win", "loss"}]


def max_losing_streak(rows):
    # Outcomes must be evaluated in settlement order, not signal creation order.
    ordered = sorted(rows, key=lambda r: (r.get("settled_at", ""), r.get("signal_id", "")))
    streak = best = 0
    for row in ordered:
        status = str(row.get("status", "")).strip().lower()
        if status == "loss":
            streak += 1
            best = max(best, streak)
        elif status in {"win", "push"}:
            streak = 0
    return best


def cumulative_profit(rows):
    ordered = sorted(rows, key=lambda r: (r.get("settled_at", ""), r.get("signal_id", "")))
    cumulative = []
    total = 0.0
    for row in ordered:
        total += _num(row, "profit_units")
        cumulative.append(total)
    return cumulative


def max_drawdown(rows):
    peak = 0.0
    drawdown = 0.0
    for value in cumulative_profit(rows):
        peak = max(peak, value)
        drawdown = max(drawdown, peak - value)
    return round(drawdown, 6)


def brier_score(rows):
    binary = binary_rows(rows)
    if not binary:
        return 0.0
    total = 0.0
    for row in binary:
        predicted = min(max(_num(row, "fair_probability"), 0.0), 1.0)
        actual = 1.0 if str(row.get("status", "")).lower() == "win" else 0.0
        total += (predicted - actual) ** 2
    return round(total / len(binary), 6)


def calibration_buckets(rows):
    buckets = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.000001)]
    output = []
    for low, high in buckets:
        selected = [r for r in binary_rows(rows) if low <= _num(r, "fair_probability") < high]
        if not selected:
            continue
        predicted = sum(_num(r, "fair_probability") for r in selected) / len(selected)
        actual = sum(str(r.get("status", "")).lower() == "win" for r in selected) / len(selected)
        output.append({"group": f"{low:.1f}-{min(high, 1.0):.1f}", "count": len(selected), "avg_predicted": round(predicted, 6), "actual_win_rate": round(actual, 6), "calibration_error": round(abs(predicted - actual), 6)})
    return output


def clv_summary(rows):
    values = []
    for row in rows:
        try:
            value = float(row.get("clv_percent", ""))
            if value == value and abs(value) != float("inf"):
                values.append(value)
        except (TypeError, ValueError):
            continue
    if not values:
        return {"clv_snapshots": 0, "avg_clv_percent": 0.0, "positive_clv_percent": 0.0}
    return {"clv_snapshots": len(values), "avg_clv_percent": round(sum(values) / len(values), 6), "positive_clv_percent": round(sum(v > 0 for v in values) / len(values) * 100.0, 4)}


def summary(rows, now=None):
    settled = settled_rows(rows)
    wins = sum(str(r.get("status", "")).lower() == "win" for r in settled)
    losses = sum(str(r.get("status", "")).lower() == "loss" for r in settled)
    pushes = sum(str(r.get("status", "")).lower() == "push" for r in settled)
    profit = sum(_num(r, "profit_units") for r in settled)
    staked = sum(_num(r, "stake_pct") for r in settled)
    roi = (profit / staked * 100.0) if staked else 0.0
    return {
        "signals": len(rows), "settled": len(settled), "wins": wins, "losses": losses, "pushes": pushes,
        **pending_summary(rows, now=now),
        "profit_units": round(profit, 6), "staked_pct_units": round(staked, 6), "roi_percent": round(roi, 4),
        "win_rate_percent": round(wins / (wins + losses) * 100.0, 4) if wins + losses else 0.0,
        "max_losing_streak": max_losing_streak(settled), "max_drawdown_units": max_drawdown(settled),
        "brier_score": brier_score(settled),
        "avg_odds": round(sum(_num(r, "offered_odds") for r in settled) / len(settled), 4) if settled else 0.0,
        "avg_ev_percent": round(sum(_num(r, "ev_percent") for r in settled) / len(settled), 4) if settled else 0.0,
        "avg_fair_probability": round(sum(_num(r, "fair_probability") for r in settled) / len(settled), 6) if settled else 0.0,
    }


def _bucket_ev(ev):
    if ev < 5:
        return "3-5"
    if ev < 8:
        return "5-8"
    return "8+"


def _bucket_odds(odds):
    if odds < 1.75:
        return "1.35-1.75"
    if odds < 2.25:
        return "1.75-2.25"
    if odds < 3.0:
        return "2.25-3.00"
    return "3.00-4.50"


def grouped_report(rows, field, bucket=None):
    groups = defaultdict(list)
    for row in rows:
        value = _num(row, field) if bucket else row.get(field, "unknown") or "unknown"
        key = bucket(value) if bucket else value
        groups[key].append(row)
    return [{"group": key, **summary(groups[key])} for key in sorted(groups, key=str)]


def build_report(rows, clv_rows=None):
    sections = []
    overall = {"section": "overall", "group": "all", **summary(rows)}
    overall.update(clv_summary(clv_rows or []))
    sections.append(overall)
    for section, field, bucket in [
        ("sport", "sport", None), ("market", "market", None),
        ("signal_version", "signal_version", None),
        ("ev_bucket", "ev_percent", _bucket_ev), ("odds_bucket", "offered_odds", _bucket_odds),
    ]:
        for item in grouped_report(rows, field, bucket):
            sections.append({"section": section, **item})
    return sections


def save_report(report, path=REPORT_FILE):
    fields = [
        "section", "group", "signals", "settled", "pending", "overdue_pending", "oldest_pending_age_hours",
        "wins", "losses", "pushes", "profit_units", "staked_pct_units",
        "roi_percent", "win_rate_percent", "max_losing_streak", "max_drawdown_units", "brier_score", "avg_odds",
        "avg_ev_percent", "avg_fair_probability", "clv_snapshots", "avg_clv_percent", "positive_clv_percent",
    ]
    with Path(path).open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report)


def print_report(report):
    overall = next((r for r in report if r["section"] == "overall"), None)
    if not overall:
        print("No settled signals yet.")
        return
    print("=== PAPER-TRADING PERFORMANCE ===")
    print(f"Settled: {overall['settled']} | Pending: {overall['pending']} | W/L/P: {overall['wins']}/{overall['losses']}/{overall['pushes']}")
    print(f"Overdue pending: {overall['overdue_pending']} | Oldest pending: {overall['oldest_pending_age_hours']:.2f} hours")
    print(f"P/L units: {overall['profit_units']:.4f} | ROI: {overall['roi_percent']:.2f}%")
    print(f"Win rate: {overall['win_rate_percent']:.2f}% | Max losing streak: {overall['max_losing_streak']}")
    print(f"Drawdown: {overall['max_drawdown_units']:.4f} units | Brier: {overall['brier_score']:.4f}")
    print(f"Avg odds: {overall['avg_odds']:.3f} | Avg EV: {overall['avg_ev_percent']:.2f}%")
    print(f"CLV snapshots: {overall['clv_snapshots']} | Avg CLV: {overall['avg_clv_percent']:.3f}% | Positive CLV: {overall['positive_clv_percent']:.2f}%")
    cohorts = [row for row in report if row.get("section") == "signal_version"]
    if cohorts:
        print("=== BY SIGNAL VERSION ===")
        for row in cohorts:
            print(
                f"{row['group']}: signals={row['signals']} | settled={row['settled']} | "
                f"pending={row['pending']} | overdue={row['overdue_pending']} | ROI={row['roi_percent']:.2f}%"
            )


def main():
    rows = load_signals()
    report = build_report(rows, load_clv())
    save_report(report)
    print_report(report)


if __name__ == "__main__":
    main()
