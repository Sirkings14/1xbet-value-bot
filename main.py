import csv
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

import requests

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Risk controls. This bot produces research alerts only; it never places wagers.
MIN_ODDS = float(os.getenv("MIN_ODDS", "1.35"))
MAX_ODDS = float(os.getenv("MAX_ODDS", "4.50"))
MIN_EV = float(os.getenv("MIN_EV", "3.0"))
MAX_STAKE_PCT = float(os.getenv("MAX_STAKE_PCT", "2.0"))
KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", "0.25"))
MIN_SHARP_BOOKS = int(os.getenv("MIN_SHARP_BOOKS", "2"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))

CACHE_FILE = Path("sent_alerts_cache.csv")
SIGNALS_FILE = Path("signals.csv")

SPORTS_TO_SCAN = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_uefa_champs_league",
    "soccer_usa_mls",
    "tennis_atp_us_open",
    "baseball_mlb",
]

SHARP_BOOKS = {"pinnacle", "betfair_ex_uk", "unibet_eu"}
RETAIL_BOOKS = {"1xbet", "1x", "marathonbet", "bet365"}


def parse_time(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def normalise_name(name, point=None):
    name = str(name).strip()
    return f"{name} {point}" if point is not None else name


def devig_shin(odds):
    """Return Shin no-vig probabilities for a complete mutually-exclusive market.

    Uses the standard Shin insider-trading model and solves for z by bisection.
    Falls back to proportional normalization if the numerical solution is invalid.
    """
    valid = [float(o) for o in odds if float(o) > 1.0 and math.isfinite(float(o))]
    if len(valid) < 2:
        return []

    q = [1.0 / o for o in valid]
    overround = sum(q)
    if overround <= 1.0:
        return [x / overround for x in q]

    # Shin's z lies between 0 and 1. Solve sum(p_i(z)) = 1.
    def probs(z):
        result = []
        denom = 1.0 - z
        for qi in q:
            radicand = z * z + 4.0 * denom * qi * qi / overround
            result.append((math.sqrt(max(0.0, radicand)) - z) / (2.0 * denom))
        return result

    lo, hi = 0.0, min(0.999999, 1.0 - 1e-9)
    for _ in range(80):
        mid = (lo + hi) / 2.0
        total = sum(probs(mid))
        if total > 1.0:
            lo = mid
        else:
            hi = mid

    result = probs((lo + hi) / 2.0)
    total = sum(result)
    if total <= 0 or not all(math.isfinite(x) for x in result):
        return [x / overround for x in q]
    return [x / total for x in result]


def fractional_kelly(probability, odds):
    """Quarter-Kelly stake, capped by MAX_STAKE_PCT."""
    p = float(probability)
    b = float(odds) - 1.0
    if p <= 0.0 or b <= 0.0 or p >= 1.0:
        return 0.0
    full_kelly = (p * b - (1.0 - p)) / b
    if full_kelly <= 0.0:
        return 0.0
    return round(min(full_kelly * KELLY_FRACTION * 100.0, MAX_STAKE_PCT), 3)


def load_cache():
    if not CACHE_FILE.exists():
        return set()
    try:
        with CACHE_FILE.open(newline="", encoding="utf-8") as fh:
            return {row["alert_id"] for row in csv.DictReader(fh) if row.get("alert_id")}
    except (OSError, KeyError, csv.Error):
        return set()


def save_cache(cache):
    with CACHE_FILE.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["alert_id"])
        writer.writeheader()
        for alert_id in sorted(cache):
            writer.writerow({"alert_id": alert_id})


def append_signal(signal):
    exists = SIGNALS_FILE.exists()
    fields = [
        "timestamp", "event_id", "sport", "match", "market", "selection",
        "offered_odds", "fair_odds", "fair_probability", "ev_percent",
        "stake_pct", "sharp_books", "sharp_sample_count",
    ]
    with SIGNALS_FILE.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow(signal)


def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured; alert printed locally.")
        print(message)
        return True

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": True,
    }
    try:
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        print(f"Telegram error: {exc}")
        return False


def format_alert(event, market, selection, offered, fair_odds, probability, ev, stake, sharp_count):
    home = event.get("home_team", "Home")
    away = event.get("away_team", "Away")
    start = event.get("commence_time", "unknown")
    return (
        "VALUE SIGNAL\n"
        "--------------------\n"
        f"{home} vs {away}\n"
        f"Market: {market}\n"
        f"Selection: {selection}\n"
        f"Start: {start}\n"
        "--------------------\n"
        f"1xBet odds: {offered:.2f}\n"
        f"Estimated fair odds: {fair_odds:.2f}\n"
        f"Model probability: {probability * 100:.2f}%\n"
        f"Estimated EV: +{ev:.2f}%\n"
        f"Fractional Kelly: {stake:.3f}%\n"
        f"Sharp-book observations: {sharp_count}\n"
        "--------------------\n"
        "Research alert only — verify the live line before acting."
    )


