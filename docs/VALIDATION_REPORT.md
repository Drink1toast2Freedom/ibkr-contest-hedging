# Validation report — October 7, 2026

The updated automated suite passed **104 tests**, with **0 failures, 0 errors,
0 skips**, including a fresh PostgreSQL database and Alembic migration. The
suite ran in approximately 27 seconds. One nonblocking Starlette/httpx
TestClient deprecation warning remains. This is evidence of implementation
behavior, not a guarantee of trading performance or maximum loss.

## Evidence

| Feature group | Result | Scope |
|---|---|---|
| Dependencies, syntax and lint | Passed | Both Python environments pass pip check; Python compilation, Ruff, JavaScript syntax and control regression tests pass |
| Scoring and manifests | Passed | Official daily-rate benchmark, missing-rate behavior, separately labeled forecast fallback, Day 0 exclusion, revisions and exact decimal accounting |
| Database | Passed | Alembic migration and PostgreSQL decimal ledger in a temporary database; operational database untouched by test fixtures |
| Valuation and optimization | Passed | American options, multiplier, dividends, Greek units, integer constraints, cost accounting, limits, infeasibility and deterministic optimizer tests |
| Research and imports | Passed mechanically | DataShop normalization, no future-history access, historical contract matching, preserved scoring rule, frozen/delayed research rejection |
| Execution and recovery | Passed in simulation | Durable intents, partial fills, cancel/fill races, unknown submissions, mandatory protection, working-order data loss and duplicate-event handling |
| Full running-app workflow | Passed in isolated simulator | 50 mandatory option buys plus 3 initial stock slices, exact 25 calls/25 puts, OPTIMAL hedge proposal, pause/shadow/paper controls, rate/NAV imports and CSV exports |
| Restart and ownership | Passed | Journal and scores survive isolated server restart; restart stays paused; a second execution owner is rejected before its broker connects |
| Dashboard | Passed | Pages/assets, server control endpoints, stale-token recovery, visible errors, confirmation dialog, CSRF and origin rejection |
| Google AI | Passed | Live Gemini 3.8 Flash request using synthetic input; schema/profile checks, privacy allowlist, rate bounds, failure behavior and no execution tools |
| IBKR connection | Passed read-only | Paper-account identity, account values, positions and contract qualification |
| IBKR margin preview | Passed non-fillable | Exact corrected adapter returns a finite margin change without warning; no fillable order submitted |
| Moomoo | Access/metadata passed; intraday validation pending | Quote-only stock LV3 and option LV1 rights, option identities, continuous collection and fail-closed quality tests |

The end-to-end simulator uses fictional contract IDs and prices and a one-second
monitor interval to accelerate construction. It has no IBKR or moomoo broker
connection and no AI credentials. Its fill results are not empirical execution
or contest-performance evidence. The simulator resets its simulated inventory
on process restart; live portfolio inventory remains IBKR's responsibility.

## Defects found and corrected

1. A moving initial stock target could churn stock orders and starve mandatory
   option construction. Freeze that target during setup; revalidate risk for
   every slice and working order. Ordinary hedge optimization resumes after
   mandatory construction reconciles.
2. Quote archives used recorder receipt time as market time. Preserve provider
   observation time separately from recording time and retain provider identity.
3. Historical contract matching silently defaulted to raw NAV scoring. Preserve
   the configured official scoring rule and available dated benchmark rates.
4. Working orders could be repriced after quotes became non-live. Cancel on
   missing, stale, future or non-live data; preserve cancellation/fill recovery.
5. Working-order projected risk checks covered ordinary hedging but omitted
   setup. Apply them during setup with its exact mandate-construction rules.
6. The margin preview omitted DAY time-in-force, triggering IBKR error 10349.
   Match the execution order's explicit DAY and regular-hours settings.
7. Reject stale or non-live new hedge candidates before optimization, rather
   than selecting an unexecutable target and relying only on its final rejection.
8. A fresh option stream must also have a fresh, permitted underlying feed
   before its model Greeks become executable.

Regression tests cover these fixes. Tests and hypothetical inquiries did not
submit, fill, cancel or replace any operational IBKR trading order.

## What is not established yet

- Fresh moomoo stock and option streams during regular market hours. US book
  exchange timestamps are unavailable; eligibility requires verified rights,
  independent fresh provider prices and subsequent book pushes. Sparse option
  updates can legitimately block trading.
- Actual IBKR paper fills, liquidity, slippage, margin behavior for every hedge
  instrument, assignment, and reconnect behavior with a nonempty paper book.
