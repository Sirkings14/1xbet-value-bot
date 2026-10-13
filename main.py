import csv
import hashlib
import math
import os
import re
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
MAX_SHARP_PROB_SPREAD_PP = float(os.getenv("MAX_SHARP_PROB_SPREAD_PP", "8.0"))
MAX_MARKET_AGE_SECONDS = float(os.getenv("MAX_MARKET_AGE_SECONDS", "900"))
SIGNAL_VERSION = "signal-integrity-v1"
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
MAX_EVENT_AGE_MINUTES = float(os.getenv("MAX_EVENT_AGE_MINUTES", "0"))
RE_ALERT_EV_DELTA = float(os.getenv("RE_ALERT_EV_DELTA", "1.0"))
RE_ALERT_ODDS_DELTA = float(os.getenv("RE_ALERT_ODDS_DELTA", "0.05"))

CACHE_FILE = Path("sent_alerts_cache.csv")
SIGNALS_FILE = Path("signals.csv")

# Free-tier mode: focus on the three soccer leagues with the most signal history.
# This reduces default odds polling to at most 6 credits per daily sweep when both
# markets are present for all three sports.
SPORTS_TO_SCAN = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_uefa_champs_league",
]
SHARP_BOOKS = {"pinnacle", "betfair_ex_uk", "unibet_eu"}
# Request only the bookmakers the model evaluates. The Odds API charges each
# group of up to 10 explicitly requested bookmakers as one region-equivalent.
API_BOOKMAKERS = ("pinnacle", "betfair_ex_uk", "unibet_eu", "onexbet")
API_MIN_REMAINING_CREDITS = int(os.getenv("API_MIN_REMAINING_CREDITS", "25"))


class ApiBudgetLow(RuntimeError):
    """Raised when the remaining provider quota reaches the protected reserve."""


# The Odds API's current EU bookmaker key for 1xBet is `onexbet`.
# Keep legacy aliases for compatibility with older cached/test data.
TARGET_RETAIL_BOOKS = {"onexbet", "1xbet", "1x"}


def log_api_quota(response, endpoint):
    """Expose provider quota headers in Actions logs without printing credentials."""
    remaining = response.headers.get("x-requests-remaining", "unknown")
    used = response.headers.get("x-requests-used", "unknown")
    last = response.headers.get("x-requests-last", "unknown")
    print(f"API_QUOTA endpoint={endpoint} last_cost={last} used={used} remaining={remaining}")


def api_budget_available(endpoint="api"):
    """Check quota via the provider's no-credit /sports endpoint; fail closed if unknown."""
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY is not configured")
    try:
        response = requests.get(
            f"{ODDS_API_BASE}/sports/",
            params={"apiKey": ODDS_API_KEY},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"API_BUDGET_CHECK_FAILED endpoint={endpoint}: {exc}; skipping paid-credit request")
        return False

    log_api_quota(response, f"budget-check:{endpoint}")
    raw_remaining = response.headers.get("x-requests-remaining")
    try:
        remaining = int(raw_remaining)
    except (TypeError, ValueError):
        print(f"API_BUDGET_UNKNOWN endpoint={endpoint}; skipping paid-credit request")
        return False

    if remaining <= API_MIN_REMAINING_CREDITS:
        print(
            f"API_BUDGET_HOLD endpoint={endpoint} remaining={remaining} "
            f"reserve={API_MIN_REMAINING_CREDITS}; skipping paid-credit request"
        )
        return False
    return True


def normalise_name(name, point=None):
    name = str(name).strip()
    if point is None:
        return name
    try:
        point_label = f"{float(point):g}"
    except (TypeError, ValueError):
        point_label = str(point).strip()
    return f"{name} {point_label}"


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
    start = parse_event_time(event.get("commence_time"))
    if start is None:
        return False
    now = now or datetime.now(timezone.utc)
    age_minutes = (now - start).total_seconds() / 60.0
    return age_minutes <= MAX_EVENT_AGE_MINUTES


def devig_shin(odds):
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


SIGNAL_FIELDS = [
    "timestamp", "event_id", "sport", "match", "commence_time", "market", "selection",
    "offered_odds", "fair_odds", "fair_probability", "ev_percent", "stake_pct",
    "sharp_books", "sharp_sample_count", "status", "profit_units", "settled_at",
    "signal_id", "signal_version", "target_book", "target_quote_updated_at", "sharp_book_keys",
    "sharp_quote_timestamps", "sharp_probability_spread_pp",
]


