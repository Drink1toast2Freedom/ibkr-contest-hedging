from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from hedge.models import Contract
from hedge.optimizer import HedgeOptimizer
from hedge.scenarios import ScenarioEngine
from hedge.universe import screen_options
from hedge.universe_discovery import qualify_stock_universe


class DiscoveryBroker:
    def __init__(self):
        self.disconnected = False
        self.connection = None

    async def connectAsync(self, *args, **kwargs):
        self.connection = kwargs

    def managedAccounts(self):
        return ["DU_TEST"]

    async def reqContractDetailsAsync(self, native):
        return [
            SimpleNamespace(
                contract=SimpleNamespace(symbol=native.symbol, secType="STK", currency="USD", conId=200)
            )
        ]

    def disconnect(self):
        self.disconnected = True


async def test_stock_discovery_is_readonly_and_preserves_mandates(manifest):
    broker = DiscoveryBroker()
    expanded = await qualify_stock_universe(manifest, ["SPY", "BA"], broker)
    assert broker.connection["readonly"] and broker.disconnected
    assert expanded.mandatory == manifest.mandatory
    assert expanded.contract_map[200].symbol == "SPY"
    assert 200 in expanded.allowlist
    with pytest.raises(ValueError):
        await qualify_stock_universe(manifest, ["../../bad"], broker)


def joint_history(path, now, future=False):
    days = pd.bdate_range(end=pd.Timestamp(now.date()) - pd.Timedelta(days=1), periods=70)
    rng = np.random.default_rng(31)
    market = rng.normal(0, 0.01, len(days))
    frames = []
    for symbol, beta in [("BA", 1.4), ("SPY", 1.0)]:
        prices = 100 * np.exp(np.cumsum(beta * market + rng.normal(0, 0.002, len(days))))
        frames.extend(
            {"date": str(d.date()), "symbol": symbol, "close": p} for d, p in zip(days, prices, strict=True)
        )
        if future:
            frames.append({"date": str(now.date()), "symbol": symbol, "close": 999999})
    pd.DataFrame(frames).to_csv(path, index=False)


def test_joint_daily_scenarios_deterministic_no_future_or_missing_history(
    manifest, snapshot, quotes, now, tmp_path
):
    path = tmp_path / "stocks.csv"
    joint_history(path, now)
    spy = Contract(con_id=200, symbol="SPY", kind="STK")
    m = manifest.model_copy(
        update={
            "contracts": (*manifest.contracts, spy),
            "allowlist": (*manifest.allowlist, 200),
            "daily_stock_history": str(path),
        }
    )
    quotes = {
        **quotes,
        200: quotes[10].model_copy(
            update={"con_id": 200, "spot": 450, "bid": 449.99, "ask": 450.01, "shortable_shares": None}
        ),
    }
    ids = screen_options(m, quotes, snapshot.quantities())
    assert 200 in ids
    engine = ScenarioEngine(m)
    first = engine.build(snapshot, quotes, now, ids)
    assert first.identifier == engine.build(snapshot, quotes, now, ids).identifier
    assert "shrunk covariance" in first.source
    joint_history(path, now, future=True)
    assert ScenarioEngine(m).build(snapshot, quotes, now, ids).identifier == first.identifier
    target = HedgeOptimizer(m).solve(snapshot, quotes, first, m.initial_nav)
    assert target.status == "OPTIMAL"
    assert target.quantities.get(200, 0) >= 0  # no invented stock borrow
    assert all(target.quantities[i] == q for i, q in m.mandatory.items())
    frame = pd.read_csv(path)
    frame[frame.symbol == "BA"].to_csv(path, index=False)
    with pytest.raises(ValueError, match="Missing joint stock history"):
        ScenarioEngine(m).build(snapshot, quotes, now, ids)


def test_cross_underlying_activation_requires_joint_history(manifest, snapshot, quotes, now):
    spy = Contract(con_id=200, symbol="SPY", kind="STK")
    m = manifest.model_copy(
        update={"contracts": (*manifest.contracts, spy), "allowlist": (*manifest.allowlist, 200)}
    )
    quotes = {**quotes, 200: quotes[10].model_copy(update={"con_id": 200})}
    with pytest.raises(ValueError, match="Joint historical coverage required"):
        ScenarioEngine(m).build(snapshot, quotes, now)