- Historical or forward-test improvement in total contest score. No adequate
  historical option-NBBO archive has been supplied. Scenario and financing
  assumptions remain disclosed models.
- Automatic retrieval of the organizer's actual daily cash benchmark. Approximate
  3.35% is authorized only for labeled forecasts; official scores require sourced
  dated rates.
- Real-money execution: disabled by design. Moomoo trading: no trade adapter.

## Reproduction and local artifacts

Run `ruff check hedge tests ops`, `node tests/dashboard_controls.cjs`, and
`python -m pytest -q` in the application environment. Set TEST_POSTGRES_URL to an
isolated PostgreSQL database to include the database test. Do not point tests
at the operational database. CI already provisions PostgreSQL separately.

Local detailed results are in `reports/validation/`: JUnit XML, synthetic
end-to-end checks, restart checks, read-only IBKR checks, exact adapter margin
preview, and the synthetic Google live-request check. These reports, local
account configurations, credentials and data archives are ignored by Git.

## Intraday correction — quote timestamps

The market-hours check revealed that `data_time` describes the latest price
update, while bid/ask callbacks continued arriving for the same option. The
adapter now dates executable quotes from actual book callbacks (and conservative
server timestamps when supplied), independently of last-price timestamps.
Collector heartbeats do not refresh quotes, and cached reads are reevaluated
against the current clock. Underlying valuation uses a fresh book midpoint.

Live option IV is a model estimate calibrated to the current midpoint with the
same American-option/dividend model; Greeks are derived from that calibration.
The bounded solve rejects unidentifiable or inconsistent prices. Old vendor IV
is never promoted to freshly observed IV. Permission, contract-identity, stream
confirmation, book-age and risk limits remain enforced.

The complete updated suite passed **106 tests with no failures or skips**,
including an isolated PostgreSQL database. A read-only collector sample showed
all 15 instruments eligible, including both mandatory options, before deployment.
After restart, streams must accumulate fresh callbacks again. The application
remains paused; these checks do not authorize or submit additional orders.

## Corrective activation and operator delta limit

The paper-enable control now generates a fresh proposal when the existing
portfolio breaches a risk limit and paused mode has no proposal. It requires
a fully compliant target and at least one independently validated first
execution slice before activating. Each subsequent slice is still gated; an
intermediate breach is permitted only when it improves an existing breach,
does not worsen another breach, and creates no new violation. No stale target
is reused as authorization. Mandatory quantities remain exact.

The operator selected a **20% residual dollar-delta cap** for the local practice
configuration, replacing 10%. The normal configuration default remains 10%;
20% is the maximum accepted configuration value. The 3% stress-loss cap and
all other limits remain unchanged. This limit controls sensitivity to the
underlying, not a maximum possible loss.

Regression checks cover activation without a prior target, an actual simulated
risk-reducing fill, stale-data rejection, an unavailable execution path, and
validation of the operator limit.

The final full regression run passed **109 tests with zero failures or skips**,
including the isolated PostgreSQL integration test.

## Operator freshness adjustment

At the operator's request, option book freshness is now **60 seconds**, replacing
30 seconds in the application default and local practice configuration. The
stock-book limit remains 5 seconds; the Greek limit remains 60 seconds. This
accepts older option books and therefore increases exposure to quote changes
between updates; permission, identity, spread, liquidity and risk checks remain
in force. Boundary regression accepts a 60-second option book with a fresh
underlying and rejects it at 61 seconds. The focused feed/valuation/optimizer/risk
suite passed **31 tests**.

## Expanded US stock/ETF universe

Qualified six additional ETF contracts read-only through IBKR (SPY, XLI, ITA,
QQQ, TLT, GLD). Moomoo supplied 1,162 prior completed daily bars across those
ETFs and BA. The optimizer now has 21 configured instruments, including all
previous contracts and held hedges. Joint daily-return covariance is regularized
and excludes today's incomplete bar; intraday/IV scaling remains modeled.

The full suite passed **113 tests, zero failures or skips**, including isolated
PostgreSQL. Additional coverage checks read-only discovery, mandate preservation,
no-lookahead daily scenarios, reproducibility, missing-history rejection, and
unavailable stock borrow constraints. See EXPANDED_UNIVERSE.md for scope and
reproduction. These results establish mechanics, not a guaranteed NAV target or
held-out contest improvement.

