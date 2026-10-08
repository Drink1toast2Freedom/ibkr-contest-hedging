> **Updated future setup:** Use the Day 0 session, with a five-minute opening stabilization wait and a one-minute close buffer. Prepare the complete mandatory-plus-option-and-stock hedge plan first; acquire each new mandatory option through a guaranteed protective pair. Patient limits are the default; urgent stock risk corrections may use qualified market orders. No bare-option or wide-spread fallback. See [Protected setup and execution](PROTECTED_SETUP_EXECUTION.md). Existing practice dates and its completed Day 1 purchases remain unchanged.

> **Current operation:** Day 0 remains October 6; mandatory purchases are scheduled for the October 7 open. Initial setup includes a deterministic BA stock delta hedge and protects exact mandatory quantities through all slices. Approximate 3.35% may be used only as a labeled forecast fallback, per user approval; official scores still require dated actual daily rates. Keep Mac awake, TWS paper account and moomoo OpenD logged in. A service restart disarms scheduled purchases. Every runtime preflight can block orders; arming is not a guarantee of fills.

> **Latest clarification:** The organizer uses the actual daily cash rate, not fixed 3.35%. Current practice mode is `actual_daily_cash_rate`; target = previous official ending NAV × (1 + sourced daily fractional rate). Missing rates leave targets/scores unknown. Accrued interest is not subtracted separately. Earlier fixed-rate examples below are superseded. Record dated rates under Settings & alerts.

# Practice operation

Automatic closing capture is now enabled for configured sessions, including
early closes and Day 0. It requests fresh IBKR NAV after the scheduled close,
retains timestamps/API provenance, and alerts if the three-minute window is missed.
Keep the service and Paper TWS running. Imported statements can revise API captures;
neither accrued interest nor fees are subtracted again. See
[CLOSING_NAV_RECORDER.md](CLOSING_NAV_RECORDER.md) for retry and recovery behavior.

> **Confirmed scoring revision:** Score = sum |NAV today − NAV previous × (1 + r/360)|, using the instructor’s approximate annual rate of 3.35%. The full prior NAV is the benchmark base. Do not subtract accrued interest separately. Official NAV comes from IBKR Custom Statement → Net Asset Value; live API NAV is a separately labeled monitoring observation.


The rules use relative days. Supply **Day 0**, **the final day**, the **explicit scored sessions**, and the **mandatory expiry** for each round. The application never hardcodes a weekday, expiry, ten sessions, or a three-day practice. Calendar dates belong in a round's local configuration.

## Rules and schedule

Normally Day 0 is the purchase/initial-hedge session and its official closing NAV establishes the baseline. Day 1 is the first scored close. Each score is the absolute difference from the previous scored closing NAV, starting with Day 0. Charges and credits in official NAV are included once.

For this practice the user explicitly requested purchases at the **Day 1 open**. `mandatory_purchase_session` records that exception independently of Day 0. It does not move the baseline or exclude Day 1 costs. Future rounds default to Day 0 purchases. Exact holdings are 25 standard BA $195 calls and 25 standard BA $195 puts, with verified expiry and 100-share multiplier.

`config/local.practice.inputs.yaml` contains this round's confirmed inputs; `config/local.practice.yaml` contains the broker-qualified contracts and allowlist. Local account IDs, reports, passwords and configurations are excluded from Git.

## Paper TWS connection

Log into Paper Trading. In Global Configuration → API → Settings, enable ActiveX and Socket Clients, use port 7497, and allow localhost connections only. Keep Read-Only API checked for initial discovery; uncheck it before activating paper submissions. Keep TWS authenticated. No IBKR password is stored by this application.

Official settings: https://www.interactivebrokers.com/docs/tws-api/doc/tws-settings/tws-configuration-for-api-use/introduction

```sh
.venv/bin/python -m hedge.cli probe-ibkr --port 7497
```

The command checks DU paper identity and writes a local account/position report. It cannot place orders. Multiple accessible paper accounts require `--account DU...`. An API sample is not an official closing report.

Live API subscriptions are needed for BA stock and options. IBKR errors 354/10090 mean missing data entitlements; delayed/frozen quotes cannot authorize trading. Check the Market Data Subscription Manager or ask the instructor to grant/share the paper account's required subscriptions. After changing subscriptions, click Reconcile to retry them; reconnect/restart TWS if IBKR requires it. No paid subscription is purchased automatically.

## Configure a future round

Run `.venv/bin/python -m hedge.cli practice-inputs --output config/local.next-round.inputs.yaml` to be asked for Day 0, final scoring day and mandatory expiry, then confirm the generated exchange sessions. Alternatively copy `config/practice.inputs.template.yaml` to a new ignored local input file. Enter Day 0 and the final day, then enumerate the actual exchange scoring sessions between them, excluding Day 0. Resolve holidays, early closes, expiry and report convention with the organizer. Set `purchase_on_day: 0` normally, or `1` only for an explicit Day 1 purchase instruction. Set `rules_confirmed: true` when resolved.

