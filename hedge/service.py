from __future__ import annotations

import asyncio
import fcntl
import os
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from .broker import IBKRBroker, SimBroker
from .config import NY
from .data import QuoteRecorder
from .demo import demo_quotes
from .execution import ExecutionManager
from .models import Target, digest, utcnow
from .moomoo_data import MoomooData
from .optimizer import HedgeOptimizer
from .recovery import RecoveryController, recovery_budget_errors
from .risk import RiskGate
from .scenarios import ScenarioEngine
from .scoring import ScoreLedger
from .storage import payload
from .supervisor import PROFILES, Supervisor
from .universe import screen_options
from .valuation import NAVTracker


class TradingService:
    def __init__(self, manifest, store, demo=False, history=None, rehearsal=False):
        if rehearsal and not demo:
            raise ValueError("Empty-account rehearsal must use the synthetic broker")
        self.rehearsal = rehearsal
        self.manifest, self.store, self.demo = manifest, store, demo
        self.events = asyncio.Queue()
        self.demo_start = datetime.combine(
            manifest.day_zero if rehearsal else manifest.scoring_sessions[0], datetime.min.time(), NY
        ).replace(hour=10)
        self.started = time.monotonic()
        if demo:
            self.store.clock = self.now
        self.broker = (
            SimBroker(manifest, self.events, self.demo_start, start_empty=rehearsal)
            if demo
            else IBKRBroker(manifest, self.events)
        )
        self.gate = RiskGate(manifest, demo)
        self.execution = ExecutionManager(manifest, store, self.broker, self.gate)
        self.scenarios = ScenarioEngine(manifest, history)
        self.optimizer = HedgeOptimizer(manifest)
        self.ledger = ScoreLedger(store, manifest)
        self.recorder = QuoteRecorder()
        self.nav_tracker = NAVTracker(manifest)
        self.supervisor = Supervisor()
        self.moomoo_data = MoomooData(manifest) if not demo else None
        if self.moomoo_data and manifest.market_data_provider == "moomoo":
            self.broker.external_quotes = self.moomoo_data.quotes
        self.mode = "PAUSED"
        self.profile = "STANDARD"
        self.ready = False
        self.health = "Starting"
        self.last_snapshot = None
        self.last_quotes = {}
        self.last_risk = None
        self.last_target = None
        self.last_scenarios = None
        self.last_optimization = {"STK": 0.0, "OPT": 0.0}
        self.last_trade = {"STK": 0.0, "OPT": 0.0}
        self.last_reconcile = 0.0
        self.tasks = []
        self.market_data_process = None
        self.market_data_log = None
        self.decision_lock = asyncio.Lock()
        self.control_lock = asyncio.Lock()
        self.reconcile_lock = asyncio.Lock()
        self.writer_file = None
        self.pg_lock = None
        self.force_optimize = False
        self.pending_target = None
        self.last_decision_id = None
        self.active_plan_started = False
        self.setup_armed = False
        self.setup_target_quantities = None
        self.auto_hedge_after_setup = False
        self.recovery = RecoveryController(manifest, store)
        self.pending_recovery = None
        self.market_data_errors = {}

    def now(self):
        return self.demo_start + timedelta(seconds=time.monotonic() - self.started) if self.demo else utcnow()

    async def start(self):
        self.store.initialize()
        Path("data").mkdir(exist_ok=True)
        path = Path("data") / ("writer-" + digest(self.manifest.account)[:12] + ".lock")
        self.writer_file = path.open("a+")
        try:
            fcntl.flock(self.writer_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.writer_file.close()
            raise RuntimeError("Another execution owner already holds this account") from exc
        if self.store.engine.dialect.name == "postgresql":
            from sqlalchemy import text

            self.pg_lock = self.store.engine.connect()
            key = int(digest(self.manifest.account)[:15], 16)
            if not self.pg_lock.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar():
                self.pg_lock.close()
                raise RuntimeError("Another PostgreSQL execution owner already holds this account")
        self.store.record("configuration", self.manifest.account, self.manifest, self.manifest.config_hash)
        if not self.demo and Path("config/local.moomoo-codes.json").exists():
            self.market_data_log = Path("data/moomoo-collector.log").open("a")
            self.market_data_process = await asyncio.create_subprocess_exec(
                str(Path(".runtime/moomoo-env/bin/python").absolute()),
                "ops/record_moomoo.py",
                stdout=self.market_data_log,
                stderr=self.market_data_log,
                env={k: v for k, v in os.environ.items() if k in ("HOME", "PATH", "TMPDIR", "LANG")},
            )
        if self.demo:
            if not self.rehearsal:
                self.ledger.import_close(
                    self.manifest.day_zero,
                    self.manifest.initial_nav,
                    "SYNTHETIC demo baseline",
                    self.manifest.account,
                )
            quotes = demo_quotes(self.manifest, self.now())
            self.broker.update_quotes(quotes, self.now())
            self.broker.cash -= sum(
                Decimal(str(quotes[i].mid)) * q * self.manifest.contract_map[i].multiplier
                for i, q in self.broker.positions.items()
            )
        self.tasks = [
            asyncio.create_task(self._event_loop()),
            asyncio.create_task(self._monitor_loop()),
            asyncio.create_task(self._supervisor_loop()),
        ]

    async def stop(self):
        await self.execution.pause()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.market_data_process and self.market_data_process.returncode is None:
            self.market_data_process.terminate()
            await self.market_data_process.wait()
        if self.market_data_log:
            self.market_data_log.close()
        self.recorder.flush()
        await self.broker.disconnect()
        if self.pg_lock:
            self.pg_lock.close()
        if self.writer_file:
            fcntl.flock(self.writer_file, fcntl.LOCK_UN)
            self.writer_file.close()

    async def _event_loop(self):
        while True:
            event, value = await self.events.get()
            try:
                self.store.record(
                    "broker_event", self.manifest.account, {"event": event, "value": payload(value)}
                )
                if event == "execution":
                    fresh = self.store.execution(value)
                    if fresh:
                        self.execution.on_execution(value)
                        self.nav_tracker.execution(value)
                        if not value.order_ref.startswith("hedge:"):
                            self.force_optimize = True
                            self.pending_target = None
                            self.active_plan_started = False
                elif event == "commission":
                    self.store.fee(
                        self.manifest.account, value["exec_id"], value["amount"], value["currency"]
                    )
                    self.nav_tracker.fee(value["exec_id"], value["amount"])
                elif event == "order":
                    self.execution.on_order(value)
                elif event in ("disconnected", "unsupported_execution"):
                    if self.last_snapshot:
                        self.last_snapshot = self.last_snapshot.model_copy(
                            update={"connected": False, "reconciled": False}
                        )
                    self.ready = False
                    self.mode = "PAUSED"
                    self.execution.paused = True
                    self.pending_target = None
                    self.alert("error", event)
                elif event == "position":
                    identifier, quantity = value["con_id"], int(float(value["quantity"]))
                    previous = (
                        self.last_snapshot.quantities().get(identifier, 0) if self.last_snapshot else None
                    )
                    bounds = self.execution.owned_inventory_ranges.get(identifier)
                    if quantity != previous and not (bounds and bounds[0] <= quantity <= bounds[1]):
                        self.pending_target = None
                        self.active_plan_started = False
                        self.force_optimize = True
                elif event == "broker_error" and value.get("code") in (201, 202):
                    rows = [
                        r
                        for r in self.store.intents(self.manifest.account, True)
                        if r["broker_order_id"] == value["order_id"]
                    ]
                    for row in rows:
                        if row["state"] == "SUBMITTING":
                            self.store.transition(row["id"], "UNKNOWN")
                        self.execution.on_order(
                            {
                                "order_ref": "hedge:" + row["id"],
                                "order_id": value["order_id"],
                                "status": "Inactive" if value["code"] == 201 else "Cancelled",
                            }
                        )
                    self.alert("warning", value["message"])
                elif event == "broker_error" and value.get("code") in (354, 10090, 10167, 10168):
                    self.market_data_errors[value.get("con_id") or value["order_id"]] = value["message"]
                    self.ready = False
                    self.alert("error", "Live API market data unavailable: " + value["message"])
            except Exception as exc:
                self.execution.paused = True
                self.mode, self.ready, self.health = (
                    "PAUSED",
                    False,
                    f"Durable event processing failed: {exc}",
                )
            finally:
                self.events.task_done()

    def alert(self, severity, message):
        recent = self.store.records("alert", self.manifest.account, 1)
        if not recent or recent[0].get("message") != message:
            self.store.record("alert", self.manifest.account, {"severity": severity, "message": message})

    async def reconcile(self):
        async with self.reconcile_lock:
            return await self._reconcile()

    async def _reconcile(self):
        prior = self.last_snapshot.quantities() if self.last_snapshot else None
        snapshot = await asyncio.wait_for(self.broker.reconcile(), 30)
        await self.events.join()
        await self.execution.reconcile(snapshot)
        self.last_snapshot = snapshot
        self.store.snapshot(snapshot)
        self.last_reconcile = time.monotonic()
        if (
            snapshot.quantities() != prior
            or self.execution.unresolved()
            or any(not o.order_ref.startswith("hedge:") for o in snapshot.orders)
        ):
            self.pending_target = None
            self.active_plan_started = False
            self.force_optimize = True
        return snapshot

    async def _monitor_loop(self):
        next_connection = 0.0
        while True:
            try:
                if not self.broker.snapshot().connected if self.demo else not self.broker.ib.isConnected():
                    if time.monotonic() >= next_connection:
                        await self.broker.connect()
                        await self.reconcile()
                        if self.setup_armed:
                            self.mode = "WAITING_SETUP"
                        self.health = "Connected; paper execution remains disabled until explicitly enabled"
                    else:
                        await asyncio.sleep(1)
                        continue
                if self.demo:
                    self.broker.update_quotes(
                        demo_quotes(self.manifest, self.now(), time.monotonic() - self.started), self.now()
                    )
                    if self.mode in ("PAPER", "SETUP"):
                        for order in list(self.broker.orders):
                            await self.broker.fill(order)
                if time.monotonic() - self.last_reconcile >= self.manifest.policy.reconcile_seconds:
                    await self.reconcile()
                self.last_snapshot = self.broker.snapshot()
                baseline = self.ledger.closes().get(self.manifest.day_zero)
                if baseline is not None:
                    self.gate.engine.baseline_nav = self.optimizer.baseline_nav = float(baseline)
                self.last_quotes = self.broker.quotes()
                self.recovery.observe_close(self.last_quotes, self.now())
                if (
                    self.moomoo_data
                    and self.moomoo_data.path.exists()
                    and self.manifest.market_data_provider != "moomoo"
                ):
                    self.last_quotes = self.moomoo_data.quotes(self.now())
                for identifier, quote in self.last_quotes.items():
                    if quote.data_type == "live":
                        self.market_data_errors.pop(identifier, None)
                self.store.snapshot(self.last_snapshot)
                self.recorder.append(self.last_quotes, self.manifest.contract_map, self.now())
                missing = set(self.last_snapshot.quantities()) - self.last_quotes.keys()
                if missing:
                    raise ValueError(f"Waiting for supported live quotes on all holdings: {sorted(missing)}")
                mandate_errors = self.gate.registry.check(self.last_snapshot.quantities())
                if mandate_errors:
                    self.health = "; ".join(mandate_errors)
                    self.ready = False
                    if self.mode == "PAPER":
                        await self.execution.pause()
                        self.mode = "PAUSED"
                    if self.mode != "SETUP":
                        self.alert("error", self.health)
                if self.last_quotes:
                    ids = screen_options(self.manifest, self.last_quotes, self.last_snapshot.quantities())
                    self.last_scenarios = await asyncio.to_thread(
                        self.scenarios.build, self.last_snapshot, self.last_quotes, self.now(), ids
                    )
                    self.last_risk = self.gate.validate(
                        self.last_snapshot,
                        self.last_quotes,
                        self.last_scenarios,
                        self.last_snapshot.quantities(),
                        self.now(),
                    )
                    self.ready = (
                        not mandate_errors and self.last_risk.passed and not self.execution.unresolved()
                    )
                    if self.mode in ("PAPER", "SETUP") and self.last_snapshot.orders:
                        projected_check = self.gate.validate(
                            self.last_snapshot,
                            self.last_quotes,
                            self.last_scenarios,
                            self.last_snapshot.projected(),
                            self.now(),
                            corrective=not self.last_risk.passed,
                            day_zero=self.mode == "SETUP",
                        )
                        if not projected_check.passed:
                            await self.execution.pause()
                            self.mode = "PAUSED"
                            self.health = "Working-order risk blocked: " + "; ".join(projected_check.reasons)
                            self.alert("error", self.health)
                    if self.mode in ("SHADOW", "PAPER"):
                        if self.optimization_due():
                            await self.optimize()
                    elif self.mode == "SETUP":
                        await self._setup_slice()
                    elif self.mode == "WAITING_SETUP" and self.manifest.can_trade(self.now(), "OPT"):
                        if self.now().astimezone(NY).date() == self.manifest.purchase_session:
                            try:
                                await self.control("setup")
                                self.setup_armed = False
                            except ValueError as exc:
                                self.health = f"Scheduled purchases waiting for preflight: {exc}"
                    if self.mode == "SHADOW" and self.auto_hedge_after_setup and not mandate_errors:
                        day = self.manifest.active_session(self.now())
                        if day == self.manifest.day_zero or (
                            day and self.ledger.previous_close(day) is not None
                        ):
                            try:
                                await self.control("paper")
                                self.auto_hedge_after_setup = False
                            except ValueError as exc:
                                self.health = f"Purchases complete; hedge activation waiting: {exc}"
                        else:
                            self.health = (
                                "Purchases complete; automatic hedging awaits previous official closing NAV"
                            )
                if self.mode == "PAPER" and self.pending_target:
                    await self._execute_pending()
                await self.execution.manage(self.last_quotes, self.now())
                if self.execution.paused and self.mode in ("PAPER", "SETUP"):
                    self.mode = "PAUSED"
                if self.mode == "PAUSED" and self.execution.pause_requested:
                    self.health = (
                        "Paused — all cancellations confirmed"
                        if self.execution.pause_complete()
                        else "Pause requested — awaiting broker confirmation"
                    )
                if self.setup_armed and self.now() >= self.manifest.session_close(
                    self.manifest.purchase_session
                ) - timedelta(minutes=self.manifest.policy.option_cutoff_minutes):
                    self.setup_armed = self.auto_hedge_after_setup = False
                    self.mode = "PAUSED"
                    self.alert(
                        "error",
                        "Scheduled mandatory purchase window ended; inspect holdings and organizer rules",
                    )
                if self.market_data_errors and not self.last_quotes:
                    self.health = "IBKR connected; live stock/option API subscriptions unavailable; purchases and hedging blocked"
                day = self.manifest.active_session(self.now())
                if self.now() >= self.manifest.session_close(
                    self.manifest.scoring_sessions[-1]
                ) and self.mode in ("PAPER", "SETUP"):
                    await self.execution.pause()
                    self.mode = "PAUSED"
                    self.health = "Contest ended; filled holdings retained"
                if day:
                    previous = self.ledger.previous_close(day)
                    if previous is None and day != self.manifest.day_zero:
                        self.alert(
                            "warning", "Previous official closing NAV missing; score-aware trading blocked"
                        )
                await asyncio.sleep(self.manifest.policy.risk_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.health = str(exc)
                self.ready = False
                self.pending_target = None
                if self.mode in ("PAPER", "SETUP"):
                    await self.execution.pause()
                    self.mode = "PAUSED"
                try:
                    self.alert("error", self.health)
                except Exception:
                    pass
                next_connection = time.monotonic() + 15
                await asyncio.sleep(5)

    def optimization_due(self):
        """Continuous evaluation remains independent of whether past trades filled."""
        elapsed = time.monotonic()
        return self.pending_target is None and (
            self.force_optimize
            or elapsed - self.last_optimization["STK"] >= self.manifest.policy.stock_seconds
            or elapsed - self.last_optimization["OPT"] >= self.manifest.policy.option_seconds
        )

    async def optimize(self):
        if self.manifest.scoring_rule == "pending_interest_adjustment":
            self.health = "Scoring revision pending; interest-offset optimization disabled"
            return
        async with self.decision_lock:
            snap, quotes, scenarios, now = (
                self.last_snapshot,
                dict(self.last_quotes),
                self.last_scenarios,
                self.now(),
            )
            day = self.manifest.active_session(now)
            previous = self.ledger.previous_close(day) if day else None
            if day == self.manifest.day_zero:
                previous = snap.nav  # initial hedge before unscored Day 0 baseline
            elif previous is not None:
                previous = self.ledger.target_for(day, previous, allow_estimate=True)
            if previous is None or self.gate.registry.check(snap.quantities()):
                return
            full = (
                self.force_optimize
                or time.monotonic() - self.last_optimization["OPT"] >= self.manifest.policy.option_seconds
            )
            estimate = self.nav_tracker.estimate(snap, quotes, self.manifest.contract_map)
            target = await asyncio.to_thread(
                self.optimizer.solve,
                snap,
                quotes,
                scenarios,
                previous,
                estimate,
                not full,
                PROFILES[self.profile],
            )
            # Broker inventory/order changes during optimization invalidate its decision.
            newest = self.broker.snapshot()
            if newest.quantities() != snap.quantities() or newest.orders != snap.orders:
                self.force_optimize = True
                return
            ordinary_floor = max(
                self.manifest.policy.score_threshold,
                self.manifest.policy.uncertainty_multiplier * target.uncertainty,
            )
            recovery_baseline = target
            if target.current_score - target.expected_score <= ordinary_floor:
                recovery_baseline = self.optimizer.evaluate_target(
                    snap,
                    quotes,
                    scenarios,
                    target.model_copy(update={"quantities": snap.quantities()}),
                    previous,
                    estimate,
                )
            recovery_target, recovery_scenarios, recovery_record = await asyncio.to_thread(
                self.recovery.evaluate,
                self.optimizer,
                snap,
                quotes,
                scenarios,
                recovery_baseline,
                previous,
                estimate,
                now,
            )
            self.store.record("recovery_shadow", snap.account, recovery_record)
            newest = self.broker.snapshot()
            if newest.quantities() != snap.quantities() or newest.orders != snap.orders:
                self.force_optimize = True
                return
            recovery_selected = False
            baseline_quantities = dict(recovery_baseline.quantities)
            if (
                self.recovery.mode == "paper"
                and recovery_target is not None
                and not self.recovery.paper_errors(recovery_record.get("model_hash"))
            ):
                if all(
                    newest.quantities().get(i, 0) == baseline_quantities.get(i, 0)
                    for i in set(newest.quantities()) | set(baseline_quantities)
                ):
                    target, scenarios = recovery_target, recovery_scenarios
                    recovery_selected = True
            gate = self.gate.validate(
                newest,
                self.broker.quotes(),
                scenarios,
                target.quantities,
                self.now(),
                2 * target.estimated_cost,
            )
            if recovery_selected:
                extra = recovery_budget_errors(
                    self.manifest,
                    self.recovery.config,
                    newest,
                    self.broker.quotes(),
                    scenarios,
                    target.quantities,
                    baseline_quantities,
                    2 * target.estimated_cost,
                )
                if extra:
                    gate = gate.model_copy(update={"passed": False, "reasons": (*gate.reasons, *extra)})
            improvement = target.current_score - target.expected_score
            threshold = max(
                self.manifest.policy.score_threshold,
                self.manifest.policy.uncertainty_multiplier * target.uncertainty,
            )
            corrective = not self.gate.engine.evaluate(
                newest, self.broker.quotes(), scenarios, newest.quantities()
            ).passed
            record = {
                **target.model_dump(mode="json"),
                "risk_passed": gate.passed,
                "risk_reasons": gate.reasons,
                "improvement": improvement,
                "threshold": threshold,
                "trade_eligible": gate.passed
                and target.status == "OPTIMAL"
                and (improvement > threshold or corrective),
                "corrective": corrective,
                "recovery": recovery_selected,
                "recovery_reference": baseline_quantities if recovery_selected else None,
                "nav_gap": estimate - float(previous),
                "closing_target": float(previous),
                "optimization_scope": "FULL" if full else "STOCK",
                "hold_distance": target.current_score,
                "proposed_distance": target.expected_score,
                "blocking_reasons": (
                    list(gate.reasons)
                    + ([f"Solver: {target.status}"] if target.status != "OPTIMAL" else [])
                    + (
                        [f"Modeled improvement ${improvement:.2f} does not exceed threshold ${threshold:.2f}"]
                        if improvement <= threshold and gate.passed and not corrective
                        else []
                    )
                ),
                "source": scenarios.source,
                "profile": self.profile,
                "at": now.isoformat(),
            }
            self.last_decision_id = self.store.record("decision", snap.account, record)
            self.last_target = target
            self.last_optimization["STK"] = time.monotonic()
            if full:
                self.last_optimization["OPT"] = time.monotonic()
            self.force_optimize = False
            if self.mode == "PAPER" and record["trade_eligible"]:
                self.pending_target = target
                self.pending_recovery = (
                    {
                        "reference": baseline_quantities,
                        "scenarios": scenarios,
                        "generated_at": now,
                        "model_hash": recovery_record.get("model_hash"),
                    }
                    if recovery_selected
                    else None
                )
                self.active_plan_started = False
            self.health = (
                "Monitoring: proposal eligible"
                if record["trade_eligible"]
                else "Monitoring: " + "; ".join(record["blocking_reasons"] or ["No qualifying trade"])
            )

    async def _execute_pending(self):
        target = self.pending_target
        snapshot = self.broker.snapshot()
        quotes = self.broker.quotes()
        actual = snapshot.quantities()
        scenario_for_plan = self.last_scenarios
        self.execution.recovery_checks = None
        if self.pending_recovery is not None:
            approval_errors = self.recovery.paper_errors(self.pending_recovery["model_hash"])
            if approval_errors:
                await self.execution.pause()
                self.recovery.mode = "shadow"
                self.mode = "SHADOW" if self.execution.pause_complete() else "PAUSED"
                self.pending_target = self.pending_recovery = None
                self.execution.recovery_checks = None
                self.force_optimize = True
                self.health = "Recovery approval invalidated; " + "; ".join(approval_errors)
                return
            if (
                self.recovery.mode != "paper"
                or (self.now() - self.pending_recovery["generated_at"]).total_seconds()
                > self.recovery.config.max_signal_age
            ):
                self.pending_target = self.pending_recovery = None
                self.force_optimize = True
                return
            scenario_for_plan = self.pending_recovery["scenarios"]
            reference = dict(self.pending_recovery["reference"])
            self.execution.recovery_checks = lambda snap, qs, sc, amounts, costs: (
                self.recovery.execution_errors(snap, qs, sc, amounts, reference, costs, self.now())
            )
        if snapshot.orders or self.store.intents(snapshot.account, True):
            return
        changed = [
            i for i in set(actual) | set(target.quantities) if actual.get(i, 0) != target.quantities.get(i, 0)
        ]
        if not changed:
            self.pending_target = None
            self.active_plan_started = False
            return
        corrective = not self.gate.engine.evaluate(snapshot, quotes, scenario_for_plan, actual).passed
        if not self.active_plan_started and not corrective:
            for i in changed:
                kind = self.manifest.contract_map[i].kind
                cooldown = (
                    self.manifest.policy.option_seconds
                    if kind == "OPT"
                    else self.manifest.policy.stock_seconds
                )
                if time.monotonic() - self.last_trade[kind] < cooldown:
                    return
        day = self.manifest.active_session(self.now())
        previous = self.ledger.previous_close(day) if day != self.manifest.day_zero else snapshot.nav
        if previous is None:
            self.pending_target = None
            return
        if day != self.manifest.day_zero:
            previous = self.ledger.target_for(day, previous, allow_estimate=True)
        if previous is None:
            self.pending_target = None
            return
        estimate = self.nav_tracker.estimate(snapshot, quotes, self.manifest.contract_map)
        target = self.optimizer.evaluate_target(
            snapshot, quotes, scenario_for_plan, target, previous, estimate
        )
        threshold = max(
            self.manifest.policy.score_threshold,
            self.manifest.policy.uncertainty_multiplier * target.uncertainty,
        )
        if target.current_score - target.expected_score <= threshold and not corrective:
            self.pending_target = None
            self.active_plan_started = False
            return
        if not self.gate.validate(
            snapshot, quotes, scenario_for_plan, target.quantities, self.now(), 2 * target.estimated_cost
        ).passed:
            self.pending_target = None
            self.force_optimize = True
            return
        result = await self.execution.submit_slice(
            target, self.last_decision_id, snapshot, quotes, scenario_for_plan, self.now()
        )
        if result["submitted"]:
            # Cooldown begins for a plan, not every slice, to allow completion.
            if not self.active_plan_started:
                for i in changed:
                    self.last_trade[self.manifest.contract_map[i].kind] = time.monotonic()
            self.active_plan_started = True
        else:
            self.health = result["reason"]
            if not self.execution.unresolved():
                self.pending_target = None
                self.active_plan_started = False
                self.force_optimize = True

    def _setup_quantities(self, snapshot, quotes):
        desired = {**snapshot.quantities(), **self.manifest.mandatory}
        for stock in self.manifest.contracts:
            if stock.kind != "STK" or stock.con_id not in self.manifest.allowlist:
                continue
            options = [
                c for c in self.manifest.contracts if c.kind == "OPT" and c.underlying_id == stock.con_id
            ]
            option_delta = sum(
                desired.get(c.con_id, 0) * c.multiplier * quotes[c.con_id].delta
                for c in options
                if desired.get(c.con_id, 0)
            )
            desired[stock.con_id] = -round(option_delta)
        return desired

    async def _setup_slice(self):
        snap = self.broker.snapshot()
        if snap.orders or self.store.intents(snap.account, True):
            return
        if self.setup_target_quantities is None:
            self.setup_target_quantities = self._setup_quantities(snap, self.broker.quotes())
        desired = dict(self.setup_target_quantities)
        complete = not self.gate.registry.check(snap.quantities())
        risk_ok = (
            self.gate.validate(
                snap, self.broker.quotes(), self.last_scenarios, snap.quantities(), self.now()
            ).passed
            if complete
            else False
        )
        if complete and risk_ok:
            await self.reconcile()
            if self.gate.registry.check(self.broker.snapshot().quantities()):
                raise ValueError("Mandatory fills did not reconcile")
            self.mode = "SHADOW"
            self.execution.paused = True
            self.broker.readonly = True
            self.force_optimize = True
            self.health = "Mandatory purchases reconciled; initial hedge proposals run in shadow mode"
            self.store.record("setup_complete", snap.account, {"quantities": desired, "at": self.now()})
            return
        target = Target(
            quantities=desired,
            expected_score=0,
            current_score=0,
            uncertainty=0,
            status="MANDATORY_SETUP",
            snapshot_id=snap.version,
            scenario_id=self.last_scenarios.identifier,
            config_hash=self.manifest.config_hash,
        )
        decision = self.store.record(
            "decision",
            snap.account,
            {
                **target.model_dump(mode="json"),
                "explanation": "Buy missing mandatory positions on the configured purchase session",
            },
        )
        result = await self.execution.submit_slice(
            target, decision, snap, self.last_quotes, self.last_scenarios, self.now(), setup=True
        )
        self.health = "Mandatory purchase submitted" if result["submitted"] else result["reason"]

    async def _supervisor_loop(self):
        # Independent of quotes, optimization and operating mode. Failures cannot
        # stop broker processing, enable execution, or change the strategy.
        while True:
            if self.supervisor.enabled:
                await self._review()
            await asyncio.sleep(5)

    async def _review(self):
        try:
            result = await self.supervisor.review(self.state())
            if result:
                current = self.last_snapshot
                result["stale"] = not current or result["snapshot_id"] != current.version
                self.store.record("supervisor", self.manifest.account, result)
        except Exception as exc:
            self.alert("warning", f"AI supervisor unavailable; deterministic strategy continues: {exc}")

    async def control(self, action, profile=None):
        async with self.control_lock:
            if action == "pause":
                self.setup_target_quantities = None
                self.setup_armed = self.auto_hedge_after_setup = False
                self.store.record("setup_schedule", self.manifest.account, {"status": "CANCELLED"})
                self.mode = "PAUSED"
                self.pending_target = self.pending_recovery = None
                self.execution.recovery_checks = None
                await self.execution.pause()
            elif action == "shadow":
                self.pending_target = self.pending_recovery = None
                self.execution.recovery_checks = None
                self.setup_target_quantities = None
                self.setup_armed = self.auto_hedge_after_setup = False
                await self.execution.pause()
                if self.store.intents(self.manifest.account, True):
                    raise ValueError("Wait for pending cancellations/reconciliation before shadow mode")
                self.broker.readonly = True
                self.mode, self.force_optimize = "SHADOW", True
            elif action in ("paper", "setup", "arm_setup"):
                if self.manifest.scoring_rule == "pending_interest_adjustment":
                    raise ValueError("Instructor's revised scoring formula required before activation")
                if not self.manifest.confirmed or not self.broker.qualified:
                    raise ValueError("Confirm contest configuration and broker contract identities first")
                if not self.demo and self.store.engine.dialect.name != "postgresql":
                    raise ValueError(
                        "Automatic IBKR paper trading requires PostgreSQL; SQLite is for demo/research"
                    )
                await self.reconcile()
                if self.execution.unresolved() or self.store.intents(self.manifest.account, True):
                    raise ValueError("Resolve working or uncertain orders before enabling execution")
                if action == "arm_setup":
                    deadline = self.manifest.session_close(self.manifest.purchase_session) - timedelta(
                        minutes=self.manifest.policy.option_cutoff_minutes
                    )
                    if self.now() >= deadline:
                        raise ValueError("Configured mandatory purchase window has already ended")
                    self.broker.readonly = True
                    self.execution.paused = True
                    self.setup_armed = self.auto_hedge_after_setup = True
                    self.mode = "WAITING_SETUP"
                    self.health = (
                        "Purchases armed for configured session open; quotes and limits revalidated then"
                    )
                    self.store.record(
                        "setup_schedule",
                        self.manifest.account,
                        {
                            "status": "ARMED",
                            "purchase_session": str(self.manifest.purchase_session),
                            "config_hash": self.manifest.config_hash,
                            "auto_hedge": True,
                        },
                    )
                    return self.state()
                if not self.last_scenarios or not self.last_quotes:
                    raise ValueError("Waiting for quotes/scenario/risk validation")
                if action == "paper":
                    result = self.gate.validate(
                        self.broker.snapshot(),
                        self.broker.quotes(),
                        self.last_scenarios,
                        self.broker.snapshot().quantities(),
                        self.now(),
                    )
                    if not result.passed:
                        # PAUSED has no proposal loop. Build a fresh, auditable
                        # target here instead of requiring a pre-existing target
                        # that paused operation can never produce.
                        self.last_target = None
                        self.force_optimize = True
                        self.last_snapshot = self.broker.snapshot()
                        self.last_quotes = self.broker.quotes()
                        await self.optimize()
                        correction = None
                        if self.last_target and self.last_target.status == "OPTIMAL":
                            correction = self.gate.validate(
                                self.broker.snapshot(),
                                self.broker.quotes(),
                                self.last_scenarios,
                                self.last_target.quantities,
                                self.now(),
                                2 * self.last_target.estimated_cost,
                            )
                        if correction is None or not correction.passed:
                            raise ValueError(
                                "Corrective hedge unavailable: "
                                + "; ".join(correction.reasons if correction else result.reasons)
                            )
                        # A fully compliant target is insufficient unless its
                        # first intermediate slice can actually reduce risk.
                        if not self.execution.feasible_slices(
                            self.last_target,
                            self.broker.snapshot(),
                            self.broker.quotes(),
                            self.last_scenarios,
                            self.now(),
                        ):
                            raise ValueError("Corrective hedge has no permitted risk-reducing first slice")
                    day = self.manifest.active_session(self.now())
                    if day != self.manifest.day_zero and self.ledger.previous_close(day) is None:
                        raise ValueError("Official previous closing NAV required")
                    if (
                        day != self.manifest.day_zero
                        and self.ledger.target_for(day, allow_estimate=True) is None
                    ):
                        raise ValueError("Actual daily cash benchmark rate required before paper trading")
                else:
                    if (
                        not self.manifest.can_trade(self.now(), "OPT")
                        or self.now().astimezone(NY).date() != self.manifest.purchase_session
                    ):
                        raise ValueError(
                            "Setup only permitted during the configured purchase-session option order window"
                        )
                    snapshot = self.broker.snapshot()
                    proposed = self._setup_quantities(snapshot, self.broker.quotes())
                    preflight = self.gate.validate(
                        snapshot,
                        self.broker.quotes(),
                        self.last_scenarios,
                        proposed,
                        self.now(),
                        day_zero=True,
                    )
                    if not preflight.passed:
                        raise ValueError("Mandatory portfolio preflight: " + "; ".join(preflight.reasons))
                    self.setup_target_quantities = dict(proposed)
                    self.store.record(
                        "setup_plan",
                        snapshot.account,
                        {
                            "quantities": proposed,
                            "snapshot_id": snapshot.version,
                            "config_hash": self.manifest.config_hash,
                            "explanation": "Freeze initial delta hedge during mandatory construction; risk rechecked on each slice",
                        },
                    )
                self.broker.readonly = False
                self.execution.paused = self.execution.pause_requested = False
                self.setup_armed = False
                self.mode, self.force_optimize = ("PAPER" if action == "paper" else "SETUP"), True
                if action == "paper" and not result.passed:
                    self.pending_target = self.last_target
                    self.active_plan_started = False
                    self.health = "Paper enabled: executing independently validated corrective hedge"
            elif action == "reconcile":
                await self.reconcile()
                if not self.demo and self.market_data_errors:
                    self.broker.refresh_market_data()
            elif action == "profile":
                self.setup_armed = self.auto_hedge_after_setup = False
                if profile not in PROFILES:
                    raise ValueError("Unknown profile")
                if not self.demo and profile not in self.supervisor.allowed_profiles:
                    raise ValueError("Profile not validated for this contest")
                await self.execution.pause()
                self.mode = "SHADOW"
                self.pending_target = None
                self.active_plan_started = False
                self.broker.readonly = True
                self.profile, self.force_optimize = profile, True
                self.store.record(
                    "profile",
                    self.manifest.account,
                    {"profile": profile, "config_hash": self.manifest.config_hash},
                )
            else:
                raise ValueError("Unknown control")
            return self.state()

    def state(self):
        snap = self.last_snapshot
        day = self.manifest.active_session(self.now())
        previous = self.ledger.previous_close(day) if day else None
        closing_target = self.ledger.target_for(day, previous, allow_estimate=True) if day else None
        try:
            estimate = (
                self.nav_tracker.estimate(snap, self.last_quotes, self.manifest.contract_map)
                if snap
                else None
            )
        except ValueError:
            estimate = None
        positions = []
        if snap:
            for p in snap.positions:
                c = self.manifest.contract_map.get(p.con_id)
                q = self.last_quotes.get(p.con_id)
                positions.append(
                    {
                        "con_id": p.con_id,
                        "label": c.label if c else f"Unknown conId {p.con_id}",
                        "quantity": p.quantity,
                        "role": "MANDATORY" if p.con_id in self.manifest.mandatory else "HEDGE",
                        "mark": q.mid if q else None,
                        "value": p.quantity * c.multiplier * q.mid if c and q else None,
                    }
                )
        return payload(
            {
                "contest": self.manifest.name,
                "account": self.manifest.account,
                "demo": self.demo,
                "rehearsal": self.rehearsal,
                "day_zero": str(self.manifest.day_zero),
                "purchase_session": str(self.manifest.purchase_session),
                "setup": {
                    "armed": self.setup_armed,
                    "auto_hedge": self.auto_hedge_after_setup,
                    "complete": bool(snap) and not self.gate.registry.check(snap.quantities()),
                    "holdings": [
                        {
                            "con_id": i,
                            "label": self.manifest.contract_map[i].label,
                            "required": q,
                            "held": snap.quantities().get(i, 0) if snap else 0,
                            "working": (snap.projected().get(i, 0) - snap.quantities().get(i, 0))
                            if snap
                            else 0,
                        }
                        for i, q in self.manifest.mandatory.items()
                    ],
                    "baseline_recorded": self.manifest.day_zero in self.ledger.closes(),
                    "on_day_zero": self.now().astimezone(NY).date() == self.manifest.day_zero,
                    "window_open": self.now().astimezone(NY).date() == self.manifest.purchase_session
                    and self.manifest.can_trade(self.now(), "OPT"),
                },
                "mode": self.mode,
                "ready": self.ready,
                "health": self.health,
                "clock": self.now().isoformat(),
                "snapshot_id": snap.version if snap else None,
                "connected": self.broker.connected if self.demo else self.broker.ib.isConnected(),
                "nav": str(snap.nav) if snap else None,
                "cash": str(snap.cash) if snap else None,
                "accrued_cash": str(snap.accrued_cash) if snap and snap.accrued_cash is not None else None,
                "interest": {
                    "annual_rate": self.manifest.policy.cash_rate,
                    "rate_status": self.manifest.policy.cash_rate_status,
                    "rate_source": self.manifest.policy.cash_rate_source,
                    "day_count": self.manifest.policy.cash_day_count,
                    "free_balance": self.manifest.policy.cash_interest_free_balance,
                    "note": "Accrued cash is already in NAV. Future interest uses a disclosed cash/settlement proxy; official NAV controls scoring.",
                },
                "nav_estimate": estimate,
                "previous_close": str(previous) if previous is not None else None,
                "closing_target": str(closing_target) if closing_target is not None else None,
                "closing_target_estimated": self.manifest.scoring_rule == "actual_daily_cash_rate"
                and day not in self.ledger.daily_rates(),
                "account_nav_at": snap.account_at.isoformat() if snap else None,
                "nav_report_source": self.manifest.report_source,
                "official_account_nav": self.ledger.latest_report(),
                "provisional_score": abs(estimate - float(closing_target))
                if estimate is not None and closing_target is not None
                else None,
                "scores": self.ledger.summary(),
                "baseline_reference": self.store.records("baseline_reference", self.manifest.account, 1),
                "positions": positions,
                "orders": self.store.intents(self.manifest.account),
                "fills": self.store.fills(self.manifest.account),
                "transitions": self.store.records("order_transition", self.manifest.account, 100),
                "decisions": self.store.records("decision", self.manifest.account, 30),
                "alerts": self.store.records("alert", self.manifest.account, 30),
                "risk": self.last_risk.model_dump(mode="json") if self.last_risk else None,
                "ai": self.store.records("supervisor", self.manifest.account, 1),
                "ai_enabled": self.supervisor.enabled,
                "ai_status": self.supervisor.status(),
                "recovery": self.recovery.latest,
                "profile": self.profile,
                "profiles": list(PROFILES if self.demo else self.supervisor.allowed_profiles),
                "pause_pending": self.execution.pause_requested and not self.execution.pause_complete(),
                "contract_labels": {i: c.label for i, c in self.manifest.contract_map.items()},
                "quote_count": len(self.last_quotes),
                "market_data_provider": "moomoo (data only)"
                if self.moomoo_data and self.moomoo_data.path.exists()
                else "IBKR"
                if not self.demo
                else "synthetic",
                "market_data_execution_verified": bool(self.last_quotes)
                and all(q.data_type == "live" for q in self.last_quotes.values())
                if self.moomoo_data and self.moomoo_data.path.exists()
                else None,
                "market_data_received_at": self.moomoo_data.received_at if self.moomoo_data else None,
                "market_data_notes": self.moomoo_data.errors if self.moomoo_data else [],
                "market_quotes": [
                    {
                        "label": self.manifest.contract_map[i].label,
                        "bid": q.bid,
                        "ask": q.ask,
                        "at": q.observed_at.isoformat(),
                        "data_type": q.data_type,
                    }
                    for i, q in self.last_quotes.items()
                ],
                "market_data_errors": list(self.market_data_errors.values()),
                "config_hash": self.manifest.config_hash,
                "contest_id": self.manifest.contest_id,
                "policy": self.manifest.policy.model_dump(),
                "limits": self.manifest.risk.model_dump(),
                "last_quote_at": max(
                    (q.observed_at.isoformat() for q in self.last_quotes.values()), default=None
                ),
                "scoring_sessions": [str(d) for d in self.manifest.scoring_sessions],
                "confirmed": self.manifest.confirmed,
            }
        )
