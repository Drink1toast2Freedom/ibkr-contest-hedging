# Urgency-aware execution and protected Day 0 setup

The goal remains the instructor's absolute, interest-adjusted closing-NAV score.
These execution defaults are deterministic and configurable, not a claim of
empirically optimal order parameters. Live-money execution remains disabled;
moomoo remains market data only. Existing practice dates and filled holdings
are not changed by this update.

## Order selection

| Situation | Default behavior |
|---|---|
| Initial setup | Patient guaranteed option-pair limit orders; residual stock hedge prepared first |
| Ordinary stock/option adjustment | Tick-valid midpoint limit, followed by staged bounded repricing |
| Approaching ordinary submission deadline | More aggressive, price-capped marketable limit |
| Existing risk breach, liquid stock | Market order permitted only with a live book <=1 second old, spread <=$0.02, and both displayed sides >=5 times the slice |
| Existing risk breach, other cases | Aggressive limit within the execution budget; no blind market fallback |
| Wide, stale, unknown or insufficient quotes | Wait or cancel and reconcile |

Market orders have no guaranteed fill-price ceiling. Reserve 5 basis points of
stock notional for adverse execution, in addition to conservative commissions,
and repeat portfolio risk checks. This reserve is a model assumption, not a
maximum possible slippage. Independent broker-side policy checks reject ordinary,
stale or insufficient-depth market requests. Options do not use market orders in
this version. Near-close urgency never bypasses quote, mandate, margin or risk
requirements.

For ordinary orders, stock spreads must be <=$0.20 and <=0.2% of midpoint;
option spreads must be <=$0.50 and <=15% of midpoint. Both conditions apply.
This rejects the observed opening BA put spread of $7.25/$10.20. Quote books
from moomoo cover selected venues rather than a complete consolidated NBBO.

Normal stock repricing waits 15 seconds; options wait 60 seconds; protected
setup packages wait 120 seconds. Pricing moves from midpoint toward the executable
side in stages, with a fixed arrival-price budget derived from the spread and
modeled slippage. A moving market does not automatically enlarge that budget.
Stocks expire locally after 60 seconds, options after 180 seconds, setup packages
after 300 seconds. Cancelled remainders reconcile before the next decision.
Each price amendment and its quote/reason is durable, and broker observations
update the dashboard's displayed limit while preserving the original limit.
Market order prices are explicitly labeled references; package limits are net
prices per 100-share option unit. Orders can remain unfilled.

Ordinary stock orders need at least $1,000 of notional per proposed slice.
The optimizer also excludes smaller planned stock changes. Risk corrections
can use smaller quantities. A proposed slice must independently improve modeled
score after costs and uncertainty, with at least $1 modeled net improvement.
The unchanged full-plan $25 threshold still applies. Exposure changes must also
improve score in a zero-new-execution-cost diagnostic, so a trade cannot qualify
solely by burning fees to reduce a positive NAV gap. No artificial fee variable
or directional recovery forecast is introduced. Default slices remain at most
100 shares or one option contract; all plausible integer partial fills must
pass risk and any active recovery budget checks.

## Setup workflow

New manifests default mandatory purchases to Day 0. Supply Day 0, end date,
explicit scoring sessions and mandatory expiry for each round. The existing
practice's user-authorized Day 1 exception is retained. Setup uses the full
purchase-session opportunity, beginning after a configurable 5-minute opening
stabilization period and ending with a 1-minute close/reconciliation buffer.
Exchange calendars handle early closes. There is no obligation to buy at the
opening instant or to cross an unsuitable spread at the deadline.

Before any acquisition, the planner evaluates complete portfolios containing:

1. All exact mandatory quantities.
2. A matched, nonmandatory option hedge for every mandatory contract.
3. The initial residual stock delta hedge, subject to borrow and size constraints.

The current protected setup adapter supports standard US long equity options.
For each call it considers approved higher-strike calls; for each put, approved
lower-strike puts, at the same expiry, underlying, multiplier and trading class.
It evaluates up to four candidates per mandatory leg and at most 256 joint
combinations using the existing nonlinear-repricing scenarios. It selects the
lowest modeled next-scored-close absolute P&L subject to all combined-portfolio
limits; portfolios within $1 of that score are compared by modeled cost and stable
contract ordering. Day 0 expense is not itself an official scored return.
Unsupported mandates or missing protective contracts block automatic setup.

The complete plan, paired contract IDs, scenario identity, configuration hash and
source snapshot are recorded before transmission. Existing inventory adjustments
and the planned residual stock hedge are prepared first, with their partial fills
independently checked. Each new mandatory option then arrives in a guaranteed
1:1 option pair: BUY mandatory, SELL approved protective hedge. Each package
is a single SMART BAG limit order with `NonGuaranteed=0`. Two leg intents and
a parent transmission identity are persisted in one database transaction. The
broker's what-if preview must pass before transmission. An unsupported or rejected
package waits; neither leg is sent separately and no unhedged fallback exists.
Near the close, the package can begin at a marketable **net limit**, subject to
the same narrow-spread and price-budget checks.

Partial packages must preserve their ratio. Separate API leg callbacks can arrive
at different times: no subsequent order is submitted until both leg executions
and actual holdings reconcile. Aggregate BAG execution reports are not counted
again as assets or fills. Unknown transmission is never retried merely because
of timeout. Reconnect expands working combos into each leg's potential holdings.
Late executions still win over cancellation callbacks. Missing, excess or
unmatched holdings halt further purchases; mandatory positions are not sold to
repair feasibility. The old bare-mandatory submission API is disabled.

Completion requires exact mandates, planned protective hedges and stock hedge,
combined risk compliance, no unresolved orders, and broker reconciliation. A missed
setup deadline cancels remaining system-owned orders and alerts the operator; it
does not waive mandatory quantities or force an expensive market purchase.

"Hedged" here means matched structural protection at entry plus independently
bounded portfolio risk. It does not mean zero delta, gamma, vega, theta, assignment
or basis risk. IBKR guarantees the supported pair delivery; the application cannot
control external manual orders or turn paper simulation into a risk-free market.
Unmatched broker observations block new acquisitions instead of assuming a hedge
will eventually fill.

## Evidence

Software regression tests cover narrow/wide spreads, price ticks, urgent market
eligibility and dispatch, opening waits, early-close-compatible windows, complete
pre-purchase planning, paired fills, no bare fallback, rejected previews, unknown
submissions, late fills, amendment persistence/caps, tiny stock changes, and
fee-only apparent improvement. Synthetic package fills test mechanics, not
historical performance or real midpoint-fill availability.

On October 8, non-executing IBKR what-if previews accepted the BA 195C/197.5C and
195P/192.5P guaranteed option pairs without warnings. No setup order was submitted
for execution. A future paper setup must still forward-test actual package fills,
prices and recovery before claiming operational/performance optimization.

References: [IBKR guaranteed combinations](https://www.ibkrguides.com/traderworkstation/create-a-combination-order.htm),
[IBKR combo support](https://www.ibkrguides.com/traderworkstation/combo-trader.htm),
[IBKR paper limitations](https://www.ibkrguides.com/clientportal/aboutpapertradingaccounts.htm).