```sh
.venv/bin/python -m hedge.cli prepare-practice --inputs config/local.practice.inputs.yaml
.venv/bin/python -m hedge.cli validate config/local.practice.yaml
```

Discovery qualifies the exact stock/mandatory options and up to 12 nearby hedge options. It rejects absent/ambiguous expiries, wrong underlying and adjusted multipliers, and never substitutes an expiry. Generated configuration starts `confirmed: false` for review and never overwrites an existing file. Confirm the complete manifest before setting it true.

## Rehearse purchases from empty holdings

Double-click **Practice Rehearsal.command**, or run:

```sh
.venv/bin/python -m hedge.cli serve --rehearsal --port 8766
```

The rehearsal uses a fresh isolated database, fictional dates/contract IDs, and synthetic prices. It never connects to IBKR. Open its local dashboard and choose **Buy missing mandatory positions** in Settings & alerts. Follow filled and working quantities and execution timestamps. It buys independently checked one-contract slices, stops at 25 per leg, reconciles, and enters shadow mode. Hedge execution can be enabled separately. Synthetic fills establish mechanics only, not contest performance.

## Start the actual practice dashboard

Double-click **Start Practice.command**, or run:

```sh
.venv/bin/python ops/run_practice.py
```

This starts the project-local PostgreSQL runtime on `127.0.0.1:55432`, applies migrations, and launches http://127.0.0.1:8765. Generated database credentials remain in `.runtime/db-password`, mode 0600. PostgreSQL data is in `data/postgres`; the downloaded runtime is in `.runtime`. These directories are ignored by Git. The installer checksum was matched to the official Postgres.app GitHub release. No system-wide database or launch agent is installed.

The service starts paused and connects read-only to IBKR. Its launcher uses a temporary caffeinate assertion to prevent idle sleep while the service runs; keep the lid open and TWS authenticated. Verify account identity, live data, qualified instruments and existing orders. Check **Practice setup progress** for required/filled/working quantities. All UI timestamps use Eastern time.

## Before the purchase-session open

In Settings & alerts, **Arm opening purchases + hedging** schedules the configured purchase session. No orders are submitted before the permitted options window. At the open, it independently rechecks actual holdings, working orders, quotes, margin and hard limits before every slice. Quotes must be live/fresh and contain IV/Greeks. If the required book or a temporary execution step fails risk checks, it blocks and alerts instead of relaxing limits. Bound limit orders do not guarantee fills.

Keep this service running and the Mac awake/connected with TWS authenticated. A restart starts paused and requires re-arming; there is no automatic failover. **Pause**, **Start shadow**, or a profile change cancels pending automation. Pause cancels system-owned pending orders where possible and retains filled holdings.

## After purchases

The service reconciles both exact mandatory quantities before ordinary hedging. Scheduled operation then enables hedging only after deterministic validation and availability of the previous official closing NAV. Without that report it stays in shadow mode, retaining the holdings. Mandatory contracts cannot be sold, increased, exercised or rolled by ordinary hedging.

Import the official Day 0 NAV used by the organizer in Settings & alerts. Starting cash and the fact that no trades occurred do not prove closing NAV: interest/credits can change it. An unverified user reference is displayed separately and cannot authorize the score controller. Each scored session needs its preceding official close. Corrected reports recompute both affected neighboring scores.

## Each scored day and final day

Overview shows account NAV, risk, provisional score and official ledger; Trades shows submissions, fills, fees and cancellations; Decisions shows modeled improvements and reasons; Settings & alerts shows purchases, subscriptions and close imports. Missing closes, stale data, incorrect mandates or uncertain submissions block new trading. Keep the mandatory holdings throughout. At the final close, execution pauses and retains holdings.

AI remains optional, disabled until deterministic operation is validated. One computer owns execution. GitHub stores code/test configuration only; a second execution computer or automatic failover is not configured.

## Interest-inclusive scoring and forecasting

IBKR NetLiquidation includes accrued interest. The dashboard shows TotalCashValue and AccruedCash separately for explanation, but never adds AccruedCash to NAV again. Official scores include increases/decreases in accrual. A month-end transfer from accrued interest to cash does not itself create a second gain.

The primary objective includes future cash interest, debit financing and stock borrow costs. This practice uses IBKR's published USD Pro rate as a **model assumption**, with its URL recorded in the local policy. It is not inferred by dividing the existing month-to-date accrual, and the paper account's effective rate remains unverified. The forecast accounts for the USD no-interest threshold, NAV eligibility scaling, a 360-day basis, weekends and a short-stock collateral proxy. It uses a cash-balance proxy for settlement timing; inspect real statement accruals before treating the forecast as calibrated. The optimizer's eligible-cash split is exact for this model, so it cannot fabricate income/expenses to meet the target.

The accumulated interest in Day 0's NAV belongs in that baseline. Hedge additional predicted accrual along with the mandatory/hedge portfolio's price changes and fees; never target a repeated subtraction of the entire month-to-date interest. Actual reported NAV and the official report always take precedence over forecast interest.
