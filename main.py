import os
import re
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone

ODDS_API_KEY = os.environ.get("ODDS_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

KELLY_FRACTION = 0.25
MAX_BANKROLL_BET_PERCENT = 5.0
MIN_ODDS_CAP = 1.35
MAX_ODDS_CAP = 4.50
CACHE_FILE = "sent_alerts_cache.csv"
TRACKER_FILE = "bet_history.csv"

SPORTS_TO_SCAN = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_one",
    "soccer_uefa_champs_league",
    "tennis_atp_wimbledon",
    "basketball_nba"
]


def send_telegram_alert(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram credentials missing. Printing alert locally:")
        print(message)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        res = requests.post(url, json=payload)
        if res.status_code == 200:
            print("📱 Telegram alert sent successfully!")
        else:
            print(f"❌ Telegram Error: {res.text}")
    except Exception as e:
        print(f"❌ Telegram Connection Error: {e}")


def load_sent_cache():
    if os.path.exists(CACHE_FILE):
        try:
            return set(pd.read_csv(CACHE_FILE)["alert_id"].tolist())
        except Exception:
            return set()
    return set()


def save_sent_cache(cache_set):
    try:
        df = pd.DataFrame({"alert_id": list(cache_set)})
        df.to_csv(CACHE_FILE, index=False)
    except Exception as e:
        print(f"⚠️ Cache Save Error: {e}")


def log_bet_tracker(data_dict):
    try:
        if os.path.exists(TRACKER_FILE):
            df = pd.read_csv(TRACKER_FILE)
            df = pd.concat([df, pd.DataFrame([data_dict])], ignore_index=True)
        else:
            df = pd.DataFrame([data_dict])
        df.to_csv(TRACKER_FILE, index=False)
        print("📈 Bet logged to tracking ledger.")
    except Exception as e:
        print(f"⚠️ Ledger Logging Error: {e}")


def devig_multiplicative(sharp_odds_dict):
    raw_implied = {k: 1.0 / v for k, v in sharp_odds_dict.items() if v > 1.0}
    total_margin = sum(raw_implied.values())
    if total_margin <= 0:
        return {}
    return {k: v / total_margin for k, v in raw_implied.items()}


def calculate_kelly_stake(true_prob, retail_odds, fraction=KELLY_FRACTION):
    p = true_prob
    b = retail_odds - 1.0
    if b <= 0 or p <= 0:
        return 0.0
    kelly_f = ((p * b) - (1.0 - p)) / b
    if kelly_f <= 0:
        return 0.0
    recommended_stake = min(kelly_f * fraction * 100, MAX_BANKROLL_BET_PERCENT)
    return round(recommended_stake, 2)


def humanize_stake(stake_pct, hypothetical_bankroll=1000.0):
    raw_amount = (stake_pct / 100.0) * hypothetical_bankroll
    if raw_amount < 5.0:
        return f"{stake_pct}% (~$5)"
    elif raw_amount < 25.0:
        clean_amount = round(raw_amount / 5.0) * 5
    elif raw_amount < 100.0:
        clean_amount = round(raw_amount / 10.0) * 10
    else:
        clean_amount = round(raw_amount / 25.0) * 25
    return f"{stake_pct}% (~${int(clean_amount)})"


def get_dynamic_ev_threshold(commence_time_str):
    try:
        match_time = datetime.fromisoformat(commence_time_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        hours_to_kickoff = (match_time - now).total_seconds() / 3600.0
        
        if hours_to_kickoff <= 3:
            return 2.0
        elif hours_to_kickoff <= 24:
            return 3.5
        else:
            return 5.0
    except Exception:
        return 3.5


def format_high_speed_alert(sport_key, home_team, away_team, selection, commence_time, 
                            offered_odds, fair_odds, ev_percent, humanized_stake, is_steam):
    steam_badge = "🔥 *STEAM MOVE DETECTED* 🔥\n" if is_steam else ""
    ev_badge = "🚀 *HIGH VALUE OVERLAY*" if ev_percent >= 6.0 else "⚡️ *POSITIVE EV EDGE*"
    
    msg = f"{steam_badge}{ev_badge}\n"
    msg += f"━━━━━━━━━━━━━━━━━━━\n"
    msg += f"⚽️ *MATCH:* `{home_team}` vs `{away_team}`\n"
    msg += f"🎯 *SELECTION:* `{selection}`\n"
    msg += f"🏆 *SPORT:* `{sport_key.upper().replace('_', ' ')}`\n"
    msg += f"⏰ *KICKOFF:* `{commence_time} UTC`\n"
    msg += f"━━━━━━━━━━━━━━━━━━━\n"
    msg += f"📈 *1xBet Line:* `{offered_odds}x` 👈 *PLACE THIS LINE*\n"
    msg += f"🎯 *Sharp Fair Odds:* `{fair_odds}x` (De-vigged)\n"
    msg += f"🔥 *Mathematical EV:* `+{round(ev_percent, 1)}%`\n"
    msg += f"💰 *Recommended Stake:* `{humanized_stake}`\n"
    msg += f"━━━━━━━━━━━━━━━━━━━\n"
    msg += f"📲 *ACTION:* Open 1xBet ➔ Search `{home_team}` ➔ Place `{selection}` @ `{offered_odds}`"
    
    return msg


def fetch_and_scan_sport(sport_key, sent_cache):
    if not ODDS_API_KEY:
        print("❌ Error: ODDS_API_KEY environment variable is missing.")
        return

    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu,uk",
        "markets": "h2h,totals",
        "oddsFormat": "decimal"
    }

    try:
        res = requests.get(url, params=params)
        if res.status_code != 200:
            print(f"⚠️ API Warning [{sport_key}]: {res.status_code} - {res.text}")
            return

        events = res.json()
        print(f"-> Scanned {len(events)} events for {sport_key}")

        for event in events:
            home_team = event.get("home_team", "Home")
            away_team = event.get("away_team", "Away")
            raw_commence = event.get("commence_time", "")
            commence_time = raw_commence.replace("T", " ")[:16]
            
            min_ev_required = get_dynamic_ev_threshold(raw_commence)

            for market_type in ["h2h", "totals"]:
                sharp_odds = {}
                retail_odds = {}

                for bookmaker in event.get("bookmakers", []):
                    key = bookmaker.get("key", "").lower()
                    for market in bookmaker.get("markets", []):
                        if market.get("key") != market_type:
                            continue

                        outcomes = market.get("outcomes", [])
                        odds_dict = {}
                        for out in outcomes:
                            name = out["name"]
                            if "point" in out:
                                name = f"{name} {out['point']}"
                            odds_dict[name] = out["price"]

                        if key in ["pinnacle", "betfair_ex_uk", "unibet_eu"]:
                            for name, price in odds_dict.items():
                                if name not in sharp_odds:
                                    sharp_odds[name] = []
                                sharp_odds[name].append(price)

                        if key in ["1xbet", "1x", "marathonbet", "bet365"]:
                            for name, price in odds_dict.items():
                                retail_odds[name] = price

                avg_sharp_line = {k: np.mean(v) for k, v in sharp_odds.items() if len(v) > 0}
                true_fair_probs = devig_multiplicative(avg_sharp_line)

                for outcome_name, true_prob in true_fair_probs.items():
                    if outcome_name not in retail_odds:
                        continue

                    offered_1x_odds = retail_odds[outcome_name]

                    if not (MIN_ODDS_CAP<= offered_1x_odds <= MAX_ODDS_CAP):
                        continue

                    fair_odds = round(1.0 / true_prob, 2)
                    ev_percent = ((true_prob * offered_1x_odds) - 1.0) * 100.0

                    if ev_percent >= min_ev_required:
                        alert_id = f"{home_team}_{away_team}_{outcome_name}_{offered_1x_odds}"
                        if alert_id in sent_cache:
                            continue

                        stake_percent = calculate_kelly_stake(true_prob, offered_1x_odds)
                        if stake_percent > 0:
                            is_steam = ev_percent >= 6.5
                            humanized_stake = humanize_stake(stake_percent)
                            
                            msg = format_high_speed_alert(
                                sport_key, home_team, away_team, outcome_name,
                                commence_time, offered_1x_odds, fair_odds,
                                ev_percent, humanized_stake, is_steam
                            )

                            send_telegram_alert(msg)
                            sent_cache.add(alert_id)
                            
                            log_bet_tracker({
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "sport": sport_key,
                                "match": f"{home_team} vs {away_team}",
                                "selection": outcome_name,
                                "offered_1xbet_odds": offered_1x_odds,
                                "fair_sharp_odds": fair_odds,
                                "ev_percent": round(ev_percent, 2),
                                "recommended_stake_pct": stake_percent
                            })

    except Exception as e:
        print(f"❌ Error scanning {sport_key}: {e}")


def run_scanner():
    print("=== STARTING QUANTITATIVE 1XBET +EV ENGINE ===")
    sent_cache = load_sent_cache()
    
    for sport in SPORTS_TO_SCAN:
        fetch_and_scan_sport(sport, sent_cache)
        
    save_sent_cache(sent_cache)
    print("=== SCAN COMPLETE ===")


if __name__ == "__main__":
    run_scanner()