The expanded live collector returned all 21 instruments with no feed-quality
notes in a market-hours sample. Shadow optimization returned OPTIMAL, and paper
mode resumed with passing held-portfolio risk checks. In that sample the new
ETFs were not selected: the best modeled improvement was below the unchanged
$100 threshold. Do not infer that adding instruments necessarily increases
trading or recovers existing NAV deviation.

## Faster target-aware paper profile

The operator selected stock/ETF evaluation every 15 seconds, full-portfolio
optimization every 120 seconds, and a $25 score-improvement floor. Uncertainty
allowances and hard risk limits remain enforced. The dashboard and decision
records expose the full NAV gap, hold-versus-trade expected closing distance,
optimization scope, and the specific blocking reason.

Regression checks cover continuing evaluations after previous trades, independent
full-portfolio scheduling, nonoverlapping execution plans, and using a $4,000 NAV
deficit in the optimization objective. The positive-return deficit fixture
validates mechanics only; it is not a market prediction or evidence of recovery
performance. See FAST_PAPER_POLICY.md.

The final full suite passed **116 tests with zero failures or skips**, including
isolated PostgreSQL integration. JavaScript syntax, dashboard-control regression,
Ruff and whitespace checks also passed.

During live activation, an overlapping broker reconciliation timed out. Broker
and service downloads are now serialized; timeout/disconnection returns an
explicit 503 blocked-activation response instead of a raw server error. A
concurrency regression confirms only one IBKR download is in flight.

After the reconciliation fix, the complete suite passed **117 tests with zero
failures or skips**, including isolated PostgreSQL.

## Forecast recovery first release

Implemented SPY/QQQ momentum and reversal, model integrity/cutoff checks, clustered
parameter uncertainty, current-price rebasing and matched-clock volatility floors;
bounded combined optimization; independent per-slice budgets; corrected-fill P&L
attribution and persistent daily loss baselines; quote-replay integration; and
CSRF-protected off/shadow/paper controls. First-release advanced-strategy scope and
missing evidence are documented in RECOVERY_RUNBOOK.md.

The final complete suite passed **129 tests with zero failures or skips**,
including isolated PostgreSQL. Eleven recovery tests cover future exclusion,
integrity, stale/sparse bars, promotion rejection, budgets, mandatory quantities,
execution-slice limits, P&L baselines, shadow isolation, price rebasing and
bar/quote discontinuity rejection. Ruff and whitespace checks passed.

Moomoo supplied **97,113 minute observations** for SPY/QQQ. Chronological research
used 13 fixed-model folds and 64 complete held-out sessions per strategy. Forecast
RMSE improved slightly relative to zero-mean forecasts, before trading costs;
this is not empirical recovery profit or total-contest performance. Bar timestamp
semantics and venue volume require verification. The profile is deployed as
**shadow recovery** and rejects bar-only evidence for paper activation.

The final live-feed correction dates complete books by the latest reported
bid/ask-side update bounded by actual callback arrival, rather than requiring
both sides to change. Regression confirms one unchanged side cannot stale a
fresh book, while a book with both source sides old remains old. Freshness caps
are unchanged. Approval changes cancel an active recovery plan before further
submissions. Live shadow produces four forecasts and independently checked
candidates; bar-based evidence is rejected for paper recovery.

Operational status at final verification: ordinary paper trading is paused.
Both the application and an independent read-only IBKR client received account,
position and execution synchronization timeouts despite the reachable TWS socket.
No fresh reconciliation or new recovery execution is claimed. Restoring the
authenticated Paper TWS session and passing reconciliation are required before
ordinary paper activation. Recovery remains shadow pending empirical promotion.

## Related stocks applied

UAL, GE and XLE were qualified read-only through IBKR and deployed as ordinary
hedge candidates, with aligned ten-symbol history and quote-only moomoo
subscriptions. The 37 focused universe/valuation/optimizer/risk/feed tests passed,
including a new measured-sign scenario and combined-limit regression. No
production risk limit, scoring rule or recovery forecast was changed.

A synthetic seven-versus-ten-symbol fixture returned OPTIMAL for stock and full
solves within the deadline, preserving mandates and numerical risk limits. The
ten-symbol stock/full solves took 0.68/1.41 seconds; this is mechanics/latency
evidence only. See EXPANDED_UNIVERSE.md for inputs and restrictions.

