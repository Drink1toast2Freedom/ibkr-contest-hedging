"""Read-only feed archive adapter. Its quotes can never authorize execution."""

import json
from datetime import datetime, timedelta
from pathlib import Path

from .config import NY
from .models import Quote
from .valuation import ValuationEngine


def code_for(contract):
    if contract.kind == "STK":
        return f"US.{contract.symbol}"
    return f"US.{contract.symbol}{contract.expiry:%y%m%d}{contract.right}{round(contract.strike * 1000)}"


class MoomooData:
    def __init__(self, manifest, path="data/moomoo/latest.json"):
        self.path = Path(path)
        self.manifest = manifest
        self.engine = ValuationEngine(manifest)
        self.received_at = None
        self.errors = []
        self.cached_version = None
        self.cached_quotes = {}

    def quotes(self, now):
        try:
            raw = json.loads(self.path.read_text())
            if raw.get("purpose") != "market_data_only" or raw.get("execution_enabled") is not False:
                raise ValueError("Untrusted feed purpose")
            received = datetime.fromisoformat(raw["received_at"])
            if received.tzinfo is None or not 0 <= (now - received).total_seconds() <= 90:
                raise ValueError("Moomoo collector stale or invalid timestamp")
            self.received_at = received.isoformat()
            if self.cached_version == raw["received_at"]:
                return self.cached_quotes
            securities = raw["securities"]
            quotes, errors = {}, []
            for contract in self.manifest.contracts:
                try:
                    item = securities[code_for(contract)]
                    row = item["quote"][0]
                    book = item["order_book"]
                    at = datetime.fromisoformat(row["data_date"] + "T" + row["data_time"]).replace(tzinfo=NY)
                    if at > now:
                        raise ValueError("Future provider timestamp")
                    stock = securities[f"US.{contract.symbol}"]["quote"][0]
                    stock_at = datetime.fromisoformat(stock["data_date"] + "T" + stock["data_time"]).replace(
                        tzinfo=NY
                    )
                    spot = float(stock["last_price"])
                    bid, ask = float(book["Bid"][0][0]), float(book["Ask"][0][0])
                    live = False
                    book_at = at
                    if raw.get("quote_server_connected") and item.get("book_push_count", 0) >= 2:
                        book_at = datetime.fromisoformat(item["book_push_at"])
                        permission = raw.get("rights", {}).get(
                            "us_option_qot_right" if contract.kind == "OPT" else "us_qot_right"
                        )
                        max_age = (
                            self.manifest.risk.option_quote_age
                            if contract.kind == "OPT"
                            else self.manifest.risk.stock_quote_age
                        )
                        live = (
                            permission in ("LV1", "LV2", "LV3", "SF")
                            and raw.get("rights", {}).get("us_qot_right") in ("LV1", "LV2", "LV3", "SF")
                            and timedelta(0)
                            <= now - stock_at
                            <= timedelta(seconds=self.manifest.risk.stock_quote_age)
                            and book_at.tzinfo is not None
                            and timedelta(0) <= now - book_at <= timedelta(seconds=max_age)
                            and timedelta(0) <= now - at <= timedelta(seconds=max_age)
                            and self.manifest.session_open(now.astimezone(NY).date())
                            <= now
                            < self.manifest.session_close(now.astimezone(NY).date())
                        )
                    iv, greeks = None, {}
                    if contract.kind == "OPT":
                        if (
                            float(row["strike_price"]) != contract.strike
                            or int(row["contract_multiplier"]) != contract.multiplier
                        ):
                            raise ValueError("Provider contract identity mismatch")
                        iv = float(row["implied_volatility"]) / 100
                        identity = raw.get("identities", {}).get(code_for(contract), {})
                        live = live and (
                            identity.get("stock_owner") == f"US.{contract.symbol}"
                            and identity.get("strike_time") == str(contract.expiry)
                            and identity.get("option_type") == ("CALL" if contract.right == "C" else "PUT")
                            and float(identity.get("strike_price", 0)) == contract.strike
                        )
                        greeks = self.engine.greeks(contract, spot, iv, now if live else at)
                    quotes[contract.con_id] = Quote(
                        provider="moomoo",
                        con_id=contract.con_id,
                        bid=bid,
                        ask=ask,
                        bid_size=int(book["Bid"][0][1]),
                        ask_size=int(book["Ask"][0][1]),
                        observed_at=min(at, book_at) if live else at,
                        data_type="live" if live else "frozen",
                        spot=spot,
                        iv=iv,
                        greek_at=(now if live else at) if iv else None,
                        **greeks,
                    )
                except (KeyError, IndexError, TypeError, ValueError):
                    errors.append(f"Missing or invalid moomoo data: {contract.label}")
            self.errors = errors
            self.cached_version, self.cached_quotes = raw["received_at"], quotes
            return quotes
        except (OSError, KeyError, TypeError, ValueError):
            self.errors = ["Moomoo feed unavailable, stale, or invalid; no executable data"]
            return {}
