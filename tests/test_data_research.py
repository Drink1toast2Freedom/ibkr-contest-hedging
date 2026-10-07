from datetime import datetime, time

import numpy as np
import pandas as pd
import pytest

from hedge.config import NY
from hedge.data import import_cboe
from hedge.demo import demo_quotes
from hedge.research import ReplayEngine, Variant, match_window, normalized_quotes
from hedge.scenarios import ScenarioEngine


def archive(manifest):
    rows = []
    for n, day in enumerate((manifest.day_zero, *manifest.scoring_sessions)):
        for hour, minute in [(10, 0), (15, 30), (16, 0)]:
            at = datetime.combine(day, time(hour, minute), NY)
            quotes = demo_quotes(manifest, at, n * 25)
            for identifier, q in quotes.items():
                c = manifest.contract_map[identifier]
                rows.append(
                    {
                        **q.model_dump(mode="json"),
                        "timestamp": at,
                        "source": "synthetic",
                        "symbol": c.symbol,
                        "expiry": str(c.expiry),
                        "strike": c.strike,
                        "right": c.right,
                        "kind": c.kind,
                    }
                )
    return pd.DataFrame(rows)


def test_quote_replay_exact_score_and_mandates(manifest):
    result = ReplayEngine(manifest, Variant(policy="unhedged")).run(archive(manifest))
    values = list(result["closes"].values())
    assert result["weekly_score"] == pytest.approx(
        sum(abs(b - a) for a, b in zip(values[:-1], values[1:], strict=True))
    )
    assert result["mandate_compliant"]
    assert result["transactions"] == 0
    assert result["evidence"] == "synthetic_model_test"


def test_missing_close_is_not_replaced_by_intraday_quote(manifest):
    frame = archive(manifest)
    frame = frame[frame.timestamp.dt.hour != 16]
    with pytest.raises(ValueError, match="Closing quote"):
        ReplayEngine(manifest, Variant(policy="unhedged")).run(frame)


def test_cboe_import_preserves_quote_provenance(tmp_path):
    source = tmp_path / "cboe.csv"
    source.write_text(
        "underlying_symbol,quote_datetime,expiration,strike,option_type,bid,ask,bid_size,ask_size,underlying_bid,underlying_ask,implied_volatility\nBA,2026-10-06 10:00:00,2026-10-16,195,C,5.1,5.2,10,12,194.99,195.01,.28\n"
    )
    output = tmp_path / "quotes.parquet"
    assert import_cboe(source, output) == 1
    frame = normalized_quotes(pd.read_parquet(output))
    assert frame.iloc[0].source == "cboe_datashop"
    assert frame.iloc[0].spot == 195
    assert str(frame.timestamp.dt.tz) == "UTC"


def test_history_never_reads_future_data(manifest, snapshot, quotes, now):
    frame = pd.DataFrame(
        {
            "timestamp": [pd.Timestamp(now) + pd.Timedelta(days=1)],
            "symbol": ["BA"],
            "iv": [10],
            "spot": [1000],
        }
    )
    base = ScenarioEngine(manifest).build(snapshot, quotes, now)
    with_future = ScenarioEngine(manifest, frame).build(snapshot, quotes, now)
    assert np.array_equal(base.changes, with_future.changes)


def test_historical_matching_only_uses_day_zero_available_contracts(manifest):
    frame = normalized_quotes(archive(manifest))
    frame = frame[frame.kind == "OPT"]
    matched = match_window(manifest, frame, [manifest.day_zero, *manifest.scoring_sessions], {"BA": 195})
    assert matched.mandatory == manifest.mandatory
    assert all(i not in matched.allowlist for i in matched.mandatory)


def test_quote_archive_preserves_provider_time_and_rejects_frozen_research(tmp_path, manifest, now):
    from datetime import timedelta

    from hedge.data import QuoteRecorder

    q = demo_quotes(manifest, now)[10].model_copy(
        update={"provider": "moomoo", "data_type": "frozen", "observed_at": now - timedelta(minutes=20)}
    )
    recorder = QuoteRecorder(tmp_path)
    recorder.append({10: q}, manifest.contract_map, now)
    recorder.flush()
    frame = pd.read_parquet(next(tmp_path.glob("*.parquet")))
    assert pd.Timestamp(frame.iloc[0].timestamp) == pd.Timestamp(q.observed_at)
    assert pd.Timestamp(frame.iloc[0].recorded_at) == pd.Timestamp(now)
    assert frame.iloc[0].source == "moomoo"
    with pytest.raises(ValueError, match="Frozen/delayed"):
        normalized_quotes(frame)


def test_historical_matching_keeps_official_daily_rate_rule(manifest):
    from decimal import Decimal

    template = manifest.model_copy(
        update={
            "scoring_rule": "actual_daily_cash_rate",
            "scoring_daily_rates": {d: Decimal("0.0001") for d in manifest.scoring_sessions},
        }
    )
    frame = normalized_quotes(archive(template))
    matched = match_window(
        template, frame[frame.kind == "OPT"], [template.day_zero, *template.scoring_sessions], {"BA": 195}
    )
    assert matched.scoring_rule == "actual_daily_cash_rate"
    assert matched.scoring_daily_rates == template.scoring_daily_rates
