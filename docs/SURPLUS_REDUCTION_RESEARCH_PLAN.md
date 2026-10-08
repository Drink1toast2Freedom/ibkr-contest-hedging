# Closing surplus reduction: research and implementation plan

Status: proposed research feature; no execution or configuration activation.
Research date: October 8, 2026.

The user confirmed that scoring uses NAV at the 4 p.m. ET market close.
Use the configured scoring-session close, with an explicit early-close convention,
rather than hardcoding dates or assuming an after-hours transaction counts.
The interest-adjusted target remains previous closing NAV times one plus the
official daily cash benchmark. This proposal does not change official scoring.

## Finding and scope

There is no verified standard IBKR trading instruction to reduce account NAV by
an exact chosen dollar amount. A marketable limit caps an unfavorable price; it
does not force execution at that price. Buying an asset exchanges cash for an
asset, and realizing an already marked loss does not deduct that loss again.

The plausible mechanism to investigate is actual execution expense: completed,
sequential purchases and sales against independent market counterparties incur
commissions and usually a spread. Price movement and improvement can change or
reverse the expected round-trip loss. This is an inference from ordinary order
mechanics, not an IBKR-endorsed surplus-removal product or a guaranteed loss.
Buying at an arbitrary inflated limit, selling at an arbitrary depressed limit,
and expecting those exact prices is not a supported mechanism.

