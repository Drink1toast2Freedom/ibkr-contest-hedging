# Related and inverse securities: BA contest research

Research date: October 7, 2026. The original assessment made no trading changes.
The operator subsequently authorized applying UAL, GE and XLE: they are now in
the hedge allowlist and quote subscriptions. See
[EXPANDED_UNIVERSE.md](EXPANDED_UNIVERSE.md) for deployment validation.
Recovery forecasts and hard limits are unchanged; operation remains shadow after close.
Data acquisition used moomoo OpenQuoteContext only; no trades were submitted.

## Recommendation

Test UAL and GE first as additional stock hedge candidates. Retain ITA, XLI and
SPY as existing sector/market benchmarks. Test XLE as a conditional energy
candidate and CVX as its single-stock alternative, without treating either as
a reliable inverse hedge. XAR is an alternative aerospace factor basket. DAL
and JETS are useful comparison candidates, but have earnings-event exposure in
the remaining practice window. Do not automatically accumulate correlated
names: choose the combination that improves the whole portfolio's expected
absolute distance from the interest-adjusted closing target, after costs.

No ordinary stock in this screen demonstrated strong, stable negative BA
correlation. A negative estimated correlation is not permission to assume the
relationship persists, and a correlation alone is not a recovery return forecast.

## Empirical screen

Downloaded 24 symbols through the existing quote-only historical API, using
completed daily prices from February 9 through October 6, 2026. There are 166
prices per symbol and 165 matched daily return observations. Today's incomplete
session is excluded. Returns are close/previous close minus one; correlations
are Pearson correlations against BA's share-price returns over the last 20, 60
and 120 observations. Provider adjustment is QFQ; corporate-action adjustments
have not been independently validated. These are daily price-return measurements,
not measured option P&L, intraday correlation, fill evidence, causal estimates or
held-out contest performance. Multiple candidates were inspected retrospectively;
selection needs a later untouched validation period.

| Symbol | 20-session correlation | 60-session | 120-session | Research role |
|---|---:|---:|---:|---|
| ITA | +0.592 | +0.604 | +0.649 | Existing aerospace benchmark; strongest sampled sector relationship |
| UAL | +0.387 | +0.578 | +0.499 | First additional airline stock |
| XLI | +0.519 | +0.549 | +0.518 | Existing broad industrial factor |
| JETS | +0.256 | +0.549 | +0.520 | Airline basket alternative; contains DAL and BA |
| DAL | +0.315 | +0.523 | +0.480 | Airline alternative; October 9 earnings event |
| XAR | +0.473 | +0.506 | +0.581 | Modified equal-weight aerospace basket alternative |
| SPY | +0.373 | +0.464 | +0.497 | Existing market factor |
| GE | +0.267 | +0.452 | +0.488 | First additional aerospace supplier stock |
| LUV | +0.154 | +0.447 | +0.405 | Boeing customer, weaker recent relationship than UAL |
| TLT | +0.118 | +0.341 | +0.404 | Rates exposure; not inverse in this sample |
| QQQ | +0.313 | +0.324 | +0.364 | Existing market/growth and recovery benchmark |
| GD | +0.335 | +0.273 | +0.223 | Defense watchlist |
| NOC | +0.240 | +0.268 | +0.269 | Defense watchlist |
| HWM | +0.401 | +0.261 | +0.386 | Supplier watchlist, not first choice |
| RTX | +0.336 | +0.243 | +0.347 | Supplier/defense watchlist, not first choice |
| GLD | +0.432 | +0.205 | +0.430 | Gold exposure; not inverse in this sample |
| LMT | -0.090 | +0.090 | +0.108 | Weak and unstable BA relationship |
| COP | +0.263 | -0.161 | -0.351 | Energy relationship reverses in recent window |
| XLE | +0.164 | -0.222 | -0.380 | Conditional energy basket; recent sign reversal |
| EOG | +0.241 | -0.246 | -0.383 | Energy watchlist; recent sign reversal |
| XOM | -0.051 | -0.265 | -0.410 | Energy alternative; weak recent inverse relationship |
| CVX | +0.014 | -0.323 | -0.421 | Conditional energy stock; recent inverse relationship disappears |
| SH | -0.382 | -0.466 | -0.498 | Inverse-market research benchmark, not approved execution |

As a basic market-factor check, residualizing BA and each candidate against SPY
over 60 sessions leaves correlations of +0.499 for ITA, +0.409 UAL, +0.351 GE,
and +0.341 DAL. This is still an in-sample diagnostic, not a proven tradable
spread. Several names are redundant: UAL/DAL correlate +0.917, UAL/JETS +0.936,
ITA/XAR +0.882 and CVX/XLE +0.930 in the same 60-session sample. Avoid interpreting
several positions in one cluster as several independent risk diversifiers.

Raw provider data and calculations are local ignored research artifacts:

- `reports/research/ba-all-candidate-daily.csv`
- `reports/research/ba-candidate-correlations.csv`
- `reports/research/ba-candidate-summary.json`

Five shortlist correlations were independently cross-checked using centered
NumPy vectors against Pandas calculations. No application code changed.

## Business relationships and event risk

