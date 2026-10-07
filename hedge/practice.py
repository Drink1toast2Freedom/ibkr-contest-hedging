"""Read-only discovery. This module has no order-submission interface."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

import exchange_calendars as xcals
import pandas as pd
import yaml
from ib_async import IB, Option, Stock
from pydantic import Field, model_validator

from .config import Manifest
from .models import Contract, Frozen, utcnow


class PracticeInputs(Frozen):
    rules_confirmed: bool = False
    account: str | None = None
    day_zero: date
    scoring_sessions: tuple[date, ...]
    expiry: date
    strike: Literal[195] = 195
    call_quantity: Literal[25] = 25
    put_quantity: Literal[25] = 25
    multiplier: Literal[100] = 100
    purchase_on_day: Literal[0, 1] = 0
    report_source: str = Field(min_length=3)
    gateway_port: Literal[7497, 4002] = 7497
    client_id: int = Field(default=72, gt=0)

    @model_validator(mode="after")
    def confirmed_calendar(self):
        if not self.rules_confirmed:
            raise ValueError("Resolve conflicting dates/expiry and set rules_confirmed before discovery")
        if self.account is not None and not self.account.startswith("DU"):
            raise ValueError("Paper DU account required")
        if not self.scoring_sessions or tuple(sorted(set(self.scoring_sessions))) != self.scoring_sessions:
            raise ValueError("Practice requires explicit, sorted, unique scoring sessions")
        if not self.day_zero < self.scoring_sessions[0] <= self.scoring_sessions[-1] <= self.expiry:
            raise ValueError("Practice baseline, scoring dates, and expiry are contradictory")
        calendar = xcals.get_calendar("XNYS")
        for day in (self.day_zero, *self.scoring_sessions):
            if not calendar.is_session(pd.Timestamp(day)):
                raise ValueError(f"Not an exchange trading session: {day}")
        return self


def paper_account(accounts, requested=None):
    available = sorted({a for a in accounts if a.startswith("DU")})
    if requested is not None:
        if requested not in available:
            raise ValueError("Requested paper account is not accessible")
        return requested
    if len(available) != 1:
        raise ValueError("Specify the account: exactly one accessible DU paper account is required")
    return available[0]


async def probe_paper(port=7497, account=None, ib=None):
    broker = ib or IB()
    broker.RaiseRequestErrors = True
    broker.RequestTimeout = 15
    try:
        await broker.connectAsync(
            "127.0.0.1", port, clientId=72, readonly=True, timeout=15, raiseSyncErrors=True
        )
        selected = paper_account(broker.managedAccounts(), account)
        summary = await broker.accountSummaryAsync(selected)
        return {
            "observed_at": utcnow().isoformat(),
            "account": selected,
            "readonly": True,
            "account_values": [
                {"tag": v.tag, "value": v.value, "currency": v.currency}
                for v in summary
                if v.account == selected
            ],
            "positions": [
                {
                    "con_id": p.contract.conId,
                    "symbol": p.contract.symbol,
                    "kind": p.contract.secType,
                    "quantity": str(p.position),
                    "expiry": p.contract.lastTradeDateOrContractMonth,
                    "strike": p.contract.strike,
                    "right": p.contract.right,
                    "multiplier": p.contract.multiplier,
                }
                for p in broker.positions(selected)
            ],
            "note": "Read-only connection evidence; not an official closing NAV or order authorization",
        }
    finally:
        broker.disconnect()


def normalized_option(details, inputs, underlying_id, expiry, strike, right):
    if len(details) != 1:
        raise ValueError(
            f"Ambiguous/unavailable BA {expiry} {strike}{right}; never substitute another expiry"
        )
    native = details[0].contract
    if (
        native.symbol != "BA"
        or native.secType != "OPT"
        or native.currency != "USD"
        or native.lastTradeDateOrContractMonth[:8] != expiry.strftime("%Y%m%d")
        or native.strike != strike
        or native.right != right
        or Decimal(native.multiplier or "0") != inputs.multiplier
        or native.tradingClass != "BA"
        or getattr(details[0], "underConId", 0) != underlying_id
    ):
        raise ValueError("BA option identity/multiplier differs from confirmed practice requirements")
    return Contract(
        con_id=native.conId,
        symbol="BA",
        kind="OPT",
        multiplier=100,
        expiry=expiry,
        strike=strike,
        right=right,
        trading_class=native.tradingClass,
        underlying_id=underlying_id,
    )


async def prepare_practice(path, ib=None):
    inputs = PracticeInputs.model_validate(yaml.safe_load(Path(path).read_text()))
    broker = ib or IB()
    broker.RaiseRequestErrors = True
    broker.RequestTimeout = 15
    try:
        await broker.connectAsync(
            "127.0.0.1",
            inputs.gateway_port,
            clientId=inputs.client_id,
            readonly=True,
            timeout=15,
            raiseSyncErrors=True,
        )
        account = paper_account(broker.managedAccounts(), inputs.account)
        stock_details = await broker.reqContractDetailsAsync(
            Stock("BA", "SMART", "USD", primaryExchange="NYSE")
        )
        if len(stock_details) != 1:
            raise ValueError("BA stock contract qualification ambiguous")
        stock = stock_details[0].contract
        if stock.symbol != "BA" or stock.secType != "STK" or stock.currency != "USD":
            raise ValueError("Underlying is not BA USD stock")
        contracts = [Contract(con_id=stock.conId, symbol="BA", kind="STK")]
        chains = await broker.reqSecDefOptParamsAsync("BA", "", "STK", stock.conId)
        eligible = [
            c
            for c in chains
            if c.exchange == "SMART" and c.tradingClass == "BA" and Decimal(c.multiplier or "0") == 100
        ]
        if len(eligible) != 1:
            raise ValueError("Standard BA option chain is ambiguous/unavailable")
        chain = eligible[0]
        if inputs.expiry.strftime("%Y%m%d") not in chain.expirations or inputs.strike not in chain.strikes:
            raise ValueError("Confirmed mandatory expiry/strike is absent from the IBKR option chain")

        async def qualify(expiry, strike, right):
            details = await broker.reqContractDetailsAsync(
                Option(
                    "BA",
                    expiry.strftime("%Y%m%d"),
                    strike,
                    right,
                    "SMART",
                    multiplier="100",
                    currency="USD",
                    tradingClass="BA",
                )
            )
            return normalized_option(details, inputs, stock.conId, expiry, strike, right)

        mandatory = {}
        for right, quantity in [("C", inputs.call_quantity), ("P", inputs.put_quantity)]:
            c = await qualify(inputs.expiry, inputs.strike, right)
            contracts.append(c)
            mandatory[c.con_id] = quantity
        # Deterministic near-strike screen: 8 same-expiry plus 4 next-expiry candidates.
        near = sorted(
            (s for s in chain.strikes if s > 0 and s != inputs.strike),
            key=lambda s: (abs(s - inputs.strike), s),
        )[:4]
        candidate_specs = [(inputs.expiry, s, r) for s in near for r in ("C", "P")]
        later = sorted(
            date.fromisoformat(f"{e[:4]}-{e[4:6]}-{e[6:8]}")
            for e in chain.expirations
            if e > inputs.expiry.strftime("%Y%m%d")
        )
        if later:
            candidate_specs += [(later[0], s, r) for s in [inputs.strike, *near[:1]] for r in ("C", "P")]
        for expiry, strike, right in candidate_specs:
            contracts.append(await qualify(expiry, strike, right))
        return Manifest(
            name="BA practice",
            account=account,
            day_zero=inputs.day_zero,
            mandatory_purchase_session=inputs.scoring_sessions[0] if inputs.purchase_on_day == 1 else None,
            scoring_sessions=inputs.scoring_sessions,
            contracts=tuple(contracts),
            mandatory=mandatory,
            allowlist=tuple(c.con_id for c in contracts if c.con_id not in mandatory),
            valuation_convention="IBKR official closing account NAV",
            report_source=inputs.report_source,
            gateway_port=inputs.gateway_port,
            confirmed=False,
        )
    finally:
        broker.disconnect()