References: [IBKR limit orders and price filters](https://www.interactivebrokers.com/en/trading/ordertypes.php?m=limitModal),
[IBKR stock commissions](https://www.interactivebrokers.com/en/pricing/commissions-stocks.php).

Research should evaluate a bounded, paper-only closing controller for small
positive residuals. It must be allowed to conclude that the feature has no useful
capacity or worsens scores. Do not assume it can dispose of a $100,000 surplus,
and do not change the main strategy to strongly pursue gains in anticipation of
an unvalidated disposal mechanism.

## Candidate mechanisms

| Mechanism | Assessment |
|---|---|
| Sequential round trips in eligible liquid USD stocks/ETFs | Candidate for measuring actual expense and precision; two fills and temporary inventory introduce risk |
| Normal beneficial hedge adjustment whose real costs reduce a surplus | Prefer when it also improves remaining-session or overnight risk; evaluate the complete resulting portfolio |
| Wide-spread options or illiquid assets | Exclude from the first experiment; nonlinear risk, unstable marks, uncertain exits and poor liquidity undermine control |
| Market-on-close orders | Not an exact expense mechanism; auction prices and timing do not provide a sequential completed round trip at one instant |
| Opposing own orders, coordinated counterparties, fictitious prints, or attempts to move closing marks | Exclude; these are not ordinary market expense mechanisms |
| Paper balance reset, cash transfer, or local ledger alteration | Exclude; no supported basis to treat these as a scored trading loss |

NYSE generally requires ordinary MOC/LOC entry by 3:50 p.m.; narrow imbalance
exceptions are not a dependable last-minute adjustment path. IBKR paper trading
also documents unsupported Auction order types and top-of-book simulated fills;
do not infer that every individual closing-order type is unavailable, but validate
specific support before any separate auction research.

References: [NYSE auctions](https://www.nyse.com/trade/auctions),
[IBKR paper limitations](https://www.ibkrguides.com/clientportal/aboutpapertradingaccounts.htm).

Contest admissibility of deliberately incurring expense is unresolved. Record
the instructor's interpretation before describing results as an eligible contest
strategy. This plan does not assert that ordinary sequential round trips are
illegal. It excludes own-order matching and artificial market activity; FINRA
Rule 5210 addresses bona fide quotations and transactions and member controls
against patterns of related-algorithm self-trading.

Reference: [FINRA Rule 5210](https://www.finra.org/rules-guidance/rulebooks/finra-rules/5210).

## Economics and practical capacity

For an incremental quantity q that is fully purchased and then sold:

    realized NAV reduction = q * (buy fill - sell fill) + actual commissions/fees

The expression can be negative if prices move favorably. While a leg remains
open, include its marked inventory and all working-order outcomes. Attribute
cycle cash flows separately from price movement in the pre-existing portfolio.

An illustrative unchanged $100 stock with a $0.01 spread, 100 shares per cycle,
and $1 commission on each order costs approximately $3 per completed cycle.
IBKR's published US fixed schedule is $0.005/share with a $1 minimum, subject to
caps and other charges. The Student Trading Lab account's actual reports must
calibrate its own schedule; the illustration is not a promised account rate.

At $3 per cycle, $100,000 requires about 33,334 cycles and 66,668 orders. That is
incompatible with assuming a precise final-minute adjustment. Even a $100
residual needs approximately 34 cycles under those assumptions. Larger size or
wider spreads introduces other constraints rather than guaranteeing capacity.

Measure the maximum useful capacity for the remaining window from observed
cycle loss distributions, displayed liquidity, order pacing, available funds,
partial-fill frequency, and conservative reconciliation latency. For a serial
controller, an initial timing bound is remaining seconds divided by the measured
95th-percentile complete-cycle latency. Count pending fees and unresolved orders
against capacity. A capacity estimate is a model, not a guaranteed amount.

## Valuation prerequisites

1. Preserve raw IBKR NAV, accrued interest and fees without duplicate deductions.
   Use the official daily benchmark; an approved approximate rate must remain
   labeled a forecast. Include benchmark uncertainty in target uncertainty.
2. Record account observation receipt time separately from broker valuation time
   when available. A newly delivered response does not prove a newly calculated
   NAV. IBKR documents periodic account updates, including a three-minute
   cadence for account summary; position changes can trigger account updates.
3. Anchor a provisional NAV estimate to IBKR and update it with timestamped
   quotes, reconciled executions and fee reserves. Maintain an explicit mark and
   latency uncertainty interval. Moomoo stays market data only.
4. Validate how executions and fee assessments just before the close appear in
   the organizer's closing value. Commission reports link actual charges to an
   execution ID; a what-if preview is an estimate, not a settled charge.
5. Compare close-time observations and modeled values with official reports.
   The current recorder requests NAV at/after the close within a 180-second
   capture window. Preserve this evidence but do not claim every observation
   within that window is the exact 4 p.m. value. Require report reconciliation
   when exact valuation cannot be established. Never overwrite a prior close
   with a later day's current value.

References: [IBKR account summary cadence](https://interactivebrokers.github.io/tws-api/account_summary.html),
[current account-update documentation](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/account-updates/receiving-account-updates),
[commission/fee reports](https://www.interactivebrokers.com/docs/tws-api/ref/commission-and-fees-report),
[order preview fee fields](https://www.interactivebrokers.com/docs/tws-api/ref/order-state).

## Deterministic controller

Define Z_s as the combined portfolio's predicted closing NAV minus today's
interest-adjusted target in scenario s. Define P_s(a) as the incremental NAV
effect of a feasible execution action, including partial fills, inventory price
movement, costs and exit failures. Evaluate:

    minimize sum_s probability_s * abs(Z_s + P_s(a))

Include no action. Require improvement beyond estimation uncertainty, plus
independent downside/overshoot limits. Do not substitute an arbitrary adjustable
loss variable for an executable action. Do not repeatedly deduct costs already
included in the anchored NAV. Jointly model the existing portfolio and adjustment;
their uncertainties need not be independent.

Before every cycle:

- A conservatively predicted closing surplus must remain positive after a
  reserve for portfolio movement, valuation uncertainty, pending expenses and
  the proposed cycle. A positive current dashboard gap alone is insufficient.
- The actual execution action must reduce modeled absolute score after its
  temporary exposure and completion risk are included.
- The action must fit a separately configured dollar expense budget, order-count
  budget, turnover budget, inventory limit and remaining time. These required
  inputs cannot default to unlimited. Completed positive losses consume the
  expense budget; favorable cycle gains do not replenish it.
- Retain mandatory quantities, current hard risk limits, fresh quotes, broker
  identity, margin checks, price collars, no duplicate orders and one execution
  owner. A pre-existing breach prioritizes a compliant corrective hedge instead.

Use one active cycle initially. Snapshot original inventory. Persist purpose-
tagged entry and exit intents before transmission. Submit a price-capped
marketable entry limit, confirm filled quantity and cancel/reconcile any remainder,
then submit an independently validated exit only for that filled increment.
Never sell more than the incremental confirmed quantity or cross own working
orders. Reconcile back to original inventory before another cycle. After partial
fills, rejections, unknown transmission, a disconnect or fee uncertainty, halt
new cycles and follow the existing recovery procedure. An order timeout is not
permission to resend an unknown submission.

The first version uses limits because a bounded loss budget needs price control.
Market orders are not inherently forbidden, but must demonstrate better completion
outcomes with acceptable unbounded-price risk in a separate validated profile.
They cannot be enabled solely to force a loss or bypass existing broker checks.

## Session timing

All times are relative to the configured scoring close; for this practice it is
4 p.m. ET. Proposed initial research schedule:

| Window | Behavior |
|---|---|
| Close minus 30 minutes | Reconcile, estimate remaining-session risk, calculate surplus/capacity and show a proposed plan |
| Close minus 15 minutes | Freeze new ordinary option adjustments under the existing policy; retain effective filled hedges |
| Close minus 10 to 2 minutes | Eligible surplus cycles only if validated capacity, budgets and score checks permit |
| Close minus 2 minutes | Stop new cycles and new ordinary stock submissions; resolve pending orders and temporary inventory |
| Close minus 1 minute | Final reconciliation and residual/uncertainty alert; no emergency loss chase |
| Close | Record provisional close-time evidence; reconcile to the instructor's scored value |

Increase the completion buffer when measured cancellation, fill or reconciliation
latency requires it. Do not submit a fresh cycle whose conservative completion
time extends beyond the buffer. A scheduled cutoff cannot guarantee that external
orders will fill or cancel before 4 p.m. Leave an unresolved residual rather than
forcing a large late loss. Preserve manual pause semantics: cancel system-owned
pending orders where possible, retain filled holdings and alert on temporary
inventory; do not silently liquidate after a user pause.

## Integration with the repository

- Add an optional validated configuration section with modes OFF, RESEARCH,
  SHADOW and PAPER; default OFF. Require explicit instrument and budget inputs.
- Add a separate SurplusController and execution-action evaluator. Keep the
  ordinary hedge optimizer's zero-new-cost improvement check intact.
- Add a narrowly scoped RiskGate purpose for surplus reduction. Replace the
  ordinary fee-only economic rejection for that purpose only with the controller's
  complete score/overshoot/capacity tests. Do not bypass common hard limits.
- Reuse ExecutionManager durability and reconciliation; add linked cycle intents,
  original inventory, fills, fee reserves and actual expense records.
- Coordinate ordinary hedging and cycles under the same account execution lock.
  Risk correction has priority; the AI supervisor cannot invent these trades or
  increase budgets.
- Add dashboard surplus, uncertainty, estimated removable capacity, budget used,
  cycle status, observed charges, expected score improvement and blocked reason.
  Show remaining surplus as a possible scored outcome, not a guaranteed zero.
- Record model and raw API close evidence separately from final reconciled NAV.
  Add all inputs/configuration versions to replay and decisions.

## Validation and rollout gates

1. Research-only replay: compare no adjustment, normal hedging, and bounded
   surplus reduction through identical quote-based execution assumptions. Include
   price improvement, widening spreads, delayed fees, late fills, partial fills,
   stale NAV, manual trades, clock skew, reconnects and already-breached risk.
2. Test mechanics: exact accounting, no double fee deduction, unchanged mandatory
   positions, restoration of incremental inventory, no conflicting own orders,
   no unknown-order retries, hard budgets and cutoff behavior. No-action must win
   when estimated surplus is below uncertainty or capacity is insufficient.
3. Forward shadow: measure actual near-close data availability and estimate error,
   execution latency and achievable capacity without submitting surplus orders.
4. Separate controlled paper pilot after the research and contest interpretation
   are resolved. Propose a $25 total expense cap for the initial experiment,
   subject to the operator's eventual chosen configuration; this document does
   not activate that budget or authorize trades. Verify charges and timing from
   broker reports rather than trusting simulated expected costs.
5. Promotion: require improved held-out official absolute scores, acceptable
   overshoot frequency/magnitude, no mandate/risk violations, resolved fee/valuation
   timing, and measured useful capacity. Keep synthetic, historical-quote and
   paper-forward evidence distinct. Failure means retain OFF/SHADOW.

Only after capacity is demonstrated should research consider a stronger preference
for positive deviations. Model disposal as finite and uncertain, with failures,
instead of deleting the positive side of the scoring objective. Small-surplus
results cannot justify taking six-figure intraday profit targets.

## Dynamic feedback controller and LLM assistance

The user requested continuous measurement of the remaining surplus, realized
reduction speed and projected loss, with LLM assistance. Use receding-horizon
control: evaluate the complete remaining-session distribution repeatedly, execute
only the next independently validated action, observe the result and replan.
Dynamic control improves adaptability; it does not create guaranteed loss
capacity or make uncertain closing marks exact.

### Measurements

Maintain one versioned state containing:

- Official target and rate provenance; approximate targets remain labeled.
- Broker NAV anchor, its observation time and broker valuation time when known;
  current provisional NAV and mark/latency uncertainty.
- Current NAV gap and predicted closing gap before any *new* adjustment. Include
  expected movement in all filled holdings and every pending execution outcome.
- Confirmed adjustment-cycle loss, actual fees, provisional fee reserve, temporary
  inventory and unfilled orders, separately from P&L of the pre-existing book.
- Observed completed-cycle throughput and net loss per elapsed second, using a
  robust rolling estimate. Favorable cycle P&L must reduce measured net loss;
  never clip gains out of the speed measurement. Keep gross consumed expense
  budget separate so gains cannot replenish hard budgets.
- Distribution of time to complete and reconcile each cycle, and empirical loss
  dispersion. Pending callbacks and marks are not completed-cycle evidence.
- Time remaining to the new-entry cutoff, remaining daily/order/turnover budgets,
  conservative removable capacity, and the probability of finishing below target.

Do not obtain adjustment speed simply by differentiating total account NAV:
market movement in mandatory holdings could look like successful expense
generation. Attribute tagged cycle fills and fees to the adjustment mechanism.
Update the counterfactual unadjusted book with the same quotes and cash flows,
and invalidate it when ordinary hedging or a manual trade changes its inventory.
Never count a pending fee again after it appears in the broker NAV anchor.

For explanatory telemetry:

    needed reduction = max(0, predicted closing gap before NEW actions - buffer)
    required speed = needed reduction / seconds remaining for NEW cycles
    achievable capacity = measured net loss per completed cycle
                          * conservatively feasible remaining cycles

Use distributions, rather than only those point estimates, for order decisions.
Existing completed losses already reflected in NAV are not subtracted again.
Committed but not reflected fees/inventory effects enter the forecast once.
If time is zero, the required-speed metric is unavailable and new entry is blocked.
If the rate is uncalibrated or nonpositive, no useful removal capacity is assumed.

### Actions and cadence

In SHADOW, recompute proposed actions every five seconds and after a fill,
commission, meaningful quote change, reconciliation or target revision. Coalesce
quote events; do not solve or query the full broker account for every tick.
Throttle broker requests under its documented pacing allowance, and keep
optimization and LLM work outside the event loop.

Consider no action, continuing the active cycle, a smaller/larger validated slice,
adjusting a bounded limit toward executable prices, a validated change in cycle
interval, or a validated instrument/profile switch. Size and speed may increase
only inside the existing independently checked budgets and empirically supported
completion capacity. Permit at most one cycle initially; the controller does not
create parallel cycles merely because the required speed exceeds observed speed.
Holding an OTM option is not a completed loss; its entire closing-value
distribution must enter the forecast. Automatic switching into short-expiry
options requires separate evidence and a validated profile and is absent from
the first fee-based implementation.

Prefer reductions whose projected score improvement exceeds model uncertainty.
Require a conservative positive residual after the proposed loss and movement
buffer, subject to an explicit modeled overshoot probability ceiling. Use
hysteresis: one threshold to start/resume and a tighter threshold to stop, so
small changes around the target do not cause repeated entry. Recalibrate the
buffer upward when model/IBKR marks disagree or latency worsens. Do not remove
the buffer to manufacture feasibility. Risk corrections take priority.

Illustration: current surplus is $1,000, remaining-book forecast is a $500 loss,
and $200 of adjustment expense is committed but not yet reflected. Only $300
remains before the uncertainty buffer. An additional planned $1,000 loss would
overshoot; continuously monitoring current NAV alone would miss this. If the
needed reduction exceeds measured remaining capacity, show the unremovable
residual and retain a no-action/smaller-action option instead of chasing it.

Use lifecycle states OFF, MONITORING, PROPOSED, EXECUTING, RECONCILING,
WAITING_FOR_CERTAINTY and CLOSED, with a reason on every transition. The normal
session-close entry cutoff stops new cycles; existing exit/reconciliation work
can finish within its validated buffer. Manual pause retains the existing
cancel-and-hold behavior. Reconnect requires reconciliation before any new cycle.

### LLM boundary

The current Google supervisor is advisory, runs on a fifteen-minute cooldown,
and accepts only STANDARD as a validated profile. No expense-generation profile
is approved or activated. Do not describe this as an existing dynamic loss
controller. The new feature needs an explicit supervisor input/schema extension.

Send an allowlisted summary of surplus, uncertainty, achieved/required speed,
estimated capacity, latency, budgets, data-quality flags and decision reasons.
Exclude account identity, credentials and arbitrary broker strings. The LLM may
explain why a target is unlikely to be reached, flag anomalies, or recommend one
of the separately validated profiles. It cannot output executable quantities or
prices, expand budgets, change mandates, invent a loss forecast or activate paper
execution. Its proposed profile is a request that deterministic checks may reject.

Retain the existing advisory cadence initially. Later compare a rate-limited
few-minute or anomaly-triggered review on evidence; faster numerical control
must never wait for inference. Use bounded structured output, no overlapping
requests, explicit configuration/snapshot identity and an expiry. An invalid,
late, stale or failed recommendation leaves the validated deterministic profile
in control. Unvalidated options-loss profiles cannot be suggested into activation.

### Dashboard and acceptance

Show current and projected closing surplus, uncertainty band, confirmed/pending
expense, achieved and required reduction speeds, remaining feasible capacity,
completion probability, budget and active cycle. Label projections as estimates.
Distinguish the ordinary portfolio's loss from controller-induced loss and show
the LLM's recommendation separately from the actually applied profile.

Add tests for shrinking/growing surplus, portfolio movement independent of fees,
late fee reports, re-anchoring without double deductions, favorable round trips,
capacity shortfall, rate discontinuities, target revisions, uncertainty inflation,
cutoff crossing, pause/reconnect, and invalid/stale LLM selections. Compare the
dynamic controller with fixed-size/fixed-interval adjustments and no adjustment
on the same quote-based and forward-shadow sessions. No promotion until measured
official-score improvement and all prior risk/execution gates are satisfied.

## Deliverables and decision

### Practicality, timing and existing-app integration review

An offline computational benchmark used the configured 24-contract universe
(14 options, 10 underlyings), synthetic demo quotes, mandatory-only inventory,
a $1,000,500 NAV and 15:40 ET on the first scoring session. No broker, database,
LLM or network execution was used. This is a deliberately synthetic workload,
not a representative live portfolio or a measured trading-performance result.

| Existing component | Samples | Median | Maximum |
|---|---:|---:|---:|
| Complete scenario build: 64 ordinary rows, 770 stress rows | 5 | 173.789 ms | 178.630 ms |
| RiskGate validation using the existing scenario set | 30 | 0.041 ms | 0.203 ms |
| Evaluate an existing target against the scenario set | 30 | 0.097 ms | 0.396 ms |
| Stock-only mixed-integer solve | 3 | 10.098 s | 10.117 s |
| Full mixed-integer solve | 3 | 10.130 s | 10.156 s |

All six optimization runs returned USER_LIMIT and are unsuccessful solutions,
not executable targets. These results demonstrate that full optimization cannot
be assumed to finish inside a five-second controller interval. They do not
establish typical runtime with actual quotes; measure that separately in shadow.
The simple evaluation timings exclude quote acquisition, fee accounting, order
previews, partial-fill path enumeration, database writes and network latency.

The existing collector publishes its latest Moomoo snapshot approximately once
per second; this does not prove one-second freshness of every instrument. The
dashboard polls every two seconds. The monitor waits for scenario/optimizer and
execution processing and then sleeps for the configured five seconds. Thus its
actual period is work time plus five seconds, and a broker preview or solve can
delay the next risk/management pass. Preview requests have a fifteen-second
timeout, regular reconciliation has a thirty-second timeout, and ordinary stock
orders currently reprice after fifteen seconds and expire locally after sixty.
Local order expiry starts cancellation; it is not confirmed cancellation.

Implement a separate lightweight event-driven surplus evaluator, with a
five-second periodic fallback. Reuse the latest versioned scenario set and require
freshness; refresh scenarios as necessary outside the event loop. Evaluate a
small finite action set rather than running a new mixed-integer portfolio search
at every tick. A sub-100-ms compute budget is a proposed engineering target,
not a measured controller result or a broker latency guarantee. Coalesce events,
allow only one evaluation in flight, and discard obsolete work rather than
accumulating queued decisions. Do not interrupt a worker and assume its thread
has stopped; bound or discard outstanding computation safely.

Use the same broker client, execution owner, mandate registry, risk gate,
NAVTracker, database, quote collector and dashboard. Add no second trading process.
Introduce an arbiter with priority: pause/fault handling, mandatory setup when
applicable, corrective risk work, ordinary hedging, then surplus adjustment.
Restrict ownership of a cycle's instrument while its entry/exit is unresolved,
but do not block unrelated risk monitoring or wait behind an LLM call. Existing
exits and required cancellations must be accounted for when preempting a cycle.

Keep atomic submission/intent checks under the common execution lock. Avoid
holding a decision lock across slow network previews where possible; perform
the preview, reacquire/check the execution reservation and revalidate fresh
state before transmitting. Retain preview requirements and existing rate limits.
The account lock serializes submissions; it does not make two-leg execution
atomic or fill/cancel acknowledgement instantaneous.

Add a monotonic fill/commission/order revision to decision validity checks.
The present optimizer compares current quantities and working orders after a
solve. A completed round trip can return both to their original values while
fees and NAV change. Invalidate or reevaluate a target when this revision
changes even if holdings look identical. Tag adjustment intents by purpose so
ordinary hedge targets cannot mistake temporary inventory for a permanent hedge.
The new purpose-specific economic check must not remove the ordinary optimizer's
fee-only-improvement protection. IOC support also needs explicit broker/order
model additions and forward validation; it is not currently implemented.

Illustrative capacity for an eight-minute new-entry window and $3 net loss per
fully reconciled cycle:

| Hypothetical full-cycle time | Maximum serial cycles | Approximate reduction |
|---|---:|---:|
| 5 seconds | 96 | $288 |
| 15 seconds | 32 | $96 |
| 30 seconds | 16 | $48 |

These are arithmetic examples, not observed throughput or upper bounds under
different sizes. The complete cycle includes two fills, accounting and
reconciliation; callbacks and pending fee reserves require measured latency.
Risk interruptions, price changes and failed exits reduce useful capacity.
There is no software-speed argument that establishes six-figure disposal capacity.

Practical verdict: monitoring, dashboard telemetry and a small finite-action
controller are feasible with moderate integration work. The harder tasks are
reliable NAV/fee attribution, pre-close completion and proving score improvement.
Keep the first version stock/ETF and shadow-only, with no automatic switching
into short-expiry options. The LLM remains off the execution path; its current
fifteen-minute cooldown and thirty-second request timeout are inappropriate for
per-cycle price/quantity control. Measure event-loop lag, quote age, decision
runtime, broker acknowledgement, full-cycle latency, fee delay and score/overshoot
results before enabling any automatic expense generation.

Deliver a valuation-timing report, a capacity/expense distribution report, replay
comparisons, fault-test results, a purpose-scoped controller, dashboard controls,
and an operator runbook. The implementation decision is conditional: small-surplus
expense adjustment may be useful; exact arbitrary-dollar loss at the close is
unverified. No application code, trading mode, holdings or limits were changed
when writing this plan.
