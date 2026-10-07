from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from .models import OrderRequest
from .optimizer import execution_cost
from .storage import TERMINAL


class ExecutionManager:
    def __init__(self, manifest, store, broker, gate):
        self.manifest, self.store, self.broker, self.gate = manifest, store, broker, gate
        self.lock = asyncio.Lock()
        self.paused = True
        self.pause_requested = False
        self.sent_at = {}
        self.repriced = set()
        self.last_preview = None
        self.preview_valid = False
        self.owned_inventory_ranges = {}

    def unresolved(self):
        return [
            r
            for r in self.store.intents(self.manifest.account, True)
            if r["state"] in ("SUBMITTING", "UNKNOWN")
        ]

    def feasible_slices(self, target, snapshot, quotes, scenarios, now, setup=False):
        """Read-only execution-path check, shared by activation and submission."""
        actual = snapshot.quantities()
        candidates = []
        for i in sorted(set(target.quantities) | set(actual)):
            difference = target.quantities.get(i, 0) - actual.get(i, 0)
            if not difference:
                continue
            c, q = self.manifest.contract_map[i], quotes[i]
            size = min(
                abs(difference),
                1 if c.kind == "OPT" else 100,
                q.ask_size if difference > 0 else q.bid_size,
            )
            if size <= 0:
                continue
            signed = size if difference > 0 else -size
            partial = dict(actual)
            partial[i] = partial.get(i, 0) + signed
            costs = execution_cost(self.manifest, quotes, [i], [signed])
            current_risk = self.gate.engine.evaluate(snapshot, quotes, scenarios, actual)
            result = self.gate.validate(
                snapshot,
                quotes,
                scenarios,
                partial,
                now,
                costs=2 * costs,
                corrective=not current_risk.passed,
                day_zero=setup,
            )
            if result.passed:
                candidates.append((result.metrics.get("stress_loss", float("inf")), i, signed, result))
        return candidates

    async def submit_slice(self, target, decision_id, snapshot, quotes, scenarios, now, setup=False):
        async with self.lock:
            if self.paused or self.pause_requested or self.unresolved():
                return {"submitted": False, "reason": "Paused or unresolved submission"}
            if (
                hasattr(self.broker, "margin_preview")
                and self.last_preview is not None
                and not self.preview_valid
                and now - self.last_preview < timedelta(minutes=1)
            ):
                return {
                    "submitted": False,
                    "reason": "Prior credit preview failed; awaiting rate-limited retry",
                }
            if self.store.intents(snapshot.account, True) or snapshot.orders:
                return {"submitted": False, "reason": "Working orders must reconcile before a new slice"}
            candidates = self.feasible_slices(target, snapshot, quotes, scenarios, now, setup)
            actual = snapshot.quantities()
            if not candidates:
                return {"submitted": False, "reason": "No intermediate execution slice passes risk checks"}
            _, identifier, signed, result = min(candidates, key=lambda x: (x[0], x[1]))
            partial = dict(actual)
            partial[identifier] = partial.get(identifier, 0) + signed
            costs = execution_cost(self.manifest, quotes, [identifier], [signed])
            q = quotes[identifier]
            # Midpoint never exceeds executable quote; options round to a safe
            # cent pending broker-specific minimum-tick qualification below.
            tick = Decimal("0.01")
            price = Decimal(str(q.mid)).quantize(tick, rounding=ROUND_HALF_UP)
            if hasattr(self.broker, "price_tick"):
                tick = Decimal(str(self.broker.price_tick(identifier, float(price))))
                price = (price / tick).quantize(Decimal(1), rounding=ROUND_HALF_UP) * tick
            if price <= 0:
                return {"submitted": False, "reason": "Nonpositive limit price"}
            request = OrderRequest(
                intent_id=uuid4().hex,
                con_id=identifier,
                signed_quantity=signed,
                limit_price=price,
                account=snapshot.account,
                snapshot_id=snapshot.version,
                decision_id=decision_id,
            )
            # Durable before reserving/submitting. Any later crash leaves a
            # recoverable intent and NEVER grants permission to resend it.
            self.store.create_intent(request)
            self.store.record(
                "risk", snapshot.account, {"intent_id": request.intent_id, **result.model_dump(mode="json")}
            )
            self.store.transition(request.intent_id, "VALIDATED")
            # Credit previews supplement (never replace) deterministic risk. Rate
            # limit to one/minute; each subsequent slice still has local checks.
            if hasattr(self.broker, "margin_preview") and (
                self.last_preview is None or now - self.last_preview >= timedelta(minutes=1)
            ):
                try:
                    self.last_preview = now
                    self.preview_valid = False
                    preview = await asyncio.wait_for(self.broker.margin_preview(request), 15)
                    change = Decimal(str(preview.initMarginChange))
                    if (
                        not change.is_finite()
                        or float(snapshot.excess_liquidity - max(Decimal(0), change))
                        < self.manifest.risk.liquidity_buffer * result.metrics["budget"]
                    ):
                        raise ValueError("Broker credit preview violates liquidity buffer")
                    if preview.warningText:
                        raise ValueError(f"Broker credit preview warning: {preview.warningText}")
                    from .models import utcnow
                    from .scenarios import ScenarioEngine

                    fresh_snapshot = self.broker.snapshot()
                    fresh_quotes = self.broker.quotes()
                    fresh_now = utcnow()
                    if fresh_snapshot.quantities() != actual or fresh_snapshot.orders:
                        raise ValueError("Broker inventory/order state changed during credit preview")
                    fresh_scenarios = await asyncio.to_thread(
                        ScenarioEngine(self.manifest).build, fresh_snapshot, fresh_quotes, fresh_now
                    )
                    refreshed = self.gate.validate(
                        fresh_snapshot,
                        fresh_quotes,
                        fresh_scenarios,
                        partial,
                        fresh_now,
                        costs=2 * costs,
                        corrective=not self.gate.engine.evaluate(
                            fresh_snapshot, fresh_quotes, fresh_scenarios, actual
                        ).passed,
                        day_zero=setup,
                    )
                    if not refreshed.passed:
                        raise ValueError("; ".join(refreshed.reasons))
                    self.preview_valid = True
                except Exception as exc:
                    self.store.transition(request.intent_id, "REJECTED")
                    return {"submitted": False, "reason": f"Credit/risk preflight blocked: {exc}"}
            order_id = self.broker.reserve_order_id()
            self.store.transition(request.intent_id, "SUBMITTING", order_id=order_id)
            self.owned_inventory_ranges[identifier] = (
                min(actual.get(identifier, 0), partial[identifier]),
                max(actual.get(identifier, 0), partial[identifier]),
            )
            try:
                evidence = await (
                    self.broker.setup_mandate(request, order_id, now)
                    if setup and request.con_id in self.manifest.mandatory
                    else self.broker.submit(request, order_id)
                )
                # Local placeOrder() return is NOT broker acknowledgement.
                self.sent_at[request.intent_id] = now
                self.store.record(
                    "submission", snapshot.account, {"intent_id": request.intent_id, **evidence}
                )
                return {"submitted": True, "intent_id": request.intent_id, "order_id": order_id}
            except Exception as exc:
                self.store.transition(request.intent_id, "UNKNOWN")
                self.store.record(
                    "alert",
                    snapshot.account,
                    {
                        "severity": "error",
                        "message": f"Uncertain order submission: {exc}",
                        "intent_id": request.intent_id,
                    },
                )
                self.paused = True
                return {"submitted": False, "reason": "Submission uncertain; reconcile before any new trade"}

    def on_order(self, event):
        reference = event.get("order_ref", "")
        if not reference.startswith("hedge:"):
            return
        identifier = reference.removeprefix("hedge:")
        rows = {r["id"]: r for r in self.store.intents(self.manifest.account)}
        if identifier not in rows:
            self.paused = True
            self.store.record(
                "alert",
                self.manifest.account,
                {"severity": "error", "message": "Broker system order has no durable intent"},
            )
            return
        row = rows[identifier]
        status = event.get("status")
        new = {
            "Submitted": "PARTIALLY_FILLED" if event.get("filled", 0) else "ACKNOWLEDGED",
            "PreSubmitted": "ACKNOWLEDGED",
            "Filled": "FILLED",
            "Cancelled": "CANCELLED",
            "ApiCancelled": "CANCELLED",
            "Inactive": "REJECTED",
            "PendingCancel": "CANCEL_PENDING",
        }.get(status)
        if new is None:
            return
        if row["state"] in TERMINAL and new != "FILLED":
            return  # stale callback cannot reopen a terminal order
        if row["state"] in ("PARTIALLY_FILLED", "CANCEL_PENDING") and new == "ACKNOWLEDGED":
            return
        if row["state"] == "SUBMITTING" and new == "CANCELLED":
            self.store.transition(identifier, "UNKNOWN")
        try:
            self.store.transition(identifier, new, event.get("order_id"), event.get("perm_id"))
        except ValueError:
            self.paused = True
            self.store.record(
                "alert",
                self.manifest.account,
                {
                    "severity": "error",
                    "message": "Unexpected broker order transition; reconcile",
                    "event": event,
                },
            )

    def on_execution(self, execution):
        rows = [
            r
            for r in self.store.intents(self.manifest.account)
            if r["id"] == execution.order_ref.removeprefix("hedge:")
            or (not execution.order_ref and execution.perm_id and r["perm_id"] == execution.perm_id)
        ]
        if not rows:
            return
        row = rows[0]
        fills = [
            f
            for f in self.store.effective_fills(self.manifest.account)
            if f["order_ref"] == "hedge:" + row["id"]
        ]
        quantity = sum(f["signed_quantity"] for f in fills)
        if quantity == row["signed_quantity"]:
            self.store.transition(row["id"], "FILLED")
        elif quantity and row["state"] not in TERMINAL:
            if quantity * row["signed_quantity"] < 0 or abs(quantity) > abs(row["signed_quantity"]):
                self.paused = True
                raise ValueError("Execution quantity exceeds durable intent; reconcile")
            self.store.transition(row["id"], "PARTIALLY_FILLED")

    async def reconcile(self, snapshot):
        by_ref = {o.order_ref: o for o in snapshot.orders}
        for row in self.store.intents(snapshot.account, True):
            if row["state"] in ("CREATED", "VALIDATED"):
                # A durable CREATED/VALIDATED intent was never submitted.
                self.store.transition(row["id"], "REJECTED")
            elif "hedge:" + row["id"] in by_ref:
                order = by_ref["hedge:" + row["id"]]
                self.on_order(
                    {
                        "order_ref": order.order_ref,
                        "order_id": order.order_id,
                        "perm_id": order.perm_id,
                        "status": "Submitted",
                        "filled": abs(row["signed_quantity"]) - abs(order.signed_remaining),
                    }
                )
            elif row["state"] in ("SUBMITTING", "ACKNOWLEDGED", "PARTIALLY_FILLED", "CANCEL_PENDING"):
                self.store.transition(row["id"], "UNKNOWN")
        if self.unresolved():
            self.paused = True

    async def pause(self):
        self.paused = self.pause_requested = True
        for row in self.store.intents(self.manifest.account, True):
            if row["state"] in ("ACKNOWLEDGED", "PARTIALLY_FILLED"):
                self.store.transition(row["id"], "CANCEL_PENDING")
                try:
                    await self.broker.cancel(row["broker_order_id"])
                except Exception as exc:
                    self.store.record(
                        "alert",
                        self.manifest.account,
                        {"severity": "error", "message": f"Cancellation unconfirmed: {exc}"},
                    )
            elif row["state"] in ("SUBMITTING", "UNKNOWN") and row["broker_order_id"] is not None:
                if row["state"] == "SUBMITTING":
                    self.store.transition(row["id"], "UNKNOWN")
                try:
                    await self.broker.cancel(row["broker_order_id"])
                except Exception as exc:
                    self.store.record(
                        "alert",
                        self.manifest.account,
                        {"severity": "error", "message": f"Uncertain-order cancellation unconfirmed: {exc}"},
                    )

    def pause_complete(self):
        return self.paused and not self.store.intents(self.manifest.account, True)

    async def manage(self, quotes, now):
        for row in self.store.intents(self.manifest.account, True):
            if row["state"] not in ("ACKNOWLEDGED", "PARTIALLY_FILLED"):
                # Lack of acknowledgement becomes explicitly uncertain.
                if (
                    row["state"] == "SUBMITTING"
                    and row["id"] in self.sent_at
                    and now - self.sent_at[row["id"]] > timedelta(seconds=15)
                ):
                    self.store.transition(row["id"], "UNKNOWN")
                    self.paused = True
                continue
            sent = self.sent_at.get(row["id"])
            q = quotes.get(row["con_id"])
            c = self.manifest.contract_map[row["con_id"]]
            age = (
                self.manifest.risk.option_quote_age if c.kind == "OPT" else self.manifest.risk.stock_quote_age
            )
            allowed = "synthetic" if self.gate.demo else "live"
            if (
                q is None
                or q.data_type != allowed
                or not timedelta(0) <= now - q.observed_at <= timedelta(seconds=age)
            ):
                self.store.transition(row["id"], "CANCEL_PENDING")
                try:
                    await self.broker.cancel(row["broker_order_id"])
                except Exception:
                    self.paused = True
                continue
            if sent is None or now - sent >= timedelta(seconds=30):
                self.store.transition(row["id"], "CANCEL_PENDING")
                try:
                    await self.broker.cancel(row["broker_order_id"])
                except Exception:
                    self.paused = True
                continue
            if now - sent >= timedelta(seconds=10) and row["id"] not in self.repriced:
                price = q.ask if row["signed_quantity"] > 0 else q.bid
                collar = max(0.01, (q.ask - q.bid) * 2)
                if price > 0 and abs(price - float(row["limit_price"])) <= collar:
                    # Never reprice beyond the economic cost used at validation.
                    await self.broker.replace(row["broker_order_id"], price)
                    self.repriced.add(row["id"])
