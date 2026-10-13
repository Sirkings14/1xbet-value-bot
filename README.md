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
- Logs the target bookmaker, the target quote update timestamp, each sharp-book quote timestamp, source-book keys, and inter-book probability spread for later audits.
- Tags new records as `signal-integrity-v1`; legacy records lacking a version are grouped as `legacy_unknown` and reported separately.
- Performance reports show signal-version cohorts independently, including their pending and overdue counts; older records with no version are grouped as `legacy_unknown`, not conflated with the upgraded engine cohort.
- Alert-suppression IDs stay stable across price moves; ledger signal IDs are snapshot-specific and include the offered odds and estimated probability so identical retries are deduplicated without erasing materially changed snapshots.
- Added request timeouts and HTTP error handling.
- Rejects malformed, completed, or stale events according to the configured event-age policy.
- Added persistent `signals.csv` research logging.
- Added pre-start CLV snapshots in `clv_snapshots.csv`, benchmarked against the median of fresh sharp-book odds rather than the later 1xBet price.
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

The odds workflow runs the test gate first. Only after tests pass does it run the scanner, capture pre-start CLV, validate the ledger, and generate the performance report. A separate workflow settles pending events and refreshes analytics (and can be run manually), keeping score requests out of each odds scan. Automatic schedules are temporarily paused after the latest live response showed only 70 credits remaining from a 500-credit usage pool. Until the API quota is upgraded or confirmed, the scanner runs only on an explicit workflow dispatch; ordinary pushes run tests but do not poll live odds. Manual scanner dispatch still consumes credits. Re-enable scheduled scanning and settlement after confirming sufficient quota.

Once the API quota is sufficient, the planned odds cadence is every ten minutes from 08:00 through 23:59 UTC. It requests only four bookmakers used by this model: Pinnacle, Betfair Exchange UK, Unibet EU, and 1xBet (`onexbet`). The Odds API treats up to ten requested bookmakers as one region-equivalent for quota costing; with two markets, this is at most 2 credits per sport request. With six sports, the planned cadence would use up to about 34,560 odds credits per 30-day month if both markets are returned for every sport on every scan. Actual usage can be lower when markets are unavailable. The planned settlement/report cadence is every six hours and it queries only sports with pending signals; at six sports continuously pending, that is at most about 1,440 score credits per 30-day month. CLV usage varies with signals inside the pre-start capture window. The scanner logs provider quota headers (`x-requests-last`, `x-requests-used`, and `x-requests-remaining`) so real consumption can be checked in GitHub Actions.

GitHub Actions scheduling can be delayed, so a CLV snapshot is a reference point rather than a guaranteed official bookmaker closing price.

## Data files

- `signals.csv` — every accepted research signal, including a retry-safe ID, offered price, source-book provenance and update timestamps, probability spread, and settlement evidence.
- `clv_snapshots.csv` — pre-start sharp-book consensus snapshots, benchmark-book keys/count, each benchmark price and source update timestamp, and the price-movement CLV calculation.
- `sent_alerts_cache.csv` — state used to suppress immaterial repeat alerts.
- `analysis_report.csv` — generated performance summary and breakdowns, including pending/overdue counts and signal-version cohorts.
- `manual_results.csv` — optional, source-attributed score backfills for past events missing from the rolling scores API window.

### CLV definition

The current CLV metric is a **pre-start sharp-book consensus price-movement measure**:

`CLV % = (signal_odds / median_fresh_sharp_odds - 1) * 100`

A positive value means the offered signal price was higher than the later median odds from the eligible sharp-book reference set. Snapshots require at least `MIN_SHARP_BOOKS` fresh, unique sharp-book observations for the same market signature. Because the capture window is normally up to ten minutes before kickoff and may not coincide with the final market close, this is not described as an official closing-line measurement.

## Verified manual score backfill

`manual_results.csv` now contains source-attributed results for the five overdue historical events in the current ledger. For any future completed event still pending after it falls outside the scores API's recent window, add another verified row using the same columns. Required columns are:

`event_id,home_team,away_team,home_score,away_score,completed,source_name,source_url,verified_at`

Rules:
- Copy the exact `event_id` and team labels from `signals.csv`; the event ID and home/away labels must match the pending signal.
- Enter only a final score that has been independently checked. Set `completed` to `true`.
- Provide a meaningful source name, a working `http://` or `https://` evidence URL, and an ISO 8601 `verified_at` timestamp with timezone, for example `2026-10-13T06:30:00+00:00`.
- The scores API result takes precedence when it can settle the signal. Invalid rows and duplicate event IDs in the manual file are rejected; manual settlement is not used if the recorded match labels do not match.
- A settled signal records its settlement method and evidence fields. Already-settled rows are not overwritten.

The report now counts pending signals, overdue pending signals (kickoff time is in the past), and the age of the oldest overdue signal. Pending records are excluded from ROI, P/L, drawdown, and calibration calculations.

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
