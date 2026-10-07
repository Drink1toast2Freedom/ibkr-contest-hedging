# Architecture and invariants

`broker → immutable snapshot → scenarios/valuation → optimizer → independent risk gate → durable intent → broker execution → reconciliation`. The AI consumes decision snapshots asynchronously. The localhost dashboard reads state and requests bounded controls; it does not call IBKR directly.

## Modules

`config/models` validate paper identity, instruments, calendars, mandates and hard bounds. `scoring/storage` persist exact official NAV revisions, broker events, decisions, intents, executions, and fees. `valuation/scenarios` produce American option price changes and stress scenarios anchored to broker state. `optimizer/risk/universe` perform integer scenario sizing, screened candidate selection, and independent validation. `broker/execution/service` synchronize IBKR, handle order uncertainty and risk-safe slices, and supervise local operation. `research/data` normalize quote evidence and replay shared strategy/risk logic. `supervisor/webapp/web` provide bounded AI reviews and local controls.

## Financial invariants

1. Score uses adjacent official close NAVs. Day 0 does not receive a separate score. Charges included in NAV are not subtracted twice.
2. Mandatory broker net quantities remain exact outside explicit Day 0 setup. A bookkeeping label cannot excuse selling a mandatory option.
3. Quantities are whole shares/contracts. All scenario P&L includes verified multipliers.
4. Cash exchanged for an option asset is not itself a NAV loss; spreads, commissions, financing and mark movements are.
5. Model stress loss is limited to 3% of the smaller of official Day 0 NAV and current positive NAV. Finite scenarios are not a bound on naked-option tail loss.
6. No AI result changes hard limits, quantities, mandates, or order transmission.

## Execution invariants

Every transmitted order has a durable intent and reserved broker ID. A local submission return is not broker acknowledgement. Timeout/crash produces UNKNOWN and blocks resubmission until broker evidence resolves it. Duplicate executions do not duplicate holdings or fees. Holdings are always read from IBKR; the local fill ledger is for audit, not an alternative source of position truth.

New slices account for all working orders. Partial execution paths are checked independently. An accepted plan can finish in risk-checked slices without waiting a fresh option cooldown for each contract; its remaining modeled benefit is rechecked with current NAV/quotes and already-paid costs. A new plan observes the relevant cooldown. Pausing requests cancellation and never assumes it succeeded.

## Database layout

`records`: immutable account-scoped configuration, snapshot, raw event, decision, risk, submission, transition, profile, supervisor, alert and research payloads, each with ID/time/provenance. `order_intents`: typed state, quantity, limit, broker IDs and decision/snapshot references, CAS state changes. `executions`: unique account+execution ID, correction linkage and typed execution quantities/prices/times. `commission_reports`: account+execution fee linkage. `closing_nav_revisions`: exact decimal NAV, contest/session/source and immutable revision sequence. Indexes scope operations by account, category, status and session. JSON payloads keep versioned raw broker evidence intact while order/scoring fields are typed.

Production storage is PostgreSQL. SQLite is intentionally limited to demo/research/shadow. Migration 0001 creates the schema; future revisions must be frozen Alembic migrations rather than rewriting history. Current migration references initial metadata because this is a new, unpublished project.

## Evidence and rollout

Synthetic model tests → quote-based historical replay → read-only Gateway shadow → fault-tested paper execution → full practice run → validated contest release. Each promotion needs account/report/data-specific evidence. Freeze code and profiles during scored sessions. GitHub CI has no broker/AI credentials and does not deploy or trade.