def extract_market_prices(event, market_key):
    sharp = {}
    retail = {}

    for bookmaker in event.get("bookmakers", []):
        key = bookmaker.get("key", "").lower()
        for market in bookmaker.get("markets", []):
            if market.get("key") != market_key:
                continue
            for outcome in market.get("outcomes", []):
                if "price" not in outcome or "name" not in outcome:
                    continue
                try:
                    price = float(outcome["price"])
                except (TypeError, ValueError):
                    continue
                if price <= 1.0 or not math.isfinite(price):
                    continue
                name = normalise_name(outcome["name"], outcome.get("point"))
                if key in SHARP_BOOKS:
                    sharp.setdefault(name, []).append((key, price))
                if key in RETAIL_BOOKS:
                    retail[name] = price

    return sharp, retail


def scan_event(event, sent_cache):
    event_id = event.get("id") or f"{event.get('home_team')}|{event.get('away_team')}|{event.get('commence_time')}"

    for market_key in ("h2h", "totals"):
        sharp, retail = extract_market_prices(event, market_key)
        if not sharp or not retail:
            continue

        # A market is de-vigged only when the sharp set contains a full outcome set.
        outcome_names = list(sharp.keys())
        if len(outcome_names) < 2:
            continue
        if market_key == "h2h" and len(outcome_names) > 3:
            continue

        # Use the median sharp price for each outcome to reduce one-book noise.
        sharp_odds = [median([price for _, price in sharp[name]]) for name in outcome_names]
        if any(o <= 1.0 for o in sharp_odds):
            continue

        fair_probs = devig_shin(sharp_odds)
        if len(fair_probs) != len(outcome_names):
            continue

        sharp_book_count = len({book for name in outcome_names for book, _ in sharp[name]})
        if sharp_book_count < MIN_SHARP_BOOKS:
            continue

        for name, probability in zip(outcome_names, fair_probs):
            offered = retail.get(name)
            if offered is None or not (MIN_ODDS <= offered <= MAX_ODDS):
                continue

            ev = (probability * offered - 1.0) * 100.0
            if ev < MIN_EV:
                continue

            stake = fractional_kelly(probability, offered)
            if stake <= 0:
                continue

            # Stable ID prevents repeated alerts every time the same line is polled.
            alert_id = f"{event_id}|{market_key}|{name}"
            if alert_id in sent_cache:
                continue

            fair_odds = 1.0 / probability
            message = format_alert(
                event, market_key, name, offered, fair_odds, probability,
                ev, stake, sharp_book_count,
            )

            if send_telegram(message):
                sent_cache.add(alert_id)
                append_signal({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event_id": event_id,
                    "sport": event.get("sport_key", ""),
                    "match": f"{event.get('home_team', '')} vs {event.get('away_team', '')}",
                    "market": market_key,
                    "selection": name,
                    "offered_odds": round(offered, 4),
                    "fair_odds": round(fair_odds, 4),
                    "fair_probability": round(probability, 6),
                    "ev_percent": round(ev, 4),
                    "stake_pct": stake,
                    "sharp_books": sharp_book_count,
                    "sharp_sample_count": sum(len(sharp[n]) for n in outcome_names),
                })


def fetch_sport(sport_key):
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY is not configured")

    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu,uk",
        "markets": "h2h,totals",
        "oddsFormat": "decimal",
    }
    response = requests.get(
        f"{ODDS_API_BASE}/sports/{sport_key}/odds/",
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def run():
    print("=== 1xBet VALUE ENGINE v2 ===")
    cache = load_cache()
    total_events = 0

    for sport in SPORTS_TO_SCAN:
        try:
            events = fetch_sport(sport)
            total_events += len(events)
            print(f"{sport}: {len(events)} events")
            for event in events:
                scan_event(event, cache)
        except requests.RequestException as exc:
            print(f"{sport}: API error: {exc}")
        except Exception as exc:
            print(f"{sport}: scanner error: {exc}")

    save_cache(cache)
    print(f"=== COMPLETE: {total_events} events scanned ===")


if __name__ == "__main__":
    run()
