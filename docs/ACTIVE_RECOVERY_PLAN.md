# Active recovery research and implementation plan

This is a proposed design. Writing this plan does not change the running strategy,
activate a new forecast, alter limits, or authorize a particular trade. The goal
is to evaluate evidence-based recovery opportunities in other securities while
preserving the mandatory BA options and the official contest objective.

## 1. Objective and the missing capability

For scoring session d, define the official target as:

    T_d = V_(d-1) * (1 + actual_daily_cash_benchmark_d)
    score_d = abs(V_d - T_d)

Use the existing separately labeled approximate benchmark only for forecasts
when the actual rate is unavailable. Actual official score calculation continues
to require the sourced daily rate. IBKR NAV includes accrued interest. Do not
subtract interest or fees again. Each day's baseline resets to its previous
closing NAV; profits on a later day cannot cancel an earlier day's score.

The current optimizer already includes e_t = estimated_NAV_t - T_d. Its market
return model has zero directional drift. More instruments and faster solves
therefore do not automatically provide profitable recovery opportunities.

Add forecasts for remaining-horizon returns and volatility, estimated from
market information available at decision time. The NAV deficit affects sizing
and portfolio selection, never the sign or magnitude of a market forecast.

For candidate combined hedge and recovery holdings h, compare:

    J(h) = expected abs(e_t + remaining portfolio P&L_s(h)
                        + remaining cash flows_s(h) - incremental trade costs(h))

Use the same accounting and independent risk gate as the existing optimizer.
Include the mandatory book, actual hedges, recovery exposures, working orders,
financing, option repricing, fees, slippage and correlations. Cash received from
selling an option is offset by an option liability; it is not immediate recovery.

Do not optimize raw expected profit, force expected profit to equal the deficit,
or promise to reach the target. Penalize overshooting and further losses through
absolute closing deviation. Retain overnight lookahead as a secondary selector.

## 2. Candidate strategies

| Candidate | Instruments | Hypothesis and signal | Relevant conditions | Principal failure | Priority |
|---|---|---|---|---|---|
| Intraday ETF momentum | SPY, QQQ; XLI/ITA when liquid | Trend persistence; opening return, recent returns, volume and market breadth predict remaining-horizon return | Persistent trends with adequate participation | Late entry and trend reversal | First |
| Intraday ETF reversal | SPY, QQQ, XLI | Temporary deviation reverses; standardized VWAP/factor residual and opening-gap features | Range or liquidity-driven dislocations | Mistaking a genuine repricing for a temporary move | First, competing with momentum |
| Relative-value pairs | QQQ/SPY, XLI/SPY, ITA/XLI; BA/ITA as a separate experiment | A historically stable factor-adjusted spread converges before the scoring close | Validated spread stability and intraday convergence | Relationships break or converge after the required horizon | Second |
| Directional option debit spreads | Qualified nonmandatory SPY/QQQ calls or puts | Express a validated directional forecast with bounded completed-spread exposure | Forecasted move exceeds spread costs and time decay | Forecast error, poor liquidity and incomplete leg execution | After stock/ETF signals pass |
| Volatility relative value and protected credit spreads | Nonmandatory BA options; later SPY/QQQ options | Option-implied volatility is rich relative to forecast realized volatility; earn modeled carry while controlling aggregate Greeks | Stable volatility conditions and measurable pricing discrepancy | Jumps, IV expansion, assignment and adverse gamma | Later |
| Gamma scalping | BA shares against the mandatory straddle; other long options only if justified | Rebalance delta as the underlying moves; realized movement may offset theta and execution costs | Sufficient actual movement and affordable execution | Theta, IV changes, spreads and hedge costs dominate | Useful benchmark, not a general independent recovery signal |

These are hypotheses to test, not current recommendations to buy or sell a
particular instrument. Momentum and reversal are alternatives conditioned on
market regime; do not automatically run opposite trades in the same symbol.
Daily correlation alone does not establish a pairs-trading signal. Recovery may
use TLT or GLD only if a separate rates/gold signal validates on the relevant
horizon, rather than because they diversify the ticker list.

Research motivation is not evidence of current performance:

- Gao et al. study first-half-hour returns predicting last-half-hour returns.
  That finding does not establish a profitable signal every 15 seconds:
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2552752
- Liu et al. study overnight/intraday reversal. Its horizon and feature definitions
  must be reproduced before using it as a strategy reference:
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2730304
- Gatev et al. study pairs trading using historical daily data. It does not validate
  same-session convergence for QQQ/SPY or BA/ITA:
  https://www.nber.org/papers/w7032
