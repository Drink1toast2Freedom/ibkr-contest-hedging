"""Read-only qualification of additional US equities/ETFs; no order interface."""

import re

from ib_async import IB, Stock

from .models import Contract
from .practice import paper_account


async def qualify_stock_universe(manifest, symbols, ib=None):
    symbols = tuple(sorted(set(symbols)))
    if not symbols or len(symbols) > 12 or any(not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", s) for s in symbols):
        raise ValueError("Provide 1–12 US stock/ETF symbols")
    broker = ib or IB()
    broker.RaiseRequestErrors = True
    broker.RequestTimeout = 15
    additions = []
    try:
        await broker.connectAsync(
            "127.0.0.1", manifest.gateway_port, clientId=83, readonly=True, timeout=15, raiseSyncErrors=True
        )
        paper_account(broker.managedAccounts(), manifest.account)
        existing = {c.symbol for c in manifest.contracts if c.kind == "STK"}
        for symbol in symbols:
            if symbol in existing:
                continue
            details = await broker.reqContractDetailsAsync(Stock(symbol, "SMART", "USD"))
            if len(details) != 1:
                raise ValueError(f"Ambiguous/unavailable US stock: {symbol}")
            c = details[0].contract
            if c.symbol != symbol or c.secType != "STK" or c.currency != "USD" or not c.conId:
                raise ValueError(f"Instrument identity mismatch: {symbol}")
            additions.append(Contract(con_id=c.conId, symbol=symbol, kind="STK"))
        if len(existing) + len(additions) > 12:
            raise ValueError("At most 12 underlying symbols per validated optimization universe")
        return type(manifest).model_validate(
            {
                **manifest.model_dump(),
                "contracts": (*manifest.contracts, *additions),
                "allowlist": (*manifest.allowlist, *(c.con_id for c in additions)),
            }
        )
    finally:
        broker.disconnect()
