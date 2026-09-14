# 1xBet Value Engine

A research-oriented +EV scanner that compares 1xBet-family prices with a small set of sharper reference books. It sends alerts; it does **not** place bets automatically.

## What changed in v2

- Fixed the syntax error in the original `main.py` (`namein` -> valid iteration logic).
- Removed the pandas/numpy runtime dependency; the scanner now uses the Python standard library plus `requests`.
- Replaced the fragile Shin implementation with a bounded numerical solve and validation.
- Uses the **median** price from each sharp-book sample rather than a simple mean, reducing sensitivity to one outlier.
- Requires at least two sharp reference books before generating a signal.
- Stable alert IDs no longer include the current 1xBet odds, preventing repeated alerts every time a price changes.
- Added request timeouts and clearer API/Telegram error handling.
- Added a persistent `signals.csv` research ledger.
- Added unit tests and a CI job before scheduled scans.
- Added environment-variable controls for odds, EV, stake, timeout, and sharp-book requirements.

## Configuration

Required GitHub Actions secrets:

- `ODDS_API_KEY`
- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`

Optional environment variables:

- `MIN_ODDS` (default `1.35`)
- `MAX_ODDS` (default `4.50`)
- `MIN_EV` (default `3.0` percent)
- `MAX_STAKE_PCT` (default `2.0` percent)
- `KELLY_FRACTION` (default `0.25`)
- `MIN_SHARP_BOOKS` (default `2`)
- `REQUEST_TIMEOUT` (default `20` seconds)

## Important model limitation

A reference-book consensus is an estimate, not a guaranteed true probability. Market timing, limits, stale prices, bookmaker-specific rules, missing outcomes, and data latency can all create false positives. Treat every signal as a research candidate and verify the live market before any decision.

## Local test

```bash
python -m unittest discover -s tests -v
python -m py_compile main.py tests/test_engine.py
```
