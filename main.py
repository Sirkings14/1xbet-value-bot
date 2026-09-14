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

MIN_ODDS = float(os.getenv("MIN_ODDS", "1.35"))
MAX_ODDS = float(os.getenv("MAX_ODDS", "4.50"))
MIN_EV = float(os.getenv("MIN_EV", "3.0"))
MAX_STAKE_PCT = float(os.getenv("MAX_STAKE_PCT", "2.0"))
KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", "0.25"))
MIN_SHARP_BOOKS = int(os.getenv("MIN_SHARP_BOOKS", "2"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
MAX_EVENT_AGE_MINUTES = float(os.getenv("MAX_EVENT_AGE_MINUTES", "0"))

# Re-alert only when a previously alerted line materially improves.
RE_ALERT_EV_DELTA = float(os.getenv("RE_ALERT_EV_DELTA", "1.0"))
RE_ALERT_ODDS_DELTA = float(os.getenv("RE_ALERT_ODDS_DELTA", "0.05"))

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
TARGET_RETAIL_BOOKS = {"1xbet", "1x"}


def normalise_name(name, point=None):
    name = str(name).strip()
    return f"{name} {point}" if point is not None else name


def parse_event_time(value):
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_event_eligible(event, now=None):
    """Allow only upcoming events, with an optional grace period for bad feeds."""
    start = parse_event_time(event.get("commence_time"))
    if start is None:
        return False
    now = now or datetime.now(timezone.utc)
    age_minutes = (now - start).total_seconds() / 60.0
    return age_minutes <= MAX_EVENT_AGE_MINUTES


def devig_shin(odds):
    """Return Shin no-vig probabilities for a complete market."""
    try:
        valid = [float(o) for o in odds if float(o) > 1.0 and math.isfinite(float(o))]
    except (TypeError, ValueError):
        return []
    if len(valid) < 2:
        return []

    q = [1.0 / o for o in valid]
    overround = sum(q)
    if overround <= 1.0:
        return [x / overround for x in q]

    def probs(z):
        result = []
        denom = 1.0 - z
        for qi in q:
            radicand = z * z + 4.0 * denom * qi * qi / overround
            result.append((math.sqrt(max(0.0, radicand)) - z) / (2.0 * denom))
        return result

    lo, hi = 0.0, 1.0 - 1e-9
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if sum(probs(mid)) > 1.0:
            lo = mid
        else:
            hi = mid

    result = probs((lo + hi) / 2.0)
    total = sum(result)
    if total <= 0 or not all(math.isfinite(x) for x in result):
        return [x / overround for x in q]
    return [x / total for x in result]


def fractional_kelly(probability, odds):
    p = float(probability)
    b = float(odds) - 1.0
    if p <= 0.0 or b <= 0.0 or p >= 1.0:
        return 0.0
    full_kelly = (p * b - (1.0 - p)) / b
    if full_kelly <= 0.0:
        return 0.0
    return round(min(full_kelly * KELLY_FRACTION * 100.0, MAX_STAKE_PCT), 3)


def load_cache():
    """Load alert state; accepts both v1 IDs and v2 state rows."""
    if not CACHE_FILE.exists():
        return {}
    try:
        with CACHE_FILE.open(newline="", encoding="utf-8") as fh:
            state = {}
            for row in csv.DictReader(fh):
                alert_id = row.get("alert_id")
                if alert_id:
                    state[alert_id] = {
                        "offered_odds": float(row.get("offered_odds", "0") or 0),
                        "ev_percent": float(row.get("ev_percent", "0") or 0),
                    }
            return state
    except (OSError, ValueError, KeyError, csv.Error):
        return {}


def save_cache(cache):
    with CACHE_FILE.open("w", newline="", encoding="utf-8") as fh:
        fields = ["alert_id", "offered_odds", "ev_percent", "updated_at"]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for alert_id, state in sorted(cache.items()):
            writer.writerow({
                "alert_id": alert_id,
                "offered_odds": state.get("offered_odds", 0),
                "ev_percent": state.get("ev_percent", 0),
                "updated_at": state.get("updated_at", ""),
            })


def append_signal(signal):
    fields = [
        "timestamp", "event_id", "sport", "match", "market", "selection",
        "offered_odds", "fair_odds", "fair_probability", "ev_percent",
        "stake_pct", "sharp_books", "sharp_sample_count",
    ]
    exists = SIGNALS_FILE.exists()
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

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        print(f"Telegram error: {exc}")
        return False


def format_alert(event, market, selection, offered, fair_odds, probability, ev, stake, sharp_books):
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
        f"Sharp books: {', '.join(sorted(sharp_books))}\n"
        "--------------------\n"
        "Research alert only — verify the live line before acting."
    )


def extract_bookmaker_markets(event, market_key):
    """Return bookmaker-specific market snapshots.

    Keeping books separate prevents an invalid market being built by mixing
    different outcome sets/lines from different sharp bookmakers.
    """
    sharp = []
    retail = []
    for bookmaker in event.get("bookmakers", []):
        key = bookmaker.get("key", "").lower()
        for market in bookmaker.get("markets", []):
            if market.get("key") != market_key:
                continue
            prices = {}
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
                prices[name] = price
            if not prices:
                continue
            snapshot = {"book": key, "prices": prices}
            if key in SHARP_BOOKS:
                sharp.append(snapshot)
            if key in TARGET_RETAIL_BOOKS:
                retail.append(snapshot)
    return sharp, retail


def market_groups(sharp_markets, market_key):
    """Group sharp snapshots by a complete outcome signature."""
    groups = {}
    for snapshot in sharp_markets:
        prices = snapshot["prices"]
        names = frozenset(prices)
        if len(names) < 2:
            continue
        if market_key == "h2h" and len(names) > 3:
            continue
        groups.setdefault(names, []).append(snapshot)
    return groups


def find_target_price(retail_markets, selection):
    """Prefer 1xBet and return its best currently listed price."""
    prices = []
    for snapshot in retail_markets:
        price = snapshot["prices"].get(selection)
        if price is not None:
            prices.append((snapshot["book"], price))
    if not prices:
        return None
    return max(prices, key=lambda item: item[1])


def should_alert(previous, offered, ev):
    if not previous:
        return True
    old_odds = previous.get("offered_odds", 0)
    old_ev = previous.get("ev_percent", 0)
    return offered >= old_odds + RE_ALERT_ODDS_DELTA or ev >= old_ev + RE_ALERT_EV_DELTA


def scan_event(event, sent_cache):
    if not is_event_eligible(event):
        return

    event_id = event.get("id") or f"{event.get('home_team')}|{event.get('away_team')}|{event.get('commence_time')}"

    for market_key in ("h2h", "totals"):
        sharp_markets, retail_markets = extract_bookmaker_markets(event, market_key)
        if not sharp_markets or not retail_markets:
            continue

        for outcome_names, snapshots in market_groups(sharp_markets, market_key).items():
            complete = [s for s in snapshots if outcome_names.issubset(s["prices"])]
            unique_books = {s["book"] for s in complete}
            if len(unique_books) < MIN_SHARP_BOOKS:
                continue

            probability_samples = {name: [] for name in outcome_names}
            for snapshot in complete:
                names = list(outcome_names)
                probs = devig_shin([snapshot["prices"][name] for name in names])
                if len(probs) != len(names):
                    continue
                for name, probability in zip(names, probs):
                    probability_samples[name].append(probability)

            if any(len(values) < MIN_SHARP_BOOKS for values in probability_samples.values()):
                continue

            fair_probs = {name: median(values) for name, values in probability_samples.items()}
            normaliser = sum(fair_probs.values())
            if normaliser <= 0:
                continue
            fair_probs = {name: p / normaliser for name, p in fair_probs.items()}

            for name, probability in fair_probs.items():
                target = find_target_price(retail_markets, name)
                if target is None:
                    continue
                _, offered = target
                if not (MIN_ODDS <= offered <= MAX_ODDS):
                    continue

                ev = (probability * offered - 1.0) * 100.0
                if ev < MIN_EV:
                    continue

                stake = fractional_kelly(probability, offered)
                if stake <= 0:
                    continue

                alert_id = f"{event_id}|{market_key}|{name}"
                if not should_alert(sent_cache.get(alert_id), offered, ev):
                    continue

                fair_odds = 1.0 / probability
                message = format_alert(event, market_key, name, offered, fair_odds, probability, ev, stake, unique_books)
                if send_telegram(message):
                    sent_cache[alert_id] = {
                        "offered_odds": offered,
                        "ev_percent": ev,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
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
                        "sharp_books": len(unique_books),
                        "sharp_sample_count": len(complete),
                    })


def fetch_sport(sport_key):
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY is not configured")
    response = requests.get(
        f"{ODDS_API_BASE}/sports/{sport_key}/odds/",
        params={
            "apiKey": ODDS_API_KEY,
            "regions": "eu,uk",
            "markets": "h2h,totals",
            "oddsFormat": "decimal",
        },
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
