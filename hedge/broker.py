from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol

from ib_async import IB, ExecutionFilter, LimitOrder
from ib_async import Contract as IBContract

from .models import Execution, PortfolioSnapshot, Position, Quote, WorkingOrder, utcnow


class BrokerAdapter(Protocol):
    async def connect(self): ...
    async def reconcile(self) -> PortfolioSnapshot: ...
    async def closing_nav_observation(self) -> dict: ...
    def snapshot(self) -> PortfolioSnapshot: ...
    def quotes(self) -> dict[int, Quote]: ...
    def reserve_order_id(self) -> int: ...
    async def submit(self, request, order_id: int): ...
    async def cancel(self, order_id: int): ...
    async def disconnect(self): ...


def finite(value):
    return value is not None and math.isfinite(float(value))


class IBKRBroker:
    def __init__(self, manifest, event_queue: asyncio.Queue):
        self.manifest, self.events = manifest, event_queue
        self.ib = IB()
        self.ib.RaiseRequestErrors = True
        self.ib.RequestTimeout = 15
        self.ib.TimezoneTWS = "America/New_York"
        self.native = {}
        self.qualification_errors = {}
        self.readonly = True
        self.qualified = False
        self.reconciled = False
        self.reconcile_lock = asyncio.Lock()
        self.account_at = datetime.min.replace(tzinfo=timezone.utc)
        self.quote_times, self.greek_times = {}, {}
        self.ticks_by_contract = {}
        self.market_rules = {}
        self.external_quotes = None
        original_computation = self.ib.wrapper.tickOptionComputation

        def option_computation(req_id, tick_type, *args):
            original_computation(req_id, tick_type, *args)
            ticker = self.ib.wrapper.reqId2Ticker.get(req_id)
            if ticker is not None and tick_type == 13:
                self.greek_times[ticker.contract.conId] = utcnow()

        self.ib.wrapper.tickOptionComputation = option_computation
        self.ib.disconnectedEvent += self._disconnected
        self.ib.accountValueEvent += self._account
        self.ib.pendingTickersEvent += self._ticks
        self.ib.execDetailsEvent += self._execution
        self.ib.commissionReportEvent += self._commission
        self.ib.orderStatusEvent += self._order
        self.ib.positionEvent += lambda p: self.emit(
            "position", {"con_id": p.contract.conId, "quantity": str(p.position)}
        )
        self.ib.errorEvent += lambda req, code, msg, contract: self.emit(
            "broker_error",
            {"order_id": req, "code": code, "message": msg, "con_id": getattr(contract, "conId", None)},
        )

    def emit(self, event, value):
        self.events.put_nowait((event, value))

    def _disconnected(self):
        self.reconciled = False
        self.emit("disconnected", {})

    def _account(self, value):
        if value.account == self.manifest.account and value.tag == "NetLiquidation":
            self.account_at = utcnow()

    def _ticks(self, tickers):
        for ticker in tickers:
            identifier = ticker.contract.conId
            if any(t.tickType in (0, 1, 2, 3) for t in ticker.ticks):
                self.quote_times[identifier] = utcnow()
            # Greeks have no independent timestamp in the high-level API. Mark
            # freshness only when an option computation tick arrives.
            if any(t.tickType in (10, 11, 12, 13) for t in ticker.ticks):
                self.greek_times[identifier] = utcnow()

    def _execution(self, trade, fill):
        e = fill.execution
        if e.acctNumber != self.manifest.account:
            return
        if not float(e.shares).is_integer():
            self.emit("unsupported_execution", {"exec_id": e.execId, "shares": str(e.shares)})
            return
        correction = None
        # IBKR corrections increment the final component of an exec ID.
        if "." in e.execId and e.execId.rsplit(".", 1)[1].isdigit() and int(e.execId.rsplit(".", 1)[1]) > 1:
            correction = e.execId.rsplit(".", 1)[0] + ".01"
        at = fill.time if fill.time.tzinfo else fill.time.replace(tzinfo=timezone.utc)
        self.emit(
            "execution",
            Execution(
                exec_id=e.execId,
                order_id=e.orderId,
                perm_id=e.permId,
                order_ref=e.orderRef,
                account=e.acctNumber,
                con_id=fill.contract.conId,
                signed_quantity=int(e.shares) * (1 if e.side == "BOT" else -1),
                price=Decimal(str(e.price)),
                at=at,
                correction_of=correction,
            ),
        )

    def _commission(self, trade, fill, report):
        if fill.execution.acctNumber == self.manifest.account and finite(report.commission):
            self.emit(
                "commission",
                {"exec_id": report.execId, "amount": str(report.commission), "currency": report.currency},
            )

    def _order(self, trade):
        if trade.order.account == self.manifest.account:
            self.emit(
                "order",
                {
                    "order_id": trade.order.orderId,
                    "perm_id": trade.order.permId,
                    "order_ref": trade.order.orderRef,
                    "status": trade.orderStatus.status,
                    "filled": trade.orderStatus.filled,
                    "remaining": trade.orderStatus.remaining,
                },
            )

    async def connect(self):
        # connectAsync performs global account downloads too; a manual reconcile
        # must not overwrite their completion futures during startup.
        async with self.reconcile_lock:
            self.qualified = self.reconciled = False
            self.native.clear()
            self.qualification_errors.clear()
            try:
                await self._connect_and_qualify()
            except BaseException:
                self.qualified = self.reconciled = False
                self.ib.disconnect()
                raise

    async def _connect_and_qualify(self):
        await self.ib.connectAsync(
            self.manifest.gateway_host,
            self.manifest.gateway_port,
            clientId=self.manifest.client_id,
            account=self.manifest.account,
            readonly=self.readonly,
            timeout=15,
            raiseSyncErrors=True,
        )
        if self.manifest.account not in self.ib.managedAccounts() or not self.manifest.account.startswith(
            "DU"
        ):
            self.ib.disconnect()
            raise ValueError("Configured paper account not accessible; live trading is disabled")
        for c in self.manifest.contracts:
            try:
                await self._qualify_contract(c)
            except TimeoutError as exc:
                message = f"Contract qualification timed out: {c.label}"
                mandate_ids = set(self.manifest.mandatory)
                mandate_ids |= {
                    self.manifest.contract_map[i].underlying_id
                    for i in self.manifest.mandatory
                    if self.manifest.contract_map[i].kind == "OPT"
                }
                if c.con_id in mandate_ids:
                    raise ConnectionError(message) from exc
                self.qualification_errors[c.con_id] = message
                self.emit("qualification_unavailable", {"con_id": c.con_id, "message": message})
        snapshot = await self._download_state()
        self._validate_required_qualification(snapshot)
        self.qualified = True
        if self.manifest.market_data_provider == "ibkr":
            self.ib.reqMarketDataType(1)
            for native in self.native.values():
                self.ib.reqMktData(native, "236" if native.secType == "STK" else "", False, False)

    def _validate_required_qualification(self, snapshot):
        required = set(self.manifest.mandatory) | set(snapshot.quantities())
        required |= {order.con_id for order in snapshot.orders if order.signed_remaining}
        required |= {
            self.manifest.contract_map[i].underlying_id
            for i in tuple(required)
            if i in self.manifest.contract_map and self.manifest.contract_map[i].kind == "OPT"
        }
        missing = required - self.native.keys()
        if missing:
            labels = [
                self.manifest.contract_map[i].label if i in self.manifest.contract_map else str(i)
                for i in sorted(missing)
            ]
            raise ValueError("Required holding/order contracts unavailable: " + "; ".join(labels))

    async def _qualify_contract(self, c):
        details = await asyncio.wait_for(
            self.ib.reqContractDetailsAsync(IBContract(conId=c.con_id, exchange=c.exchange)), 15
        )
        if len(details) != 1:
            raise ValueError(f"Ambiguous/unavailable contract ID {c.con_id}")
        native = details[0].contract
        if (
            native.conId != c.con_id
            or native.symbol != c.symbol
            or native.secType != c.kind
            or native.currency != c.currency
        ):
            raise ValueError(f"Contract identity mismatch: {c.con_id}")
        if c.kind == "OPT":
            if (
                native.lastTradeDateOrContractMonth[:8] != c.expiry.strftime("%Y%m%d")
                or native.right != c.right
                or native.strike != c.strike
                or int(native.multiplier) != c.multiplier
            ):
                raise ValueError(f"Option expiry/strike/right/multiplier mismatch: {c.con_id}")
            if c.trading_class and native.tradingClass != c.trading_class:
                raise ValueError(f"Trading class mismatch: {c.con_id}")
        self.ticks_by_contract[c.con_id] = details[0].minTick
        exchanges = details[0].validExchanges.split(",")
        rules = details[0].marketRuleIds.split(",")
        if "SMART" in exchanges and len(rules) == len(exchanges):
            rule_id = int(rules[exchanges.index("SMART")] or 0)
            if rule_id:
                self.market_rules[c.con_id] = await self.ib.reqMarketRuleAsync(rule_id)
        self.native[c.con_id] = native

    async def closing_nav_observation(self):
        """Fresh, explicitly requested account NAV; never reuse summary cache."""
        async with self.reconcile_lock:
            if not self.ib.isConnected() or self.manifest.account not in self.ib.managedAccounts():
                raise ConnectionError("Configured paper account unavailable for closing NAV capture")
            observed = []

            def receive(value):
                if (
                    value.account == self.manifest.account
                    and value.tag == "NetLiquidation"
                    and value.currency in ("BASE", "USD")
                ):
                    observed.append((Decimal(value.value), utcnow()))

            request_at = utcnow()
            request_id = self.ib.client.getReqId()
            future = self.ib.wrapper.startReq(request_id)
            self.ib.accountSummaryEvent += receive
            try:
                self.ib.client.reqAccountSummary(request_id, "All", "NetLiquidation")
                await asyncio.wait_for(future, 15)
                if not observed or any(not value.is_finite() for value, _ in observed):
                    raise ValueError("Fresh IBKR closing NAV unavailable")
                if len({value for value, _ in observed}) != 1:
                    raise ValueError("Inconsistent closing NAV observations; retry capture")
                nav, observed_at = observed[-1]
                return {
                    "account": self.manifest.account,
                    "nav": nav,
                    "requested_at": request_at,
                    "observed_at": observed_at,
                    "source": "ibkr",
                }
            finally:
                self.ib.accountSummaryEvent -= receive
                self.ib.wrapper._endReq(request_id)
                if self.ib.isConnected():
                    self.ib.client.cancelAccountSummary(request_id)

    async def reconcile(self):
        # Several IB requests use one global completion future. Overlapping
        # downloads can overwrite it and strand a caller until timeout.
        async with self.reconcile_lock:
            return await self._download_state()

    async def _download_state(self):
        self.reconciled = False
        await self.ib.reqPositionsAsync()
        await self.ib.reqAllOpenOrdersAsync()
        completed = await self.ib.reqCompletedOrdersAsync(False)
        for trade in completed:
            self._order(trade)
        fills = await self.ib.reqExecutionsAsync(ExecutionFilter(acctCode=self.manifest.account))
        for fill in fills:
            self._execution(None, fill)
        # connectAsync already subscribes to account updates. Re-requesting the
        # same subscription can wait indefinitely for a new download-end marker.
        if not self.ib.accountValues(self.manifest.account):
            await asyncio.wait_for(self.ib.reqAccountUpdatesAsync(self.manifest.account), 15)
        self.reconciled = True
        snapshot = self.snapshot()
        try:
            self._validate_required_qualification(snapshot)
        except ValueError:
            self.reconciled = False
            raise
        return snapshot

    def snapshot(self):
        values = {
            v.tag: v.value
            for v in self.ib.accountValues(self.manifest.account)
            if v.currency in ("BASE", "USD", "")
        }
        required = ["NetLiquidation", "TotalCashValue", "ExcessLiquidity", "InitMarginReq"]
        if any(k not in values for k in required):
            raise ValueError("Required account NAV/cash/margin fields unavailable")
        positions = []
        for p in self.ib.positions(self.manifest.account):
            if not float(p.position).is_integer():
                raise ValueError("Fractional holdings require an additional validated adapter")
            positions.append(
                Position(
                    con_id=p.contract.conId, quantity=int(p.position), average_cost=Decimal(str(p.avgCost))
                )
            )
        orders = []
        for t in self.ib.openTrades():
            if t.order.account == self.manifest.account:
                remaining = t.orderStatus.remaining
                if not finite(remaining) or not float(remaining).is_integer():
                    raise ValueError("Unknown/fractional remaining order quantity")
                orders.append(
                    WorkingOrder(
                        order_id=t.order.orderId,
                        perm_id=t.order.permId,
                        con_id=t.contract.conId,
                        signed_remaining=int(remaining) * (1 if t.order.action == "BUY" else -1),
                        order_ref=t.order.orderRef,
                        status=t.orderStatus.status,
                    )
                )
        return PortfolioSnapshot(
            account=self.manifest.account,
            at=utcnow(),
            account_at=self.account_at,
            nav=Decimal(values["NetLiquidation"]),
            cash=Decimal(values["TotalCashValue"]),
            accrued_cash=Decimal(values["AccruedCash"]) if "AccruedCash" in values else None,
            settled_cash=Decimal(values["SettledCash"]) if "SettledCash" in values else None,
            excess_liquidity=Decimal(values["ExcessLiquidity"]),
            initial_margin=Decimal(values["InitMarginReq"]),
            positions=tuple(sorted(positions, key=lambda p: p.con_id)),
            orders=tuple(orders),
            connected=self.ib.isConnected(),
            reconciled=self.reconciled,
        )

    def quotes(self):
        if self.external_quotes is not None:
            return {i: q for i, q in self.external_quotes(utcnow()).items() if i in self.native}
        result = {}
        for i, c in self.manifest.contract_map.items():
            native = self.native.get(i)
            if native is None:
                continue
            t = self.ib.ticker(native)
            if t is None or not finite(t.bid) or not finite(t.ask) or t.bid < 0 or t.ask < t.bid:
                continue
            if i not in self.quote_times:
                continue
            g = t.modelGreeks
            spot = (
                t.marketPrice()
                if c.kind == "STK"
                else (g.undPrice if g and finite(g.undPrice) else float("nan"))
            )
            if not finite(spot) or spot <= 0:
                continue
            kwargs = {}
            if c.kind == "OPT":
                if (
                    not g
                    or not all(
                        finite(getattr(g, key)) for key in ("impliedVol", "delta", "gamma", "vega", "theta")
                    )
                    or g.impliedVol <= 0
                ):
                    continue
                # IB vega is per 1 percentage point, internal vega is per unit.
                kwargs = {
                    "iv": g.impliedVol,
                    "delta": g.delta,
                    "gamma": g.gamma,
                    "vega": g.vega * 100,
                    "theta": g.theta,
                    "greek_at": self.greek_times.get(i),
                    "model_price": g.optPrice if finite(g.optPrice) else None,
                }
            result[i] = Quote(
                provider="ibkr",
                con_id=i,
                bid=t.bid,
                ask=t.ask,
                bid_size=max(0, int(t.bidSize)) if finite(t.bidSize) else 0,
                ask_size=max(0, int(t.askSize)) if finite(t.askSize) else 0,
                observed_at=self.quote_times[i],
                spot=spot,
                shortable_shares=max(0, int(t.shortableShares))
                if c.kind == "STK" and finite(t.shortableShares)
                else None,
                data_type={1: "live", 2: "frozen", 3: "delayed", 4: "frozen"}.get(
                    t.marketDataType, "delayed"
                ),
                **kwargs,
            )
        return result

    def reserve_order_id(self):
        return self.ib.client.getReqId()

    def refresh_market_data(self):
        """Operator retry after enabling entitlements; old ticks cannot pass freshness."""
        if not self.ib.isConnected() or not self.qualified:
            raise ValueError("Broker contract qualification required before subscribing")
        self.ib.reqMarketDataType(1)
        for identifier, native in self.native.items():
            if self.ib.ticker(native) is not None:
                self.ib.cancelMktData(native)
            self.quote_times.pop(identifier, None)
            self.greek_times.pop(identifier, None)
            self.ib.reqMktData(native, "236" if native.secType == "STK" else "", False, False)

    def price_tick(self, con_id, price):
        applicable = [r.increment for r in self.market_rules.get(con_id, ()) if price >= r.lowEdge]
        if applicable:
            return applicable[-1]
        return self.ticks_by_contract.get(con_id, 0.01) or 0.01

    async def submit(self, request, order_id):
        if self.readonly or not self.qualified or not self.reconciled or not self.ib.isConnected():
            raise ValueError("Broker is not ready for paper execution")
        if request.con_id not in self.native:
            raise ValueError("Order contract has not passed broker qualification")
        if request.account != self.manifest.account or not request.account.startswith("DU"):
            raise ValueError("Paper account identity rejected")
        if request.con_id not in self.manifest.allowlist:
            raise ValueError("Discretionary trading of mandatory/unapproved instruments is forbidden")
        order = LimitOrder(
            "BUY" if request.signed_quantity > 0 else "SELL",
            abs(request.signed_quantity),
            float(request.limit_price),
            orderId=order_id,
            account=request.account,
            orderRef="hedge:" + request.intent_id,
            tif="DAY",
            outsideRth=False,
        )
        trade = self.ib.placeOrder(self.native[request.con_id], order)
        return {"order_id": trade.order.orderId, "perm_id": trade.order.permId}

    async def setup_mandate(self, request, order_id, now):
        if request.account != self.manifest.account or not request.account.startswith("DU"):
            raise ValueError("Paper account identity rejected")
        if (
            now.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York")).date()
            != self.manifest.purchase_session
        ):
            raise ValueError("Mandatory purchases are only allowed on the configured purchase session")
        if request.con_id not in self.manifest.mandatory or request.signed_quantity <= 0:
            raise ValueError("Invalid mandatory purchase")
        quantity = self.snapshot().projected().get(request.con_id, 0)
        if quantity + request.signed_quantity > self.manifest.mandatory[request.con_id]:
            raise ValueError("Mandatory purchase would exceed exact quantity")
        if self.readonly or not self.qualified or not self.reconciled or not self.ib.isConnected():
            raise ValueError("Broker not ready")
        order = LimitOrder(
            "BUY",
            request.signed_quantity,
            float(request.limit_price),
            orderId=order_id,
            account=request.account,
            orderRef="hedge:" + request.intent_id,
            tif="DAY",
            outsideRth=False,
        )
        trade = self.ib.placeOrder(self.native[request.con_id], order)
        return {"order_id": trade.order.orderId, "perm_id": trade.order.permId}

    async def cancel(self, order_id):
        for t in self.ib.openTrades():
            if (
                t.order.orderId == order_id
                and t.order.account == self.manifest.account
                and t.order.orderRef.startswith("hedge:")
            ):
                self.ib.cancelOrder(t.order)
                return
        # Do not claim cancellation succeeded merely because cache has no order.
        raise ValueError("System-owned working order not found; reconcile cancellation")

    async def replace(self, order_id, limit_price):
        for t in self.ib.openTrades():
            if (
                t.order.orderId == order_id
                and t.order.account == self.manifest.account
                and t.order.orderRef.startswith("hedge:")
            ):
                t.order.lmtPrice = float(limit_price)
                self.ib.placeOrder(t.contract, t.order)
                return
        raise ValueError("Cannot replace absent system order")

    async def margin_preview(self, request):
        order = LimitOrder(
            "BUY" if request.signed_quantity > 0 else "SELL",
            abs(request.signed_quantity),
            float(request.limit_price),
            account=request.account,
            tif="DAY",
            outsideRth=False,
        )
        return await self.ib.whatIfOrderAsync(self.native[request.con_id], order)

    async def history(self, con_id, end="", duration="20 D", bar_size="1 min", what="TRADES"):
        rows = await self.ib.reqHistoricalDataAsync(
            self.native[con_id],
            endDateTime=end,
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=what,
            useRTH=True,
            formatDate=2,
        )
        return [
            {
                "timestamp": b.date,
                "con_id": con_id,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
                "source": "ibkr",
            }
            for b in rows
        ]

    async def disconnect(self):
        self.reconciled = False
        self.ib.disconnect()


