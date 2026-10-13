# 1xBet Value Engine

A research-oriented +EV scanner that compares 1xBet-family prices with a small set of sharper reference books. It sends research alerts; it does **not** place bets automatically.

## v2 pipeline

```text
Live odds
  -> event/time validation
  -> sharp-book market grouping
  -> Shin de-vigging
  -> median sharp consensus
  -> 1xBet target price
  -> +EV filter
  -> fractional Kelly risk cap
  -> stable research alert
  -> pre-start CLV snapshot
  -> post-event paper settlement
  -> signal integrity gate
  -> performance analytics
```

The objective is to measure whether the signal process has a repeatable edge before treating it as a decision system.

## What changed in v2

- Fixed the original `main.py` syntax error (`namein`).
- Removed the pandas/numpy runtime dependency; the scanner uses the standard library plus `requests`.
- Replaced the fragile Shin implementation with a bounded numerical solve and validation.
- Uses median sharp-book consensus instead of a simple mean.
- Requires at least two complete sharp-book observations for a signal.
- Keeps different market/line signatures separate so incomplete books are not mixed together.
- Separates each totals line into its own Over/Under market and rejects incomplete soccer 1X2 outcome sets.
- Requires a recent market-source timestamp and rejects missing/stale market quotes.
- Uses at most one observation per sharp bookmaker; duplicate observations from one book are excluded.
- Rejects signals when sharp-book probability estimates disagree by more than the configurable spread guard.
- Persists a stable signal ID and source-book provenance before attempting Telegram delivery, with CSV schema migration and retry-safe deduplication.
- Logs the target bookmaker, sharp-book keys, and inter-book probability spread for later audits.
- Stable signal IDs no longer include the current 1xBet odds, preventing duplicate signals when prices move.
- Added request timeouts and HTTP error handling.
- Rejects malformed, completed, or stale events according to the configured event-age policy.
- Added persistent `signals.csv` research logging.
- Added pre-start CLV snapshots in `clv_snapshots.csv`.
- Added paper settlement from completed-event scores in `settle_signals.py`.
- Added data-integrity validation in `signal_quality.py`.
- Added performance analytics including ROI, drawdown, losing streak, Brier score, calibration, EV/odds/sport/market breakdowns, and CLV.
- Added unit tests and CI syntax/test gates before scheduled scans.
- Updated GitHub Actions versions and permissions.

## Research-only safety boundary

The bot is deliberately an **alert and evaluation engine**. It does not log in to a bookmaker, submit wagers, move funds, or automate betting decisions. Stake sizing is a paper-trading percentage used to evaluate the model.

## Default model controls

- `MIN_ODDS=1.35`
- `MAX_ODDS=4.50`
- `MIN_EV=3.0` percent
- `MAX_STAKE_PCT=2.0` percent
- `KELLY_FRACTION=0.25`
- `MIN_SHARP_BOOKS=2`
- `MAX_SHARP_PROB_SPREAD_PP=8.0` (initial heuristic guard; not optimized on outcome data)
- `MAX_MARKET_AGE_SECONDS=900` (15-minute maximum source-market age)
- `REQUEST_TIMEOUT=20` seconds
- `MAX_EVENT_AGE_MINUTES=0` (future events only by default)
- `RE_ALERT_EV_DELTA=1.0` percentage point
- `RE_ALERT_ODDS_DELTA=0.05`
- `CLV_MINUTES_BEFORE_START=10`

The 8-percentage-point disagreement cap is a configurable initial guardrail, not a validated optimum. The 15-minute quote-age cap follows a conservative market freshness policy. Measure rejection counts and CLV before tuning either setting. These defaults do not establish profitability.

## GitHub Actions setup

Required repository secrets:

- `ODDS_API_KEY`
- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`

The scheduled workflow runs the test gate first. Only after tests pass does it run the scanner, capture pre-start CLV, settle completed signals, validate the resulting ledger, and generate the performance report.

The scanner schedule currently polls every five minutes during the configured UTC window. GitHub Actions scheduling can be delayed, so a CLV snapshot is a reference point rather than a guaranteed official bookmaker closing price.

## Data files

- `signals.csv` — every accepted research signal, including event time, price, fair probability, EV, stake percentage, and settlement fields.
- `clv_snapshots.csv` — pre-start reference-price snapshots and the price-movement CLV calculation.
- `sent_alerts_cache.csv` — state used to suppress immaterial repeat alerts.
- `analysis_report.csv` — generated performance summary and breakdowns.

### CLV definition

The current CLV metric is a **pre-start reference-price movement proxy**:

`CLV % = (signal_odds / reference_odds - 1) * 100`

Positive CLV means the later reference price shortened relative to the original signal price. It is useful evidence about price movement, but it is not claimed to be an official exchange/bookmaker closing-line measurement.

## Validation standard

A green CI run proves the code and unit tests pass; it does **not** prove profitability.

Before considering the model ready for any real-world decision, accumulate a meaningful paper-trading sample and review:

1. Net paper P/L and ROI.
2. Positive CLV rate and average CLV.
3. Calibration/Brier score of the probability estimates.
4. Performance by sport and market.
5. Performance by EV and odds buckets.
6. Maximum drawdown and losing streak.
7. Data-quality failures, stale prices, missing books, and settlement exceptions.

Do not tune thresholds against a tiny sample. Separate model changes from evaluation periods so improvements are not mistaken for overfitting.

## Local test

```bash
python -m unittest discover -s tests -v
python -m py_compile main.py settle_signals.py analytics.py signal_quality.py clv.py
```

## Current status

The rebuild is designed to be **paper-trading ready**, subject to live API credentials and the quality of incoming odds data. The evidence phase comes next: let the engine collect signals, CLV observations, and settlements, then judge the edge from the resulting data rather than from backtest assumptions or hype.
