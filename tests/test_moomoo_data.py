import json
from datetime import timedelta

from hedge.config import NY
from hedge.demo import demo_manifest
from hedge.models import utcnow
from hedge.moomoo_data import MoomooData, code_for
from hedge.valuation import ValuationEngine


def fixture(manifest, now):
    at = (now - timedelta(minutes=20)).astimezone(NY)
    securities = {}
    for c in manifest.contracts:
        price = ValuationEngine(manifest).price(c, 195, 0.3, now) if c.kind == "OPT" else 195
        securities[code_for(c)] = {
            "quote": [
                {
                    "code": code_for(c),
                    "data_date": str(at.date()),
                    "data_time": at.time().isoformat(),
                    "last_price": 195,
                    "strike_price": c.strike,
                    "contract_multiplier": c.multiplier,
                    "implied_volatility": 30,
                }
            ],
            "order_book": {"Bid": [[price - 0.01, 10]], "Ask": [[price + 0.01, 10]]},
        }
    return {
        "purpose": "market_data_only",
        "execution_enabled": False,
        "received_at": now.isoformat(),
        "securities": securities,
    }


def test_recent_receipt_does_not_make_cached_quote_live(tmp_path):
    m, now = demo_manifest(), utcnow()
    path = tmp_path / "quotes.json"
    raw = fixture(m, now)
    path.write_text(json.dumps(raw))
    feed = MoomooData(m, path)
    quotes = feed.quotes(now)
    assert len(quotes) == len(m.contracts)
    assert all(q.data_type == "frozen" for q in quotes.values())
    assert all((now - q.observed_at).total_seconds() >= 1200 for q in quotes.values())
    assert quotes[101].iv == 0.3
    assert feed.quotes(now + timedelta(seconds=91)) == {}


def test_mismatched_contract_and_untrusted_purpose_blocked(tmp_path):
    m, now = demo_manifest(), utcnow()
    path = tmp_path / "quotes.json"
    raw = fixture(m, now)
    raw["securities"][code_for(m.contract_map[101])]["quote"][0]["contract_multiplier"] = 10
    path.write_text(json.dumps(raw))
    feed = MoomooData(m, path)
    assert 101 not in feed.quotes(now)
    assert feed.errors
    raw["execution_enabled"] = True
    path.write_text(json.dumps(raw))
    assert feed.quotes(now) == {}


def test_stream_requires_fresh_prices_permissions_identity_and_later_book_pushes(tmp_path):
    from datetime import datetime

    m = demo_manifest()
    now = datetime.combine(m.scoring_sessions[0], datetime.min.time(), NY).replace(hour=10)
    raw = fixture(m, now)
    raw.update(
        quote_server_connected=True,
        rights={"us_qot_right": "LV3", "us_option_qot_right": "LV1"},
        identities={},
    )
    for c in m.contracts:
        item = raw["securities"][code_for(c)]
        item["quote"][0].update(data_date=str(now.date()), data_time=now.time().isoformat())
        item.update(book_push_at=now.isoformat(), book_push_count=2)
        if c.kind == "OPT":
            raw["identities"][code_for(c)] = {
                "stock_owner": "US.BA",
                "strike_time": str(c.expiry),
                "option_type": "CALL" if c.right == "C" else "PUT",
                "strike_price": c.strike,
            }
    p = tmp_path / "quotes.json"
    p.write_text(json.dumps(raw))
    assert all(q.data_type == "live" for q in MoomooData(m, p).quotes(now).values())
    raw["securities"][code_for(m.contract_map[101])]["book_push_count"] = 1
    p.write_text(json.dumps(raw))
    assert MoomooData(m, p).quotes(now)[101].data_type == "frozen"
    raw["rights"]["us_option_qot_right"] = "NO"
    p.write_text(json.dumps(raw))
    assert all(q.data_type == "frozen" for i, q in MoomooData(m, p).quotes(now).items() if i != 10)


def test_fresh_option_cannot_use_stale_underlying_for_greeks(tmp_path):
    from datetime import datetime

    m = demo_manifest()
    now = datetime.combine(m.scoring_sessions[0], datetime.min.time(), NY).replace(hour=10)
    raw = fixture(m, now)
    raw.update(
        quote_server_connected=True,
        rights={"us_qot_right": "LV3", "us_option_qot_right": "LV1"},
        identities={},
    )
    for c in m.contracts:
        item = raw["securities"][code_for(c)]
        item["quote"][0].update(data_date=str(now.date()), data_time=now.time().isoformat())
        item.update(book_push_at=now.isoformat(), book_push_count=2)
        if c.kind == "OPT":
            raw["identities"][code_for(c)] = {
                "stock_owner": "US.BA",
                "strike_time": str(c.expiry),
                "option_type": "CALL" if c.right == "C" else "PUT",
                "strike_price": c.strike,
            }
    stale = now - timedelta(seconds=6)
    raw["securities"]["US.BA"]["book_push_at"] = stale.isoformat()
    path = tmp_path / "quotes.json"
    path.write_text(json.dumps(raw))
    assert all(q.data_type == "frozen" for q in MoomooData(m, path).quotes(now).values())


