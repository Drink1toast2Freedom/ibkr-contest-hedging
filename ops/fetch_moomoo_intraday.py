"""Quote-only paginated US minute archive, unadjusted prices and interval ends."""

import argparse
import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=["SPY", "QQQ"])
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--calendar-days", type=int, default=180)
    parser.add_argument("--output", default="data/recovery/intraday.csv")
    args = parser.parse_args()
    import moomoo as sdk
    import pandas as pd

    ctx = sdk.OpenQuoteContext(host="127.0.0.1", port=11111)
    frames = []
    try:
        for symbol in args.symbols:
            page, number = None, 0
            while True:
                ret, data, page = ctx.request_history_kline(
                    "US." + symbol,
                    start=str(args.as_of - timedelta(days=args.calendar_days)),
                    end=str(args.as_of),
                    ktype=sdk.KLType.K_1M,
                    autype=sdk.AuType.NONE,
                    max_count=1000,
                    page_req_key=page,
                    extended_time=False,
                )
                if ret != sdk.RET_OK:
                    raise ValueError(f"Historical minute coverage unavailable: {symbol}: {data}")
                frame = data[["time_key", "open", "high", "low", "close", "volume"]].copy()
                frame["timestamp"] = pd.to_datetime(frame.time_key).dt.tz_localize(
                    ZoneInfo("America/New_York")
                ).dt.tz_convert("UTC") + pd.Timedelta(minutes=1)
                frame["symbol"] = symbol
                frames.append(frame.drop(columns="time_key"))
                number += 1
                if number % 10 == 0 or page is None:
                    print(f"{symbol}: {number} pages archived", flush=True)
                if page is None:
                    break
                time.sleep(0.75)  # <=40 requests/30sec, below documented 60 limit
        joined = pd.concat(frames, ignore_index=True).drop_duplicates(["timestamp", "symbol"])
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        joined.to_csv(path, index=False)
        path.with_suffix(".json").write_text(
            json.dumps(
                {
                    "provider": "moomoo",
                    "purpose": "market_data_only",
                    "execution_enabled": False,
                    "adjustment": "NONE",
                    "interval_seconds": 60,
                    "timestamp_convention": "provider timestamp plus 60s conservative availability",
                    "received_at": datetime.now(timezone.utc).isoformat(),
                    "as_of": str(args.as_of),
                    "symbols": args.symbols,
                    "rows": len(joined),
                }
            )
        )
        print(f"{len(joined)} minute bars archived; OHLC is not bid/ask execution evidence", flush=True)
    finally:
        ctx.close()


if __name__ == "__main__":
    main()
