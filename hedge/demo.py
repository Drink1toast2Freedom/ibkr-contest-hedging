import math
from datetime import timedelta

import exchange_calendars as xcals
import pandas as pd

from .config import NY, Manifest
from .models import Contract, Quote, utcnow
from .valuation import ValuationEngine


def demo_manifest():
    calendar = xcals.get_calendar("XNYS")
    today = utcnow().astimezone(NY).date()
    session = calendar.date_to_session(pd.Timestamp(today), direction="previous")
    zero = calendar.previous_session(session).date()
    sessions = tuple(calendar.sessions_window(session, 3).date)
    expiry = sessions[-1] + timedelta(days=30)
    contracts = [Contract(con_id=10, symbol="BA", kind="STK")]
    for identifier, strike, right in [
        (101, 195, "C"),
        (102, 195, "P"),
        (103, 190, "C"),
        (104, 200, "C"),
        (105, 190, "P"),
        (106, 200, "P"),
    ]:
        contracts.append(
            Contract(
                con_id=identifier,
                symbol="BA",
                kind="OPT",
                multiplier=100,
                expiry=expiry,
                strike=strike,
                right=right,
                underlying_id=10,
            )
        )
    return Manifest(
        name="Synthetic BA demonstration",
        account="DU_SYNTHETIC",
        day_zero=zero,
        scoring_sessions=sessions,
        valuation_convention="SYNTHETIC demonstration NAV",
        report_source="SYNTHETIC demo generator — not IBKR or contest evidence",
        confirmed=True,
        contracts=tuple(contracts),
        mandatory={101: 25, 102: 25},
        allowlist=(10, 103, 104, 105, 106),
    )


def demo_quotes(manifest, now, elapsed=0):
    spot = 195 + 1.3 * math.sin(elapsed / 120) + 0.25 * math.sin(elapsed / 17)
    iv = 0.28 + 0.005 * math.sin(elapsed / 200)
    valuation = ValuationEngine(manifest)
    quotes = {}
    for c in manifest.contracts:
        price = valuation.price(c, spot, iv, now)
        greeks = valuation.greeks(c, spot, iv, now) if c.kind == "OPT" else {}
        spread = 0.08 if c.kind == "OPT" else 0.02
        quotes[c.con_id] = Quote(
            con_id=c.con_id,
            bid=max(0.01, price - spread / 2),
            ask=price + spread / 2,
            bid_size=20 if c.kind == "OPT" else 500,
            ask_size=20 if c.kind == "OPT" else 500,
            observed_at=now,
            data_type="synthetic",
            spot=spot,
            iv=iv if c.kind == "OPT" else None,
            greek_at=now,
            model_price=price,
            **greeks,
        )
        if c.kind == "STK":
            quotes[c.con_id] = quotes[c.con_id].model_copy(update={"shortable_shares": 100000})
    return quotes


def rehearsal_manifest():
    """Fictional dates and IDs: never a proposed broker contest manifest."""
    manifest = demo_manifest()
    calendar = xcals.get_calendar("XNYS")
    zero = calendar.date_to_session(pd.Timestamp(utcnow().astimezone(NY).date()), direction="next")
    sessions = tuple(calendar.sessions_window(calendar.next_session(zero), 2).date)
    return Manifest.model_validate(
        {
            **manifest.model_dump(),
            "name": "Synthetic Day 0 purchase rehearsal",
            "account": "DU_REHEARSAL",
            "day_zero": zero.date(),
            "scoring_sessions": sessions,
        }
    )