def test_fresh_book_with_old_last_trade_uses_calibrated_iv(tmp_path):
    from datetime import datetime

    m = demo_manifest()
    now = datetime.combine(m.scoring_sessions[0], datetime.min.time(), NY).replace(hour=10)
    raw = fixture(m, now)
    raw.update(
        quote_server_connected=True,
        rights={"us_qot_right": "LV3", "us_option_qot_right": "LV1"},
        identities={},
    )
    for c in m.contracts:
        item = raw["securities"][code_for(c)]
        item.update(book_push_at=now.isoformat(), book_push_count=2)
        if c.kind == "OPT":
            item["quote"][0]["implied_volatility"] = 90
            raw["identities"][code_for(c)] = {
                "stock_owner": "US.BA",
                "strike_time": str(c.expiry),
                "option_type": "CALL" if c.right == "C" else "PUT",
                "strike_price": c.strike,
            }
    path = tmp_path / "quotes.json"
    path.write_text(json.dumps(raw))
    feed = MoomooData(m, path)
    quotes = feed.quotes(now)
    assert all(q.data_type == "live" for q in quotes.values())
    assert quotes[101].observed_at == now
    assert abs(quotes[101].iv - 0.3) < 0.001
    assert abs(quotes[101].model_price - quotes[101].mid) < 0.0001
    # A still-running collector/cache cannot turn an old book into a new quote.
    assert all(q.data_type == "frozen" for q in feed.quotes(now + timedelta(seconds=6)).values())
    raw["securities"][code_for(m.contract_map[101])]["order_book"] = {
        "Bid": [[0.00001, 10]],
        "Ask": [[0.00002, 10]],
    }
    path.write_text(json.dumps(raw))
    assert MoomooData(m, path).quotes(now)[101].data_type == "frozen"


def test_old_source_book_time_is_not_hidden_by_new_callback(tmp_path):
    from datetime import datetime

    m = demo_manifest()
    now = datetime.combine(m.scoring_sessions[0], datetime.min.time(), NY).replace(hour=10)
    item = fixture(m, now)["securities"]["US.BA"]
    item.update(book_push_at=now.isoformat(), book_push_count=2)
    source = now - timedelta(minutes=15)
    item["order_book"]["svr_recv_time_bid"] = source.isoformat()
    assert MoomooData.book_time(item) == source


def test_option_book_one_minute_boundary_with_fresh_underlying(tmp_path):
    from datetime import datetime

    m = demo_manifest()
    assert m.risk.option_quote_age == 60
    now = datetime.combine(m.scoring_sessions[0], datetime.min.time(), NY).replace(hour=10)
    raw = fixture(m, now)
    raw.update(
        quote_server_connected=True,
        rights={"us_qot_right": "LV3", "us_option_qot_right": "LV1"},
        identities={},
    )
    for c in m.contracts:
        item = raw["securities"][code_for(c)]
        book_at = now - timedelta(seconds=60) if c.kind == "OPT" else now
        item.update(book_push_at=book_at.isoformat(), book_push_count=2)
        if c.kind == "OPT":
            raw["identities"][code_for(c)] = {
                "stock_owner": "US.BA",
                "strike_time": str(c.expiry),
                "option_type": "CALL" if c.right == "C" else "PUT",
                "strike_price": c.strike,
            }
    path = tmp_path / "quotes.json"
    path.write_text(json.dumps(raw))
    feed = MoomooData(m, path)
    assert all(q.data_type == "live" for q in feed.quotes(now).values())
    quotes = feed.quotes(now + timedelta(seconds=1))
    assert quotes[10].data_type == "live"
    assert all(q.data_type == "frozen" for i, q in quotes.items() if i != 10)


def test_book_freshness_uses_latest_side_update_without_reviving_old_book():
    from datetime import datetime

    now = datetime(2026, 10, 7, 12, tzinfo=NY)
    old = now - timedelta(minutes=10)
    fresh = now - timedelta(seconds=1)
    item = {
        "book_push_count": 3,
        "book_push_at": now.isoformat(),
        "order_book": {
            "Bid": [[195.0, 10]],
            "Ask": [[195.01, 10]],
            "svr_recv_time_bid": old.isoformat(),
            "svr_recv_time_ask": fresh.isoformat(),
        },
    }
    assert MoomooData.book_time(item) == fresh
    item["order_book"]["svr_recv_time_ask"] = old.isoformat()
    assert MoomooData.book_time(item) == old


def test_future_last_trade_does_not_hide_verified_current_book(tmp_path):
    from datetime import datetime

    m = demo_manifest()
    now = datetime.combine(m.scoring_sessions[0], datetime.min.time(), NY).replace(hour=10)
    raw = fixture(m, now)
    raw.update(
        quote_server_connected=True,
        rights={"us_qot_right": "LV3", "us_option_qot_right": "LV1"},
        identities={},
    )
    for c in m.contracts:
        item = raw["securities"][code_for(c)]
        vendor_trade = now + timedelta(milliseconds=100)
        item["quote"][0].update(data_date=str(vendor_trade.date()), data_time=vendor_trade.time().isoformat())
        item.update(book_push_at=now.isoformat(), book_push_count=2)
        if c.kind == "OPT":
            raw["identities"][code_for(c)] = {
                "stock_owner": "US.BA",
                "strike_time": str(c.expiry),
                "option_type": "CALL" if c.right == "C" else "PUT",
                "strike_price": c.strike,
            }
    path = tmp_path / "quotes.json"
    path.write_text(json.dumps(raw))
    feed = MoomooData(m, path)
    assert len(feed.quotes(now)) == len(m.contracts)
    assert all(q.data_type == "live" and q.observed_at == now for q in feed.quotes(now).values())
    # Same future trade without subsequent verified book updates remains unusable.
    for item in raw["securities"].values():
        item["book_push_count"] = 1
    path.write_text(json.dumps(raw))
    assert feed.quotes(now) == {}
