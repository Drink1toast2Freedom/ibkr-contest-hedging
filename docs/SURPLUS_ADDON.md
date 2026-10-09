# Standalone surplus research add-on

This is the first isolated implementation of the surplus-reduction research plan.
It monitors the main app and evaluates bounded stock/ETF expense cycles. It has
**no broker execution capability**. IBKR paper expense generation is not enabled.
The official symmetric, interest-adjusted score and ordinary optimizer are unchanged.

## Start and use

1. Start the normal hedging app and its dashboard on port 8765.
2. Double-click `Start Surplus Add-on.command`, or run:

   ```sh
   .venv/bin/python -m surplus_addon serve
   ```

3. Open <http://127.0.0.1:8770/>. The default is **main app read-only feed**.
   It refreshes every five seconds; the page refreshes every two seconds.
4. To test the entire feedback loop, click **New synthetic rehearsal**, then
   **Run rehearsal automatically**, or step it with **Simulate one cycle**. Each
   completed fictional cycle advances the fictional clock by 15 seconds. Automatic
   rehearsal keeps monitoring when uncertainty prevents a cycle, replans after
   every completion, and reports the residual at the fictional close. **Stop automatic
   rehearsal** stops this runner; **Pause add-on** also stops it. **Advance demo
   1 minute** tests the entry cutoff when the automatic runner is stopped.
   No broker order is created and actual holdings are untouched.
5. **Use main app feed** returns to current account observations. **Pause add-on**
   pauses only this research monitor. It does not pause the main hedging app.
6. Stop the add-on by pressing Control-C in its terminal. The main app continues.

The main app must include the new read-only export endpoint. Older running
versions return an unavailable status until restarted. The add-on never starts
or changes TWS, OpenD, PostgreSQL, subscriptions or main-app trading mode.

## What it implements

- Reads only `GET /api/addons/surplus-input` from a localhost main-app URL. That
  export supplies the manifest, broker NAV anchor, provisional NAV estimate,
  official-rule target, exact positions, quotes and scenarios. It performs no
  new broker request or optimization. It exposes no API keys or database password.
- Validates matching account, snapshot/configuration/scenario identities, broker
  event revision, quote quality, freshness and current-session forecast horizon.
- Derives current gap, predicted closing gap, movement/valuation buffer, needed
  reduction, required and observed speed, modeled capacity, estimated completion
  time and capacity shortfall. It reports protected surplus separately from capacity
  shortfall; sufficient modeled capacity does not imply an exactly zero final score.
- Checks all integer slices up to the configured maximum for buy-and-restore stock/ETF cycles using the main
  instrument allowlist and existing slice, liquidity and spread limits. Options,
  crypto, deliberate extreme prices and market orders are absent from v1.
- Reuses the deterministic RiskGate independently for every integer partial-fill
  holding on the entry and restoration path. Mandatory quantities remain exact.
  Pending main-app orders and existing risk breaches take precedence.
- Requires expected absolute-score improvement above estimation uncertainty;
  retains a conservative residual buffer and a modeled overshoot probability cap.
- Uses separate start/stop thresholds and a near-close entry window. Day 0,
  market holidays, the entry cutoff and actual exchange closes, including early
  closes, prevent new proposals. No next-session forecast substitutes for today.
- Persists decisions and completed **synthetic** cycles in its own SQLite ledger,
  with exact Decimal loss/fee/turnover totals and snapshot/configuration links.
  Gains lower net loss speed. A configured fixed expense cap consumes gross expenses
  and adverse cycle loss; gains do not replenish that cap. The default allowance
  follows the remaining conservatively predicted surplus, already including prior
  cycles and any offsetting gains. Completed expenses already in NAV are not subtracted
  again in the closing forecast; accrued interest is already in IBKR NAV.
- Has an optional Google explanation panel and model selector. AI is off by
  default in this separate add-on. Enabling it uses the already configured local
  key; no key is displayed or sent to the main app. Advice has a 15-minute
  cooldown and never determines an executable quantity, price or budget.

## Defaults and assumptions

`config/surplus-addon.template.yaml` contains research defaults: no turnover cap,
no fixed dollar expense cap, 2,000 order equivalents, adaptive integer quantities
from one through 100, one serial cycle, a 15-second assumed cycle duration,
monitoring every five seconds, and entries from 30 to 2 minutes before the scoring
close. The per-cycle worst modeled loss reserve must fit the remaining surplus
above the movement buffer. This is a surplus-sized allowance, **not unlimited
permission to lose account value**. The default minimum movement buffer remains
$25, and existing liquidity, minimum-notional, risk and freshness checks still apply.
An optional `daily_expense_budget` sets a separately enforced fixed cap. Copy it to
`config/local.surplus-addon.yaml` and specify `--config` to change research inputs.
These caps are simulation settings, not authorization to execute paper orders.

The dashboard calls actual account observations **shadow** and fictional results
**synthetic**. The synthetic rehearsal starts 30 minutes before a fictional
scoring close with a $600 surplus and declared fictional structural option hedges
in four contracts at two neighboring strikes (13/12 short calls and 13/12 short puts). The exact
25 mandatory calls and puts remain held. The hedges reduce the simulated book's
forecast uncertainty so the full-window feedback loop can be exercised; they are
not purchased in IBKR and are not evidence of actual contest performance.
Every new rehearsal has a new fictional account identifier;
its budgets and records cannot be confused with the actual paper account.

