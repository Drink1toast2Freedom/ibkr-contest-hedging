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
   **Simulate one cycle**. This creates fictional fills and fees and advances the
   fictional clock by 15 seconds. **Advance demo 1 minute** tests the entry cutoff.
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
  reduction, required speed, modeled capacity and unremovable residual surplus.
- Checks a finite set of buy-and-restore stock/ETF cycles using the main
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
  Gains lower net loss speed. Consumed execution expenses and adverse cycle loss
  do not replenish budgets. Completed expenses already in NAV are not subtracted
  again in the closing forecast; accrued interest is already in IBKR NAV.
- Has an optional Google explanation panel and model selector. AI is off by
  default in this separate add-on. Enabling it uses the already configured local
  key; no key is displayed or sent to the main app. Advice has a 15-minute
  cooldown and never determines an executable quantity, price or budget.

## Defaults and assumptions

`config/surplus-addon.template.yaml` contains research defaults: $25 daily expense
budget, 100 order equivalents, $100,000 turnover, quantities 10/25/50/100, one
serial cycle, a 15-second assumed cycle duration, monitoring every five seconds,
and entries only from 10 to 2 minutes before the scoring close. Copy it to
`config/local.surplus-addon.yaml` and specify `--config` to change research inputs.
These caps are simulation settings, not authorization to execute paper orders.

The dashboard calls actual account observations **shadow** and fictional results
**synthetic**. The synthetic rehearsal starts six minutes before a fictional
scoring close with a $600 surplus so proposals, budget exhaustion and feedback
can be exercised. Every new rehearsal has a new fictional account identifier;
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
and tick rounding. Expense and turnover reserves include unfavorable modeled
cycle moves. This is a finite scenario bound, not a guarantee for every possible
market move or fill. Candidate capacity assumes completed serial cycles and
reserves sufficient expense/order/turnover budget for each. **Empirically supported
paper capacity remains uncalibrated**, including fee/NAV timing and completion.
The required loss speed may exceed feasible modeled capacity; the controller
shows that shortfall rather than enlarging limits or inventing trades.

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

Validation on this Mac: 196 existing checks and 64 add-on checks passed, including
the isolated PostgreSQL integration check. Browser controls were exercised through
one completed fictional cycle; its NAV reflected the loss and the main app stayed
in shadow with no new broker intent.

An offline benchmark, ten samples per workload, recorded:

| Synthetic workload | Scenarios / stresses | Median compute | Maximum compute |
|---|---|---|---|
| 7 instruments | 64 / 70 | 18.2 ms | 18.4 ms |
| Configured 24-instrument universe | 64 / 770 | 105.2 ms | 123.8 ms |

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