IBKR synchronization recovered: reconciliation verified mandatory holdings,
zero working orders and zero unresolved intents before deployment. The restarted
dashboard shows UAL, GE and XLE; main operation and recovery are shadow after
market close. Fresh execution data will require regular-session verification.
Crossed after-hours samples were rejected, not relabeled live or repaired to
create apparent fills. No new positions were acquired during deployment.

## Activation prerequisite diagnosis, October 8

The paper portfolio's BA dollar delta exceeded its configured 20% cap while
October 7 closing NAV was absent from the ledger. Optimization returned without
a target, so the activation handler misleadingly reported only the current
delta breach as an unavailable corrective hedge. No score-aware decision could
be constructed without that session's previous closing value.

A shared closing-target preflight now requires the correct previous session's
NAV and actual benchmark or approved forecast before risk-correction optimization.
Missing inputs clear stale targets and produce an explicit dashboard/control
reason. Day 0 still uses the unscored setup reference; the old Day 0 baseline
is not substituted for later missing closes. Limits and mandates are unchanged.

All 132 tests passed, zero failures or skips, including isolated PostgreSQL.
Regressions verify missing-baseline precedence over a delta breach, clearing old
targets, missing-rate diagnostics, and successful independently gated corrective
activation after the baseline is imported. Stale quotes and unsafe initial
slices remain rejected. No source-less closing NAV or forced recovery trade
was created to make activation pass.

## Automatic closing capture

The supplied October 7 statement NAV ($995,294.19) was imported. Automatic
exchange-close recording requests a fresh scoped IBKR NetLiquidation response,
independently of trading mode and quote quality. Capture/retry windows, account
identity, finite values and timestamps are validated; records retain API
provenance and can be revised from statements. Existing closes win; stale or late
startup values cannot backfill a missed session.

All 155 tests passed, zero failures or skips, including isolated PostgreSQL.
New coverage includes early closes, weekends, restarts, disconnect/retry,
pre-close/late/nonfinite/wrong-account/wrong-source observations, statement races,
fresh summary events rather than cached values, cleanup, startup/reconciliation
serialization and qualification timeouts. JS/control checks and Ruff passed.
The fresh account-summary query also succeeded with a separate read-only IBKR
client without writing a closing ledger entry before market close. See
CLOSING_NAV_RECORDER.md. The first real scheduled capture remains a forward check.

The additional optional-candidate fault checks quarantine only timed-out contracts
with no holdings or working orders. Such quotes are excluded from optimization;
direct submissions still reject unqualified IDs. Mandatory contracts, filled
positions, orders and option underlyings remain required. A native contract-ID
mismatch is rejected. The operator restarted TWS, restoring the original full
universe lookup before final activation checks; limits were not raised.

## Corrective solve after a deadline

After restoring the baseline and TWS connection, the full mixed-integer solve
hit its 10-second deadline. Its unaccepted result was discarded, leaving the
existing BA residual delta breach unresolved. The service now attempts a separate
stock-only delta solve when the primary solve fails and the actual portfolio
already breaches a residual delta limit. Every option quantity is frozen to actual
inventory, and all combined-portfolio risk constraints remain enforced. Refreshed
broker state and quotes invalidate obsolete targets; the independent execution
gate still validates each slice. Ordinary score optimization resumes once the
portfolio is compliant. No limit increase or directional recovery forecast is used.

Regression coverage verifies that an invalid failed-solver incumbent is discarded,
the fallback preserves mandatory and discretionary options, and a healthy
portfolio does not use this risk-only fallback. The full 155-test suite passed.

Forward check on October 8 at approximately 12:43 Eastern: normal paper activation
accepted the independently gated stock fallback. IBKR filled 23 BA shares at
$186.47 (12:42:12) and 100 at $186.51 (12:42:27), increasing actual BA stock from
200 to 323 shares. All option positions, including the two mandatory 25-contract
positions, were unchanged. The observed BA dollar delta was approximately
-$196,757 against a $199,310 cap; combined-portfolio risk passed with no active
orders. PAPER mode continued, with subsequent ordinary proposals subject to
modeled score improvement and uncertainty. These are point-in-time observations,
not a guarantee of future compliance or evidence of improved contest performance.
The next scheduled NAV capture was October 8 at 16:00 Eastern.

## Recovery activation diagnostics

The deployed promotion file contains bar-based forecast validation and zero
qualifying combined quote-based contest windows; it does not contain combined
mean or 90th-percentile contest scores. The prior diagnostic falsely described
missing scores as measured underperformance. Missing results and invalid recorded
metrics now have separate messages, while actual worse recorded results retain
the corresponding performance rejection. The dashboard shows a cached,
read-only promotion status and disables paper recovery until validation passes.
Backend activation and every execution slice continue to require uncached evidence;
no evidence, limits or promotion requirements were changed to enable recovery.

