# Cross-instrument paper hedging

The practice allowlist now contains BA shares and options plus SPY, XLI, ITA,
QQQ, TLT and GLD ETF shares. US stocks and ETFs both use qualified IBKR STK
contracts. All discretionary positions are jointly evaluated against the same
interest-adjusted closing NAV objective. Existing holdings remain in valuation
and risk calculations. Exact mandatory contracts cannot be traded away.

The universe is configurable; it is not restricted to BA. Read-only discovery:

```
python -m hedge.cli expand-universe --config CONFIG.yaml \
  --symbols SPY XLI ITA --output EXPANDED.yaml
python ops/fetch_moomoo_daily.py --symbols BA SPY XLI ITA \
  --as-of YYYY-MM-DD --output data/moomoo/daily-stocks.csv
```

Run the daily downloader in the isolated moomoo SDK environment. It creates only
OpenQuoteContext, downloads prior completed adjusted daily bars, and never
opens a trade context. Set `daily_stock_history` in the expanded manifest to the
CSV path; generate collector codes from the manifest's qualified contracts.
Pause and reconcile orders before deploying a manifest change. The normal
startup is paused; use shadow mode to inspect proposals before enabling paper.

Discovery accepts up to 12 underlying symbols per optimization universe to
bound data subscriptions and mixed-integer solve work. New symbols must have
unambiguous USD stock identities, live permitted books, and joint historical
coverage before activation. Stocks cannot be newly shorted without verified
borrow availability. Existing short positions remain represented and may be
reduced. Futures, FX, bonds and crypto are not activated by a ticker list;
they require validated pricing, settlement, margin and execution adapters.

Daily-return scenarios use the configured 20/60/120 prior-session window,
aligned across all represented symbols, with at least 20 observations, 20%
diagonal covariance shrinkage, and deterministic antithetic shocks. The model
has zero directional drift. Today's incomplete bar and future bars are excluded.
Daily volatility is scaled to the remaining calendar horizon; option IV/skew
paths remain modeled. Missing, duplicate or stale joint history blocks scenario
construction instead of substituting invented correlations. Sufficient matched
intraday option history takes precedence when supplied.

These are forecast assumptions, not demonstrated contest performance. A broader
universe cannot guarantee reaching the closing target or repairing earlier NAV
changes. No-trade remains a valid outcome if candidates do not improve expected
score enough after costs and uncertainty. The current practice improvement floor is $25, with an unchanged uncertainty
allowance, 20% local residual-delta cap and 3% stress-loss cap. See
FAST_PAPER_POLICY.md for the operator-selected faster evaluation schedule.

Tests cover read-only discovery, mandatory protection, multi-underlying scenario
coverage, deterministic covariance/shocks, future-row exclusion, missing-history
rejection, and unavailable borrow restrictions. Repeated stress repricing is
cached within each scenario build to avoid redundant option valuations.