UAL is a Boeing customer; shared travel/capital-spending conditions support a
relationship hypothesis. Aircraft delays, fuel prices, labor and airline demand
also create risks absent from BA. United's announced results release is October
20, after this practice's final close; that does not remove unscheduled or
cross-airline event risk. Sources:
[United 2025 10-K](https://www.sec.gov/Archives/edgar/data/100517/000010051726000023/ual-20251231.htm)
and [United earnings schedule](https://united.mediaroom.com/2026-09-30-United-to-Hold-Webcast-of-Third-Quarter-2026-Financial-Results).

GE supplies engines for Boeing platforms, including the 737 MAX through CFM and
the 777X through GE9X. It also serves Airbus and aftermarket customers, so its
stock is an imperfect BA proxy. Its published calendar lists October 20 earnings.
Sources: [GE annual report](https://www.geaerospace.com/investor-relations/annual-report)
and [GE calendar](https://www.geaerospace.com/sites/default/files/geaerospace_webcast_presentation_04212026.pdf).

DAL announces its results webcast for October 9 at 10 a.m. ET. Prefer UAL over
DAL for an initial ordinary-stock trial in this practice; require an explicit
earnings stress for all airline candidates, including UAL and JETS. JETS holds
DAL and BA, so it is neither event-free nor independent of the mandatory
underlying. Sources: [Delta announcement](https://news.delta.com/delta-air-lines-announces-webcast-september-quarter-2026-financial-results)
and [JETS holdings](https://usglobaletfs.com/fund/u-s-global-jets-etf/).

ITA holds BA alongside other aerospace/defense companies. XAR's modified equal
weighting offers a different mix, not an independent bet on the same factor.
Sources: [ITA](https://www.ishares.com/us/products/239502/ishares-us-aerospace--defense-etf)
and [XAR](https://www.ssga.com/us/en/individual/etfs/state-street-spdr-sp-aerospace-defense-etf-xar).

XLE represents energy companies including Exxon and Chevron. Commodity supply
shocks can benefit producers while hurting transport economics; a broad demand
shock can hurt both sectors. This is a conditional economic hypothesis, not a
claim that oil causes BA returns or guarantees an inverse relationship. CVX
adds company-specific commodity and operating risk. Sources:
[XLE](https://www.ssga.com/us/en/institutional/etfs/state-street-energy-select-sector-spdr-etf-xle)
and [Chevron 10-K](https://www.sec.gov/Archives/edgar/data/93410/000009341026000078/cvx-20251231.htm).

SH targets the inverse of the S&P 500's daily performance, not BA's performance.
It requires the separate daily-reset/holding-horizon validation already required
by the plan. Do not enable it merely to circumvent missing stock borrow. No
leveraged or volatility-linked fund is proposed for immediate activation.
Sources: [SH objective](https://www.proshares.com/our-etfs/leveraged-and-inverse/sh)
and [FINRA holding-period explanation](https://syndication.finra.org/content/lowdown-leveraged-and-inverse-exchange-traded-products).

Airbus is a competitor research hypothesis, not an established inverse BA asset.
Its foreign ordinary shares introduce FX and mismatched trading-session risk;
an ADR/OTC instrument needs separately verified quotes and execution liquidity.
It is deferred from the first US-exchange-stock expansion. SPR is not proposed
as a standalone stock: Boeing completed its acquisition in December 2025.
Source: [Boeing acquisition announcement](https://boeing.mediaroom.com/2025-12-08-Boeing-Completes-Acquisition-of-Spirit-AeroSystems).

## How to add candidates without changing the objective

1. Research pool: the 24 screened symbols. Initial next-test additions: UAL,
   GE and XLE, with DAL/JETS, XAR and CVX tested as alternatives. This adds three
   names to the existing seven underlying symbols and stays within the current
   12-underlying cap. Retain all filled holdings regardless of candidate selection.
2. Independently qualify IBKR identities and verify moomoo quote rights, fresh
   bid/ask size, spreads, corporate actions and scheduled events. Measure actual
   market-hours liquidity; this daily screen does not establish executable size.
3. Acquire synchronized completed intraday bars and forward NBBO. Estimate
   20/60/120-session relationships at matching time of day and remaining holding
   horizon. Include factor residuals, negative/positive market days, overnight
   gaps, event windows, sign changes and estimator uncertainty. Energy candidates
   with disappearing inverse relationships must not be labeled permanent hedges.
4. Reprice the actual mandatory options and all existing hedges under joint
   scenarios. Compare each added instrument with BA shares, BA option hedges and
   existing ETFs. Stocks principally hedge delta/common factors; they cannot
   neutralize BA gamma, vega or theta by correlation alone. A long straddle can
   gain on either large price direction, so a permanently long inverse stock is
   not a general straddle hedge. Order direction comes from the combined optimizer.
5. Require lower held-out total official contest score after realistic costs,
   acceptable tails and unchanged hard limits. A pure hedge may use zero-drift
   covariance scenarios; directional recovery needs a separately trained and
   approved forecast. Do not reuse the SPY/QQQ signal model for a new ticker.
6. Start in shadow. Keep one execution owner, ordinary 15-second stock/120-second
   full solves, nonoverlapping execution plans and existing deadlines. Benchmark
   solve latency with the larger universe; do not claim capacity from ticker
   count alone. If latency or subscriptions degrade, retain the small validated
   set. New shorts still require verified borrow; no position is forced because
   NAV is below target.

This extension uses existing stock/ETF interfaces and needs no new account,
second trading computer or independent optimizer. It does require data and
validation. No empirical combined-option contest improvement has been established
by this correlation study. Only the first three hedge candidates were subsequently
applied; no new recovery signal or paper-recovery profile was promoted.