- OIC explains that option spreads constrain both reward and risk; assignment and
  leg execution can change the exposure:
  https://www.optionseducation.org/referencelibrary/faq/strategies
  https://prd-web.optionseducation.org/news/june-key-takeaways-the-wheel-strategy-and-credit-spreads

Exclude doubling positions after losses, forced daily profit quotas, random
security selection, and uncapped premium-selling as recovery mechanisms.
Leveraged/inverse products require a distinct validated model of their daily
reset behavior before activation:
https://syndication.finra.org/content/lowdown-leveraged-and-inverse-exchange-traded-products

## 3. First version to build

Related-security extension researched October 7, 2026:
[RELATED_SECURITIES_RESEARCH.md](RELATED_SECURITIES_RESEARCH.md) screens 24
symbols using 20/60/120 completed daily-return windows and primary-source business
and event information. Next research priorities are UAL and GE, plus XLE as a
conditional energy candidate. DAL/JETS, XAR and CVX are alternatives, not an
instruction to accumulate every related name. DAL's October 9 earnings event
requires special treatment in this practice. No ordinary stock screened provides
a strong, stable natural inverse BA relationship. SH remains an unapproved
inverse-market research benchmark requiring separate reset validation.

These candidates extend the hedge research pool, not the active execution
allowlist or SPY/QQQ recovery forecasts. Compare repriced combined option P&L and
official score after costs, validate intraday relationships and forecasts, then
use existing shadow/promotion gates. Correlation with BA shares alone is not
evidence of reduced straddle risk or expected deficit recovery. Retain the current
12-underlying cap and benchmark latency before enlarging an active universe.

Start with SPY and QQQ shares. Add XLI and ITA only after liquidity and signal
validation. Keep all currently held contracts in the combined risk calculation.
Use the existing qualified stock/ETF universe; other asset categories require
additional settlement, valuation and execution adapters.

Acquire one-minute historical bars for the preceding 20/60/120 sessions through
moomoo's quote-only API where permitted. Check coverage, adjustment methodology,
time zone, completed intervals, split/dividend handling and quota limits. Record
bid/ask observations going forward. Existing daily bars support covariance, not
intraday forecast validation. No paid data acquisition is assumed.

Train small deterministic models before complex models:

1. Remaining-session momentum regression with opening gap, opening return,
   5/15/60-minute returns, realized volatility and relative volume.
2. Reversal regression with standardized VWAP and factor residuals, overnight
   gap and liquidity/spread features.
3. Regime/profile selection estimated only from preceding data. Freeze selection
   rules before held-out evaluation. Shrink unstable forecasts toward zero.

VWAP and volume-derived features must document whether underlying data is
consolidated or venue-limited. Do not combine stock last-trade times with option
book freshness or pretend a connection heartbeat is a new market observation.

Forecast returns for the actual intended holding horizon: next 15/30/60 minutes
or the scoring close. Include model-estimation uncertainty, regime changes and
cross-instrument residual correlation, not just dispersion of simulated draws.
Use conditional historical residual resampling or a calibrated distribution.
Preserve fixed seeds, parameter versions and data-cutoff timestamps.

## 4. Coordinated controller

Maintain one execution owner and one combined optimizer. Hedge and recovery
labels are strategy attribution, not separate broker accounts or permission to
ignore offsetting positions. Net proposals for the same instrument and preserve
exact mandatory quantities. AI may explain proposals or select approved profiles;
it cannot choose numerical positions or change limits.

Suggested interfaces:

- SignalEngine: consumes point-in-time bars/features; outputs forecast horizon,
  mean, uncertainty, signal quality, regime and training-data version.
- RecoveryCandidateGenerator: generates bounded positions from qualified symbols,
  forecast strength and available risk capacity. A zero forecast produces no
  directional candidate.
- ScenarioEngine: combines forecast means and calibrated residual paths with
  option repricing and existing cash-flow assumptions.
- HedgeOptimizer: compares combined portfolios under the official absolute-distance
  objective; retains deterministic integer and transaction-cost constraints.
- RecoveryController: checks evidence and incremental benefit before invoking the
  existing RiskGate and ExecutionManager.

Every 15 seconds evaluate stock/ETF opportunities; every 2 minutes evaluate the
full option portfolio. New independent signals may request a solve, subject to
rate limits. Do not overlap working execution plans. Trades require a robust
expected closing-distance improvement above the larger of $25 and the forecast
uncertainty allowance, after modeled costs. The current 1.645 multiplier is not
a calibrated confidence guarantee, especially with nonnormal correlated errors.

For stronger protection against forecast overconfidence, evaluate an uncertainty
set of plausible forecast means and require improvement under a conservative
selection of those forecasts. Backtest this gate; do not bypass it because the
account is far below target. Monitor existing trades continuously and reconsider
when signals decay, prices move, fills occur, or the close approaches.