Cycle descriptors use marketable **LMT/IOC** proposals, with cent-rounded entry
and exit-reference collars. IOC has **not** been added to the main execution
adapter. Hypothetical exits are requoted after the simulated holding interval;
the simulator does not establish that an initial fixed exit collar would fill.
The simulation uses a fixed-seed sampled stock move. It does not cherry-pick a
move that burns exactly the surplus. Explicit simulation clicks execute only
server-generated proposals after revalidation.

Price variation during a cycle is modeled from remaining-session scenarios using
square-root time scaling. Execution costs include spread, slippage, commissions
and tick rounding. The expense reserve and turnover estimate include unfavorable
modeled cycle moves. This is a finite scenario bound, not a guarantee for every possible
market move or fill. Turnover remains recorded in the ledger and never rejects a
proposal. Candidate capacity uses modeled net reduction, time and order capacity,
plus worst-cycle expense reserves if a fixed cap was configured. It ranks total
reducible capacity before immediate score improvement, and replans rather than
authorizing an unchecked series. **Empirically supported
paper capacity remains uncalibrated**, including fee/NAV timing and completion.
The required loss speed may exceed feasible modeled capacity; the controller
shows that shortfall. Uncertainty, the protected buffer, discrete quantities,
minimum commissions, minimum notionals and the entry cutoff can retain a surplus.
Removing turnover does not guarantee removal of an arbitrary surplus or an exact
closing NAV. The automatic runner advances a fictional clock only; the shadow feed
never runs simulated cycles or creates orders in a real or paper account.

Old custom configuration files with `daily_turnover_budget` must remove that field;
validation rejects it so an obsolete setting cannot silently remain effective.
To adopt the new surplus-sized allowance, set `daily_expense_budget: null`. Keeping
`daily_expense_budget: '25'` deliberately retains a $25 fixed cap.

Snapshot fetching has a three-second timeout. Numerical work runs outside the
async event loop with one evaluation in flight and no queued backlog. LLM work
is independent. The compute time displayed excludes snapshot acquisition and
cannot be interpreted as broker execution speed.

## Isolation and storage

The add-on imports shared configuration, valuation/scenario and risk utilities.
It does not import `hedge.broker`, `hedge.execution`, `ib_async`, or moomoo trading
contexts. It has no execution owner, broker socket or operational database DSN.
Its network reader makes GET requests to one fixed localhost endpoint and never
follows redirects. Its control schema exposes only research source, monitor and
simulation actions. There is no paper/live activation control.

Default ledger: `data/surplus-addon/research.sqlite` (Git-ignored, file mode 0600).
Independent AI preferences: `config/local.surplus-ai.json` (Git-ignored).
JSON export contains the most recent 30 decisions and 30 synthetic cycles. The
durable ledger retains older records. Stop the add-on before archiving the ledger,
including SQLite WAL files. Do not publish actual account research exports.

## Tests and future integration

```sh
.venv/bin/python -m surplus_addon validate
.venv/bin/python -m pytest -q tests/test_surplus_addon.py
```

The acceptance tests cover source isolation, no broker imports, read-only main
export, exact mandates, temporary inventory, unknown/stale state, cost accounting,
gain/loss attribution, budget reservation, target provenance, pauses, source
changes, early closes, simulations, API controls, and UI feedback.

Validation on this Mac, October 9, 2026: **291 tests passed**, including 72 add-on
checks and the isolated PostgreSQL integration check. The end-to-end automatic
rehearsal ran over 90 completed cycles, exceeded $3 million recorded turnover,
preserved all original holdings, and reduced the $600 starting surplus below $40
at the fictional close. The residual is reported rather than labeled zero. This
checks mechanics under the fixed synthetic seed, not real execution capacity or
contest performance. Tests also cover optional fixed caps, small final slices,
insufficient capacity, loss-reserve liquidity checks, automatic monitoring retries,
pause/source changes and rejecting automatic cycles on the read-only account feed.

The deployed browser rehearsal completed **97 cycles / 194 order equivalents**,
with $3,714,368.09 turnover, $194.00 simulated commissions already included in
$566.87 net cycle loss, and a **$32.68 closing residual**. Mandatory and initial
hedge holdings were restored after every cycle. The fictional book's calendar
mark movement also contributes to the closing NAV; commissions are not added to
net cycle loss a second time. The main application remained in PAPER mode.

An offline benchmark of the updated evaluator, five samples per workload, recorded:

| Synthetic workload | Scenarios / stresses | Median compute | Maximum compute |
|---|---|---|---|
| 7 instruments | 64 / 70 | 17.5 ms | 130.8 ms |
| Configured 24-instrument universe | 64 / 770 | 80.9 ms | 97.0 ms |

Both workloads produced bounded proposals, with all 101 partial holdings on the
selected 100-share path checked. These timings exclude HTTP acquisition,
scenario generation, LLM and broker execution; they measure computation only.
Reproduce with `.venv/bin/python ops/benchmark_surplus.py`, optionally adding
`--config config/local.practice.yaml`. Synthetic prices do not demonstrate
contest-score improvement or actual disposal capacity.

Before paper expense execution can be integrated, implement linked durable cycle
intents, real IOC/exit handling and fee attribution under the **existing single
execution owner**; validate adverse fills, failed exits, reconnect/cancellation
races and actual 4 p.m. NAV timing. Verify contest admissibility and demonstrate
useful measured capacity and score improvement. Neither this implementation nor
its synthetic results establishes that arbitrary surplus can be removed exactly.

See [the research plan](SURPLUS_REDUCTION_RESEARCH_PLAN.md) for the broader
evaluation and promotion requirements. Its pre-implementation benchmark is
historical; the optimizer has since been improved as recorded in
[optimizer runtime](OPTIMIZER_RUNTIME.md).
