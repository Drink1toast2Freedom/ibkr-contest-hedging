# Recovery implementation and operator runbook

## What is implemented

The first release adds deterministic SPY/QQQ momentum and reversal forecasts,
frozen model integrity checks, session-clustered parameter uncertainty, 50%
forecast shrinkage, conservative means, matched-clock residual-volatility floors,
and current-price rebasing. NAV deficit is never a forecast input.

The combined integer optimizer can propose an ETF recovery overlay on the actual
hedge book. It evaluates the same closing-NAV objective, cash flows and costs.
A qualifying ordinary hedge is completed first; an unexecuted hypothetical hedge
cannot be used to hide recovery exposure. Shadow candidates have independent
portfolio-risk and incremental-budget checks. Every actual slice repeats these
checks, including after a broker margin preview.

Recovery budgets are capped at 0.25% NAV modeled incremental downside, 5% NAV
incremental gross notional, and a 0.25% NAV attributed daily trading loss stop.
These caps do not guarantee a maximum realized loss. Commissions are taken from
corrected broker fills, with conservative provisional estimates while reports
are pending. Core hedges that close attributed recovery holdings update that
attribution. Daily baselines persist across restarts and mode changes; overnight
inventory requires prior closing marks. Financing remains in combined IBKR NAV
and is explicitly excluded from the strategy's trading-P&L attribution.

The live controller exposes Hedge only, Shadow recovery and Enable paper recovery.
Shadow analysis does not authorize recovery orders and leaves ordinary hedging
available. Recovery defaults to shadow after a paper-recovery restart. Turning
recovery off while a recovery plan is pending pauses submissions for cancellation
and reconciliation before ordinary paper hedging is re-enabled.

## Data and commands

Moomoo remains quote-only. The collector subscribes to one-minute stock/ETF bars,
seeds recent bars at startup, and writes an atomic live-bars CSV separately from
quote archives. Historical paging is throttled. No trade context is created.

```
.runtime/moomoo-env/bin/python ops/fetch_moomoo_intraday.py \
  --symbols SPY QQQ --as-of 2026-10-07 --calendar-days 180
.venv/bin/python -m hedge.cli train-recovery \
  --config config/local.practice.yaml --bars data/recovery/intraday.csv \
  --before 2026-10-07T09:30:00-04:00
.venv/bin/python -m hedge.cli validate-recovery \
  --config config/local.practice.yaml --bars data/recovery/intraday.csv \
  --train-sessions 60 --test-sessions 5
```

These dates are examples; use the next run's actual cutoff. Training excludes
current-session labels. The downloaded archive contains 97,113 bars across 125
sessions per symbol, including an incomplete acquisition-day session. Live
models train on 60 preceding completed sessions. Research holds each model fixed
for successive five-session test folds. Overlapping labels are separated by
session, and covariance of fitted parameters clusters by session.

The provider's minute timestamp start/end semantics are not asserted as verified.
The importer conservatively makes observations available 60 seconds after the
provider timestamp; incomplete/future observations are excluded. The last eligible
bar is a closing-price proxy, not the organizer's NAV. First-available-bar opening
features and provider volume coverage require verification before promotion.
Model results based on this convention are provisional. Forward adjustment is
NONE for research prices; historical daily covariance data remains separately
labeled. Split/dividend events and venue/consolidation differences need review.

Quote-based combined replay is available through the existing replay interfaces:

```
.venv/bin/python -m hedge.cli backtest --config CONFIG.yaml \
  --quotes HISTORICAL_NBBO.parquet --recovery --output REPORT.json
```

The supplied configuration's frozen model must precede the simulated scored
sessions. This executes only the simulator, preserves mandates, requires next
observed executable quotes, and records recovery fills and budget rejections.
It does not automatically promote a profile. No historical NBBO archive was
purchased or fabricated.

## Evidence and paper promotion

`data/recovery/validation.json` is the promotion-evidence file. The controller
requires an exact model/configuration/contest match, empirical combined quote-based
contest replay, at least 20 independent held-out contest windows, improved mean
score without worse 90th-percentile score, chronological training, forward shadow
and fault-test evidence, sourced cash benchmarks, bar-timing/volume verification,
and validated execution paths. Bar-only results and synthetic fixtures are
rejected. Approval is rechecked before selection and each slice; changed models
invalidate an active recovery plan. AI cannot change these requirements.

The dashboard displays promotion availability separately from forecasts and
ordinary paper hedging. Until evidence passes, the paper recovery button is
disabled and the expandable validation list identifies missing requirements.
Missing contest scores are reported as unrecorded results, rather than measured
underperformance. A direct API attempt still fails the same deterministic checks.
Dashboard status uses a file-change cache without reading the bar archive;
activation and execution continue to check current evidence independently.

Available bar research shows small RMSE improvements relative to a zero-return
forecast, before costs. That is not evidence of a profitable combined contest
strategy. Actual daily cash-rate history, historical option/ETF NBBO, fill sizes,
contract metadata, fees/financing assumptions, and representative forward tests
are still needed. Bar extrema and midpoint fills are not fabricated as evidence.
Historical NAV replays remain proxies for official IBKR reporting.

The dashboard distinguishes forecast/candidate status from execution authorization.
No signal, weak evidence, no borrow, stale bars/quotes, an expired signal/model,
model corruption, or a loss/budget breach prevents new recovery exposure. The
fallback is the validated hedge controller. All global limits, exact mandatory
quantities, account identity and IBKR source-of-truth rules continue to apply.

A difference greater than 2% between a forecast's completed-bar price and the
current quote blocks that forecast until a split, adjustment, feed mismatch, or
large intervening move is reviewed. Rebasing never turns an inconsistent price
series into an apparent recovery opportunity.

On October 7 the deployed service paused ordinary paper trading because TWS
accepted its socket connection but timed out on positions, account values, and
executions. An independent read-only client reproduced the failure. A reachable
port alone is insufficient: restore the authenticated Paper TWS session, then
reconcile broker state before enabling paper trading. Recovery remains shadow.

Pairs, directional option spreads, volatility-relative-value recovery and new
long-option gamma scalping are later research stages in ACTIVE_RECOVERY_PLAN.md;
they are not activated by this first release. The existing BA option/stock hedge
remains available as the gamma/Greek benchmark. No implementation result is
claimed to guarantee recovery of the current deficit.

API references for the quote-only minute-data implementation:
https://openapi.moomoo.com/moomoo-api-doc/en/quote/update-kl.html
https://openapi.moomoo.com/moomoo-api-doc/en/quote/request-history-kline.html
