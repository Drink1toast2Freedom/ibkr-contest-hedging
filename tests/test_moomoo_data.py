import json
from datetime import timedelta

from hedge.config import NY
from hedge.demo import demo_manifest
from hedge.models import utcnow
from hedge.moomoo_data import MoomooData, code_for


def fixture(manifest, now):
    at = (now - timedelta(minutes=20)).astimezone(NY)
    securities = {}
    for c in manifest.contracts:
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
            "order_book": {"Bid": [[1, 10]], "Ask": [[2, 10]]},
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
