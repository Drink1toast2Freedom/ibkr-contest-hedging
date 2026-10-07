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