Near target, reduce speculative exposure and return to portfolio stabilization.
A proposed $250 no-new-recovery-entry band should be tested, rather than assumed
optimal. Above target, stabilize; do not deliberately spend money to remove gains.
On a new scoring session, recompute the target from the preceding official close.

## 5. Risk and feasible recovery

Keep current combined limits: 3% modeled stress loss, 20% residual dollar delta
per underlying, liquidity buffer, gross exposure, position caps and mandate
protection. Unavailable borrow prohibits new short-stock exposure. Qualification
and subscriptions for non-BA options must pass before using them.

For initial recovery trials, propose an additional 0.25% of B (about $2,500)
modeled incremental downside budget and an attributed daily loss stop of the
same size. These are proposed research settings, not changes to active limits.
Evaluate incremental recovery loss against the baseline hedge portfolio in each
stress scenario, while enforcing all limits on the actual combined portfolio.
Track attributed realized and marked P&L, with execution costs, in the local ledger.
After a stop, block new recovery entries; withdraw exposure only along a safe,
reconciled execution path. Never liquidate mandatory options.

Do not expand exposure as the deficit grows. If a forecast is +0.2%, an isolated
$100,000 equity exposure has only $200 forecast gross profit; recovering $4,000
would require $2 million exposure at that forecast before costs. A 20% adverse
move on that unhedged position is $400,000, incompatible with the current roughly
$30,000 combined stress budget. Spreads and diversification can change exposure,
but cannot make uncertain profit guaranteed. Report partial-recovery opportunities
and infeasibility explicitly.

For option spreads, validate every intermediate leg and assignment state. Long-leg
protection must be established before an exposed short leg if that path passes
all budgets; otherwise reject the trade or first validate an appropriate combo
execution adapter. Completed-spread payoff limits do not excuse temporary risk.

## 6. Validation and implementation gates

1. Data reliability: recover subscriptions after disconnects, refresh/reconcile
   account state, measure provider and receipt timestamps, and reproduce decisions.
   Stale held instruments block recovery. Faster trading requires this foundation.
2. Feature/data pipeline: import completed one-minute history, record live NBBO,
   and create provenance and coverage reports. No forecast may use future bars,
   today's later close, revised future corporate actions or future contract lists.
3. Forecast research: chronological walk-forward training, validation and held-out
   testing. Group splits by session and purge overlapping return labels. Compare
   momentum, reversal and no-forecast benchmarks without repeated test-set tuning.
4. Combined contest replay: compare no recovery, current hedge, momentum, reversal,
   pairs and ensembles using identical mandates, daily targets, costs and fills.
   Label bar-based execution assumptions separately from quote-based evidence;
   historical OHLC does not prove fills at a midpoint or at bar extremes.
5. Shadow operation: run the winning eligible model live without trades, with
   forecast calibration and candidate/risk explanations. A few practice sessions
   exercise operations but cannot establish a durable predictive edge.
6. Bounded paper trial: promote only a fixed model/configuration with improved
   held-out contest score and acceptable tail deterioration; preserve the
   recovery-specific budget. Fault-test disconnects, unknown submissions and
   partial fills. Escalate breadth only after evidence supports it.

Primary selection metric: held-out total official contest score. Also report
90th-percentile/worst contest score, further loss from the current deficit,
probability and amount of recovery, overshoot, forecast calibration, realized
costs, turnover, drawdown, margin usage, stress loss, and mandate compliance.
Compare entire contest windows, including previous-close resets and overnight
exposure; do not rank candidates by standalone Sharpe or profitable trades alone.
Historical cash benchmarks must be sourced; results using assumed rates are
reported separately and cannot be labeled exact official-score results.

Dashboard additions: active signal/regime and horizon, forecast confidence and
provenance, predicted hold/trade distance, target-band probability as a diagnostic,
remaining recovery budget, attributed recovery P&L, next evaluation, rejection
reason, and separate switches for hedge-only/shadow-recovery/paper-recovery.
The safe fallback is the validated hedge controller, with no speculative recovery
entries when forecasts or data quality fail.

## 7. Delivery order and recommendation

Implement data reliability and forecast measurement first, then the competing
momentum/reversal ETF models, then joint score-aware selection. Test pairs next.
Use validated directional forecasts to justify option debit spreads only after
quote history and execution paths are ready. Treat volatility strategies as later
research and gamma scalping as an option-book benchmark.

The actual practice calendar ends October 9. Historical and forward evidence
must be labeled honestly; the remaining practice sessions cannot establish
performance across many regimes. This plan aims to identify positive expected
recovery opportunities. It cannot promise recovery by a deadline.
