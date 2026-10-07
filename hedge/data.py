from pathlib import Path

import pandas as pd


class QuoteRecorder:
    def __init__(self, directory="data/quotes"):
        self.directory = Path(directory)
        self.buffer = []

    def append(self, quotes, contracts, at):
        for i, q in quotes.items():
            self.buffer.append(
                {
                    **q.model_dump(mode="json"),
                    "timestamp": at.isoformat(),
                    "symbol": contracts[i].symbol,
                    "kind": contracts[i].kind,
                    "expiry": str(contracts[i].expiry),
                    "strike": contracts[i].strike,
                    "right": contracts[i].right,
                    "source": q.data_type,
                }
            )
        if len(self.buffer) >= 1000:
            self.flush()

    def flush(self):
        if not self.buffer:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        from uuid import uuid4

        pd.DataFrame(self.buffer).to_parquet(self.directory / f"quotes-{uuid4().hex}.parquet", index=False)
        self.buffer.clear()


def import_cboe(path, output):
    """Normalize DataShop files. Does not purchase data or invent Greeks."""
    frame = pd.read_csv(path)
    frame.columns = [c.strip().lower().replace(" ", "_") for c in frame.columns]
    aliases = {
        "underlying_symbol": "symbol",
        "quote_datetime": "timestamp",
        "expiration": "expiry",
        "option_type": "right",
        "implied_volatility": "iv",
    }
    frame = frame.rename(columns=aliases)
    required = {"symbol", "timestamp", "expiry", "strike", "right", "bid", "ask", "bid_size", "ask_size"}
    if not required.issubset(frame.columns):
        raise ValueError(f"Missing DataShop fields: {sorted(required - set(frame.columns))}")
    times = pd.to_datetime(frame.timestamp)
    if times.dt.tz is None:
        times = times.dt.tz_localize("America/New_York", ambiguous="raise", nonexistent="raise")
    frame["timestamp"] = times.dt.tz_convert("UTC")
    if "underlying_bid" in frame and "underlying_ask" in frame:
        frame["spot"] = (frame.underlying_bid + frame.underlying_ask) / 2
    elif "active_underlying_price" in frame:
        frame["spot"] = frame.active_underlying_price
    else:
        raise ValueError("Underlying quote/price required")
    if ((frame.bid < 0) | (frame.ask < frame.bid)).any():
        raise ValueError("Negative or crossed archived quotes")
    frame["source"] = "cboe_datashop"
    frame["evidence"] = "historical_quotes"
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(output, index=False)
    return len(frame)