class SimBroker:
    """Explicit synthetic demo/replay. Never opens an IBKR connection."""

    def __init__(self, manifest, queue, now, start_empty=False):
        self.manifest, self.events, self.now = manifest, queue, now
        self.positions = {} if start_empty else dict(manifest.mandatory)
        self.cash = manifest.initial_nav
        self.accrued_cash = Decimal(0)
        self.nav = manifest.initial_nav
        self.connected = False
        self.reconciled = False
        self.qualified = True
        self.readonly = True
        self.orders = {}
        self._quotes = {}
        self._counter = 1000

    async def connect(self):
        self.connected = self.reconciled = True

    async def reconcile(self):
        self.reconciled = self.connected
        return self.snapshot()

    async def closing_nav_observation(self):
        if not self.connected:
            raise ConnectionError("Simulator disconnected")
        return {
            "account": self.manifest.account,
            "nav": self.nav,
            "requested_at": self.now,
            "observed_at": self.now,
            "source": "synthetic",
        }

    def snapshot(self):
        return PortfolioSnapshot(
            account=self.manifest.account,
            at=self.now,
            account_at=self.now,
            nav=self.nav,
            cash=self.cash,
            accrued_cash=self.accrued_cash,
            settled_cash=self.cash,
            excess_liquidity=self.nav * Decimal(".8"),
            initial_margin=self.nav * Decimal(".2"),
            positions=tuple(Position(con_id=i, quantity=q) for i, q in sorted(self.positions.items()) if q),
            orders=tuple(self.orders.values()),
            connected=self.connected,
            reconciled=self.reconciled,
            source="synthetic",
        )

    def quotes(self):
        return self._quotes

    def update_quotes(self, quotes, now):
        previous = self._quotes
        if previous:
            from .cashflows import cash_carry

            interval = (now - self.now).total_seconds() / 86400
            borrow = sum(
                max(0, -quantity) * previous[i].spot
                for i, quantity in self.positions.items()
                if self.manifest.contract_map[i].kind == "STK" and i in previous
            )
            accrued = Decimal(
                str(cash_carry(self.manifest, float(self.cash), borrow, interval, float(self.nav)))
            )
            self.accrued_cash += accrued
            self.nav += accrued
            for i, q in quotes.items():
                if i in previous:
                    self.nav += Decimal(
                        str(
                            (q.mid - previous[i].mid)
                            * self.positions.get(i, 0)
                            * self.manifest.contract_map[i].multiplier
                        )
                    )
        self._quotes, self.now = quotes, now

    def reserve_order_id(self):
        self._counter += 1
        return self._counter

    async def submit(self, request, order_id):
        if self.readonly or not self.connected:
            raise ValueError("Synthetic broker is read-only/disconnected")
        self.orders[order_id] = WorkingOrder(
            order_id=order_id,
            con_id=request.con_id,
            signed_remaining=request.signed_quantity,
            order_ref="hedge:" + request.intent_id,
        )
        self.events.put_nowait(
            (
                "order",
                {
                    "order_id": order_id,
                    "order_ref": "hedge:" + request.intent_id,
                    "status": "Submitted",
                    "filled": 0,
                    "remaining": abs(request.signed_quantity),
                },
            )
        )
        return {"order_id": order_id, "perm_id": order_id}

    async def fill(self, order_id, quantity=None):
        order = self.orders[order_id]
        signed = (
            order.signed_remaining
            if quantity is None
            else int(quantity) * (1 if order.signed_remaining > 0 else -1)
        )
        if abs(signed) > abs(order.signed_remaining):
            raise ValueError("Overfill")
        quote = self._quotes[order.con_id]
        price = Decimal(str(quote.ask if signed > 0 else quote.bid))
        c = self.manifest.contract_map[order.con_id]
        fee = max(
            self.manifest.policy.minimum_commission,
            abs(signed)
            * (
                self.manifest.policy.commission_option
                if c.kind == "OPT"
                else self.manifest.policy.commission_share
            ),
        )
        self.cash -= price * signed * c.multiplier + Decimal(str(fee))
        self.nav -= Decimal(str(abs(signed) * (quote.ask - quote.bid) / 2 * c.multiplier + fee))
        self.positions[order.con_id] = self.positions.get(order.con_id, 0) + signed
        remaining = order.signed_remaining - signed
        if remaining:
            self.orders[order_id] = order.model_copy(update={"signed_remaining": remaining})
        else:
            del self.orders[order_id]
        execution = Execution(
            exec_id=f"SIM.{order_id}.{self._counter}.{self.now.timestamp()}",
            order_id=order_id,
            account=self.manifest.account,
            con_id=order.con_id,
            signed_quantity=signed,
            price=price,
            at=self.now,
            order_ref=order.order_ref,
        )
        self.events.put_nowait(("execution", execution))
        self.events.put_nowait(
            ("commission", {"exec_id": execution.exec_id, "amount": str(fee), "currency": "USD"})
        )
        self.events.put_nowait(
            (
                "order",
                {
                    "order_id": order_id,
                    "order_ref": order.order_ref,
                    "status": "Filled" if not remaining else "Submitted",
                    "remaining": abs(remaining),
                    "filled": abs(order.signed_remaining) - abs(remaining),
                },
            )
        )

    async def setup_mandate(self, request, order_id, now):
        from .config import NY

        if (
            request.account != self.manifest.account
            or now.astimezone(NY).date() != self.manifest.purchase_session
        ):
            raise ValueError("Wrong account or outside configured purchase session")
        required = self.manifest.mandatory.get(request.con_id)
        current = self.snapshot().projected().get(request.con_id, 0)
        if required is None or not 0 <= current < current + request.signed_quantity <= required:
            raise ValueError("Day 0 may only buy missing mandatory quantity")
        return await self.submit(request, order_id)

    async def cancel(self, order_id):
        order = self.orders.pop(order_id, None)
        if order:
            self.events.put_nowait(
                (
                    "order",
                    {
                        "order_id": order_id,
                        "order_ref": order.order_ref,
                        "status": "Cancelled",
                        "remaining": 0,
                        "filled": 0,
                    },
                )
            )

    async def replace(self, order_id, limit_price):
        if order_id not in self.orders:
            raise ValueError("Unknown order")

    async def disconnect(self):
        self.connected = self.reconciled = False