All 163 tests passed with no failures or skips, including isolated PostgreSQL
and dashboard controls. Regressions cover missing-versus-measured scores,
nonfinite/non-numeric/negative metrics, evidence-change cache invalidation,
missing files, disabled controls and escaped diagnostic text. Ordinary paper
hedging and shadow recovery remain independently controlled.

## Urgency-aware orders and protected setup

The October 7 opening-price and October 8 small-order audits motivated an
execution-policy update. Normal orders use patient, tick-valid limits with staged
repricing and a fixed arrival budget; urgent stock risk corrections can use market
orders only with a live <=1-second, deep, <=$0.02-spread book. Modeled slippage and
fresh risk checks remain enforced; market orders do not have a hard price ceiling.
Wide option spreads wait rather than force a fill. Ordinary stock slices need
$1,000 notional and independent after-cost/uncertainty improvement; fee-only
apparent score improvement is rejected. Default slice maximums and hard portfolio
limits are unchanged.

Future Day 0 setup first records a complete portfolio and a feasible balanced
acquisition path. Planned stock hedges are prepared, then each mandatory option
is acquired with a matched nonmandatory option through a guaranteed pair limit
order. No bare-mandatory API fallback exists. Parent and both leg intents are
atomic in storage; cancellation races, duplicate reports, unknown transmission,
working-leg exposure and restart uncertainty remain auditable. Unsupported
mandates/routes wait without purchasing. Recorded target performance is modeled;
no historical contest-score improvement is claimed for these new defaults.

On October 8, read-only IBKR what-if requests accepted BA 195C/197.5C and
195P/192.5P guaranteed pairs without warnings. This validates order construction
and preview support, not actual package-fill behavior. A future paper Day 0 still
needs forward execution verification. See PROTECTED_SETUP_EXECUTION.md.

Final verification for this update: **180 passed, zero failures and skips**, including isolated PostgreSQL and JavaScript controls (49.36 seconds). Ruff and diff checks passed. Actual package fills remain a forward paper-test item; no current practice holdings were changed during validation.
# Optimizer runtime verification — October 8, 2026

- Final regression suite: **196 passed**, no failures or skips, **33.45 seconds**.
  PostgreSQL integration used a newly created isolated database, removed after
  the run. The operational ledger was not used for tests.
- Ruff and diff checks passed. Dashboard tests include runtime rendering and
  explicit incomplete-ranking feedback.
- Sixteen new optimizer tests cover positive-part exactness, exact stress
  dominance, asymmetric fees, brute-force integer objectives and cash carry,
  failed-ranking retention, invalid bounds, total deadlines, objective offsets,
  and incomplete next-close reporting. Earlier failures were test-fixture issues
  and were corrected before the final full run.
- Final expanded synthetic comparison: tracked `6b9d1fc` versus the updated model,
  same configured 24 instruments, 14 options, 64 score scenarios and 770 stresses.
  One sample per mode: stock-only **10.109 s / USER_LIMIT → 8.747 s / certified
  primary target**; full **10.127 s / USER_LIMIT → 1.659 s / certified primary
  target**, approximately **6.1 times faster**. Secondary ranking did not finish
  in those two updated runs; no timeout solution was accepted.
- A smaller BA-only synthetic comparison (three samples) previously measured
  old stock median **77.697 ms** versus new **33.141 ms**. The final hierarchy
  produced the same stock target and approximately 1.6-second full solves in
  subsequent repeats. Load-sensitive timings are not universal latency promises.
- Original score and hard limits remain. Primary selection uses a certified
  lower-bound cap to preserve the existing $1 model-optimum band. Incomplete
  optional ranking is explicit, rather than reported as fully optimized.
- Application restarted only after verifying SHADOW mode and no active system
  intents. The same PostgreSQL ledger and existing holdings were retained;
  SHADOW mode was restored, and paper execution was not enabled.
- Synthetic computation is not historical/forward contest-score evidence or
  a measurement of IBKR fill latency. The surplus-reduction proposal remains
  documentation only and was not activated by this update.

Reproduction and model proofs: [Optimizer runtime](OPTIMIZER_RUNTIME.md).
Local detailed results: `reports/validation/optimizer-runtime-expanded-final.json`
and `reports/validation/optimizer-runtime-small-comparison.json`.