def build_signal_id(event_id, market, selection, offered_odds, fair_probability):
    """Stable ID for one distinct price/probability snapshot; safe to retry on delivery failure."""
    raw = "|".join([
        str(event_id), str(market), str(selection),
        f"{float(offered_odds):.4f}", f"{float(fair_probability):.6f}",
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def append_signal(signal, path=None):
    """Persist a research signal independently of Telegram and avoid duplicate snapshots."""
    path = Path(path) if path is not None else SIGNALS_FILE
    existing_rows = []
    existing_ids = set()
    fields = list(SIGNAL_FIELDS)

    if path.exists():
        with path.open("r", newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            existing_rows = list(reader)
            existing_fields = list(reader.fieldnames or [])
        fields = existing_fields + [field for field in SIGNAL_FIELDS if field not in existing_fields]
        existing_ids = {row.get("signal_id", "") for row in existing_rows if row.get("signal_id")}

        # Migrate older CSV headers before appending, so columns never shift.
        if fields != existing_fields:
            with path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(existing_rows)

    signal_id = str(signal.get("signal_id") or "")
    if signal_id and signal_id in existing_ids:
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    if not path.exists():
        fields = list(SIGNAL_FIELDS)
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow(signal)
    return True


def market_is_fresh(snapshot, now=None):
    """Fail closed when a bookmaker market has no usable or timezone-aware source timestamp."""
    raw = snapshot.get("last_update")
    if not raw:
        return False
    try:
        parsed = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        return False
    updated = parsed.astimezone(timezone.utc)
    now = now or datetime.now(timezone.utc)
    age_seconds = (now - updated).total_seconds()
    return -60.0 <= age_seconds <= MAX_MARKET_AGE_SECONDS


def unique_book_snapshots(snapshots, outcome_names):
    """Use one complete observation per bookmaker; exclude ambiguous duplicate rows."""
    by_book = {}
    required_names = set(outcome_names)
    for snapshot in snapshots:
        if not required_names.issubset(snapshot.get("prices", {})):
            continue
        by_book.setdefault(snapshot.get("book", ""), []).append(snapshot)
    unique, duplicate_books = [], []
    for book, observations in by_book.items():
        if not book or len(observations) != 1:
            if book:
                duplicate_books.append(book)
            continue
        unique.append(observations[0])
    return unique, sorted(duplicate_books)


def probability_spread_pp(probability_samples):
    spreads = []
    for values in probability_samples.values():
        if values:
            spreads.append((max(values) - min(values)) * 100.0)
    return max(spreads, default=0.0)


def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_DIAGNOSTIC: Telegram not configured (message printed locally, not delivered).")
        print(message)
        return None
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "disable_web_page_preview": True},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        print("TELEGRAM: message delivered successfully")
        return True
    except requests.RequestException as exc:
        print(f"TELEGRAM: delivery failed: {exc}")
        return False


def format_alert(event, market, selection, offered, fair_odds, probability, ev, stake, sharp_books,
                 target_book="", probability_spread_pp=0.0, target_quote_updated_at=""):
    home = event.get("home_team", "Home")
    away = event.get("away_team", "Away")
    start = event.get("commence_time", "unknown")
    return (
        "VALUE SIGNAL\n--------------------\n"
        f"{home} vs {away}\nMarket: {market}\nSelection: {selection}\nStart: {start}\n"
        "--------------------\n"
        f"1xBet odds: {offered:.2f}\nEstimated fair odds: {fair_odds:.2f}\n"
        f"Model probability: {probability * 100:.2f}%\nEstimated EV: +{ev:.2f}%\n"
        f"Fractional Kelly (paper sizing only): {stake:.3f}%\nTarget book: {target_book or 'unknown'}\n"
        f"Sharp books: {', '.join(sorted(sharp_books))}\n"
        f"Target quote updated: {target_quote_updated_at or 'unknown'}\n"
        f"Maximum sharp probability spread: {probability_spread_pp:.2f} percentage points\n"
        "--------------------\nResearch alert only — verify the live line before acting."
    )


def extract_bookmaker_markets(event, market_key):
    sharp, retail = [], []
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
            snapshot = {
                "book": key,
                "prices": prices,
                "last_update": market.get("last_update") or bookmaker.get("last_update"),
            }
            if key in SHARP_BOOKS:
                sharp.append(snapshot)
            if key in TARGET_RETAIL_BOOKS:
                retail.append(snapshot)
    return sharp, retail


def market_groups(sharp_markets, market_key):
    groups = {}
    for snapshot in sharp_markets:
        prices = snapshot["prices"]

        if market_key == "totals":
            # A bookmaker may expose several totals lines in one market response.
            # De-vig each Over/Under pair independently; never treat different
            # lines (e.g. 2.5 and 3.5 goals) as outcomes of one market.
            by_point = {}
            for selection, price in prices.items():
                match = re.fullmatch(
                    r"(Over|Under)\s+(-?\d+(?:\.\d+)?)",
                    str(selection),
                    flags=re.IGNORECASE,
                )
                if not match:
                    continue
                side = match.group(1).title()
                point = float(match.group(2))
                canonical_selection = f"{side} {point:g}"
                by_point.setdefault(point, {})[canonical_selection] = price

            for line_prices in by_point.values():
                names = frozenset(line_prices)
                if len(names) != 2:
                    continue
                if not any(name.startswith("Over ") for name in names):
                    continue
                if not any(name.startswith("Under ") for name in names):
                    continue
                groups.setdefault(names, []).append({
                    "book": snapshot["book"],
                    "prices": line_prices,
                    "last_update": snapshot.get("last_update"),
                })
            continue

        names = frozenset(prices)
        if len(names) < 2 or (market_key == "h2h" and len(names) > 3):
            continue
        groups.setdefault(names, []).append(snapshot)
    return groups


def valid_market_signature(sport_key, market_key, outcome_names):
    """Reject incomplete outcome sets before converting odds into probabilities."""
    names = set(outcome_names)

    if market_key == "totals":
        if len(names) != 2:
            return False
        has_over = any(name.startswith("Over ") for name in names)
        has_under = any(name.startswith("Under ") for name in names)
        return has_over and has_under

    if market_key == "h2h":
        if str(sport_key or "").lower().startswith("soccer_"):
            # Soccer head-to-head markets must include home, draw and away.
            return len(names) == 3 and any(name.strip().casefold() == "draw" for name in names)
        return 2 <= len(names) <= 3

    return False


def find_target_quote(retail_markets, selection):
    """Return the best eligible target quote with its originating snapshot metadata."""
    candidates = []
    for snapshot in retail_markets:
        price = snapshot.get("prices", {}).get(selection)
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        if price > 1.0 and math.isfinite(price):
            candidates.append((price, snapshot))
    if not candidates:
        return None
    price, snapshot = max(candidates, key=lambda item: item[0])
    return snapshot, price


def find_target_price(retail_markets, selection):
    """Compatibility helper returning (book, price) while preserving the source-aware quote path."""
    quote = find_target_quote(retail_markets, selection)
    if quote is None:
        return None
    snapshot, price = quote
    return snapshot["book"], price


def should_alert(previous, offered, ev):
    if not previous:
        return True
    return offered >= previous.get("offered_odds", 0) + RE_ALERT_ODDS_DELTA or ev >= previous.get("ev_percent", 0) + RE_ALERT_EV_DELTA


def scan_event(event, sent_cache, stats=None):
    stats = stats if stats is not None else {}
    stats["events_seen"] = stats.get("events_seen", 0) + 1
    start = parse_event_time(event.get("commence_time"))
    if start is None:
        stats["malformed_time"] = stats.get("malformed_time", 0) + 1
        return
    if not is_event_eligible(event) or event.get("completed") is True:
        stats["ineligible_or_completed"] = stats.get("ineligible_or_completed", 0) + 1
        return
    stats["eligible"] = stats.get("eligible", 0) + 1
    event_id = event.get("id") or f"{event.get('home_team')}|{event.get('away_team')}|{event.get('commence_time')}"
    for market_key in ("h2h", "totals"):
        sharp_markets, retail_markets = extract_bookmaker_markets(event, market_key)
        original_sharp_count, original_retail_count = len(sharp_markets), len(retail_markets)
        sharp_markets = [snapshot for snapshot in sharp_markets if market_is_fresh(snapshot)]
        retail_markets = [snapshot for snapshot in retail_markets if market_is_fresh(snapshot)]
        stale_count = (original_sharp_count - len(sharp_markets)) + (original_retail_count - len(retail_markets))
        if stale_count:
            stats["stale_or_undated_market_snapshots"] = (
                stats.get("stale_or_undated_market_snapshots", 0) + stale_count
            )
        if not sharp_markets:
            stats["no_sharp_markets"] = stats.get("no_sharp_markets", 0) + 1
            continue
        if not retail_markets:
            stats["no_1xbet_markets"] = stats.get("no_1xbet_markets", 0) + 1
            continue
        for outcome_names, snapshots in market_groups(sharp_markets, market_key).items():
            if not valid_market_signature(event.get("sport_key", ""), market_key, outcome_names):
                stats["invalid_market_signature"] = stats.get("invalid_market_signature", 0) + 1
                continue
            complete, duplicate_books = unique_book_snapshots(snapshots, outcome_names)
            if duplicate_books:
                stats["duplicate_sharp_book_observations_excluded"] = (
                    stats.get("duplicate_sharp_book_observations_excluded", 0) + len(duplicate_books)
                )
            unique_books = {s["book"] for s in complete}
            if len(unique_books) < MIN_SHARP_BOOKS:
                stats["insufficient_sharp_books"] = stats.get("insufficient_sharp_books", 0) + 1
                continue
            probability_samples = {name: [] for name in outcome_names}
            names = list(outcome_names)
            for snapshot in complete:
                probs = devig_shin([snapshot["prices"][name] for name in names])
                if len(probs) != len(names):
                    continue
                for name, probability in zip(names, probs):
                    probability_samples[name].append(probability)
            if any(len(values) < MIN_SHARP_BOOKS for values in probability_samples.values()):
                stats["insufficient_probability_samples"] = stats.get("insufficient_probability_samples", 0) + 1
                continue
            spread_pp = probability_spread_pp(probability_samples)
            if spread_pp > MAX_SHARP_PROB_SPREAD_PP:
                stats["sharp_disagreement_rejected"] = stats.get("sharp_disagreement_rejected", 0) + 1
                continue
            fair_probs = {name: median(values) for name, values in probability_samples.items()}
            normaliser = sum(fair_probs.values())
            if normaliser <= 0:
                stats["invalid_fair_probability"] = stats.get("invalid_fair_probability", 0) + 1
                continue
            fair_probs = {name: p / normaliser for name, p in fair_probs.items()}
            for name, probability in fair_probs.items():
                target = find_target_quote(retail_markets, name)
                if target is None:
                    stats["selection_missing_at_1xbet"] = stats.get("selection_missing_at_1xbet", 0) + 1
                    continue
                target_snapshot, offered = target
                target_book = target_snapshot["book"]
                target_quote_updated_at = target_snapshot.get("last_update")
                if not MIN_ODDS <= offered <= MAX_ODDS:
                    stats["odds_out_of_range"] = stats.get("odds_out_of_range", 0) + 1
                    continue
                ev = (probability * offered - 1.0) * 100.0
                if ev < MIN_EV:
                    stats["ev_below_threshold"] = stats.get("ev_below_threshold", 0) + 1
                    continue
                stake = fractional_kelly(probability, offered)
                if stake <= 0:
                    stats["non_positive_kelly"] = stats.get("non_positive_kelly", 0) + 1
                    continue
                stats["qualified_signals"] = stats.get("qualified_signals", 0) + 1
                alert_id = f"{event_id}|{market_key}|{name}"
                if not should_alert(sent_cache.get(alert_id), offered, ev):
                    stats["suppressed_duplicate_or_small_move"] = stats.get("suppressed_duplicate_or_small_move", 0) + 1
                    continue
                fair_odds = 1.0 / probability
                timestamp = datetime.now(timezone.utc).isoformat()
                signal = {
                    "timestamp": timestamp,
                    "event_id": event_id,
                    "sport": event.get("sport_key", ""),
                    "match": f"{event.get('home_team', '')} vs {event.get('away_team', '')}",
                    "commence_time": event.get("commence_time", ""),
                    "market": market_key,
                    "selection": name,
                    "offered_odds": round(offered, 4),
                    "fair_odds": round(fair_odds, 4),
                    "fair_probability": round(probability, 6),
                    "ev_percent": round(ev, 4),
                    "stake_pct": stake,
                    "sharp_books": len(unique_books),
                    "sharp_sample_count": len(complete),
                    "signal_id": build_signal_id(event_id, market_key, name, offered, probability),
                    "signal_version": SIGNAL_VERSION,
                    "target_book": target_book,
                    "target_quote_updated_at": target_quote_updated_at,
                    "sharp_book_keys": "|".join(sorted(unique_books)),
                    "sharp_quote_timestamps": "|".join(
                        f"{snapshot['book']}={snapshot['last_update']}"
                        for snapshot in sorted(complete, key=lambda item: item["book"])
                    ),
                    "sharp_probability_spread_pp": round(spread_pp, 4),
                }
                if append_signal(signal):
                    stats["signals_recorded"] = stats.get("signals_recorded", 0) + 1
                else:
                    stats["duplicate_signal_snapshots_suppressed"] = (
                        stats.get("duplicate_signal_snapshots_suppressed", 0) + 1
                    )

                delivery = send_telegram(
                    format_alert(
                        event, market_key, name, offered, fair_odds, probability, ev, stake,
                        unique_books, target_book=target_book, probability_spread_pp=spread_pp,
                        target_quote_updated_at=target_quote_updated_at,
                    )
                )
                if delivery is True:
                    stats["telegram_sent"] = stats.get("telegram_sent", 0) + 1
                    sent_cache[alert_id] = {"offered_odds": offered, "ev_percent": ev, "updated_at": timestamp}
                elif delivery is None:
                    stats["telegram_not_configured"] = stats.get("telegram_not_configured", 0) + 1
                    # Local paper-trading mode must not log a new snapshot every poll.
                    sent_cache[alert_id] = {"offered_odds": offered, "ev_percent": ev, "updated_at": timestamp}
                else:
                    stats["telegram_failed"] = stats.get("telegram_failed", 0) + 1


def fetch_sport(sport_key):
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY is not configured")
    if not api_budget_available(f"odds:{sport_key}"):
        raise ApiBudgetLow(f"API quota reserve reached before odds request for {sport_key}")
    response = requests.get(
        f"{ODDS_API_BASE}/sports/{sport_key}/odds/",
        params={"apiKey": ODDS_API_KEY, "bookmakers": ",".join(API_BOOKMAKERS), "markets": "h2h,totals", "oddsFormat": "decimal"},
        timeout=REQUEST_TIMEOUT,
    )
    log_api_quota(response, f"odds:{sport_key}")
    response.raise_for_status()
    return response.json()


def run():
    print("=== 1xBet VALUE ENGINE v2 ===")
    print(f"Telegram configured: {'yes' if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID else 'NO'}")
    print(f"Odds request bookmakers: {', '.join(API_BOOKMAKERS)} | markets: h2h,totals")
    print(f"Filters: odds {MIN_ODDS:.2f}-{MAX_ODDS:.2f}, EV >= {MIN_EV:.2f}%, sharp books >= {MIN_SHARP_BOOKS}, max book spread <= {MAX_SHARP_PROB_SPREAD_PP:.1f}pp, market age <= {MAX_MARKET_AGE_SECONDS:.0f}s")
    print(f"Target 1xBet bookmaker keys: {', '.join(sorted(TARGET_RETAIL_BOOKS))}")
    cache = load_cache()
    stats = {}
    total_events = 0
    for sport in SPORTS_TO_SCAN:
        try:
            events = fetch_sport(sport)
            total_events += len(events)
            print(f"{sport}: {len(events)} events")
            for event in events:
                scan_event(event, cache, stats)
        except ApiBudgetLow as exc:
            print(f"{exc}; stopping the rest of this scan.")
            break
        except requests.RequestException as exc:
            print(f"{sport}: API error: {exc}")
        except Exception as exc:
            print(f"{sport}: scanner error: {exc}")
    save_cache(cache)
    print("=== DIAGNOSTICS ===")
    for key in sorted(stats):
        print(f"{key}: {stats[key]}")
    print(f"=== COMPLETE: {total_events} events scanned ===")


if __name__ == "__main__":
    run()
