from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from hedge.config import NY
from hedge.demo import demo_quotes
from hedge.models import OrderRequest
from hedge.practice import PracticeInputs, normalized_option, paper_account, prepare_practice, probe_paper
from hedge.scenarios import ScenarioEngine
from hedge.service import TradingService


def inputs():
    return dict(
        rules_confirmed=True,
        day_zero="2026-10-05",
        scoring_sessions=["2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"],
        expiry="2026-10-16",
        report_source="Organizer IBKR closing NAV report",
    )


def test_practice_calendar_accepts_four_explicit_scored_sessions():
    parsed = PracticeInputs.model_validate(inputs())
    assert parsed.day_zero == date(2026, 10, 5)
    assert len(parsed.scoring_sessions) == 4
    for change in (
        {"rules_confirmed": False},
        {"expiry": "2026-10-07"},
        {"scoring_sessions": ["2026-10-10"]},
        {"account": "U123"},
    ):
        with pytest.raises(ValidationError):
            PracticeInputs.model_validate({**inputs(), **change})


def test_paper_account_must_be_unambiguous():
    assert paper_account(["U_LIVE", "DU_PAPER"]) == "DU_PAPER"
    with pytest.raises(ValueError):
        paper_account(["U_LIVE"])
    with pytest.raises(ValueError):
        paper_account(["DU_A", "DU_B"])
    assert paper_account(["DU_A", "DU_B"], "DU_A") == "DU_A"


class DiscoveryBroker:
    """Intentionally provides no placeOrder/submit/exercise method."""

    def __init__(self):
        self.connected = self.disconnected = False
        self.details = []

    async def connectAsync(self, host, port, **kwargs):
        assert host == "127.0.0.1" and kwargs["readonly"] is True
        self.connected = True

    def managedAccounts(self):
        return ["DU_FAKE"]

    async def reqSecDefOptParamsAsync(self, *args):
        return [
            SimpleNamespace(
                exchange="SMART",
                tradingClass="BA",
                multiplier="100",
                expirations={"20261016", "20261120"},
                strikes={185, 190, 195, 200, 205},
            )
        ]

    async def reqContractDetailsAsync(self, request):
        if request.secType == "STK":
            return [
                SimpleNamespace(
                    contract=SimpleNamespace(conId=10, symbol="BA", secType="STK", currency="USD")
                )
            ]
        request.conId = 100 + len(self.details)
        self.details.append(request)
        return [SimpleNamespace(contract=request, underConId=10)]

    def disconnect(self):
        self.disconnected = True

    async def accountSummaryAsync(self, account):
        return [SimpleNamespace(account=account, tag="NetLiquidation", value="1000000", currency="USD")]

    def positions(self, account):
        return []


async def test_paper_probe_reads_account_summary_cache():
    broker = DiscoveryBroker()
    result = await probe_paper(ib=broker)
    assert result["account_values"][0]["tag"] == "NetLiquidation"
    assert result["readonly"] and broker.disconnected


async def test_discovery_is_readonly_and_builds_exact_mandates(tmp_path):
    path = tmp_path / "inputs.yaml"
    path.write_text(yaml.safe_dump(inputs()))
    broker = DiscoveryBroker()
    manifest = await prepare_practice(path, broker)
    assert manifest.mandatory == {100: 25, 101: 25}
    assert set(manifest.allowlist).isdisjoint(manifest.mandatory)
    assert len(manifest.allowlist) == 13  # 12 hedge options plus stock
    assert not manifest.confirmed
    assert broker.disconnected
    malformed = SimpleNamespace(contract=broker.details[0], underConId=999)
    with pytest.raises(ValueError):
        normalized_option(
            [malformed], PracticeInputs.model_validate(inputs()), 10, date(2026, 10, 16), 195, "C"
        )


async def test_conflicting_rules_never_connect(tmp_path):
    path = tmp_path / "inputs.yaml"
    path.write_text(yaml.safe_dump({**inputs(), "rules_confirmed": False}))
    broker = DiscoveryBroker()
    with pytest.raises(ValidationError):
        await prepare_practice(path, broker)
    assert not broker.connected


async def test_empty_account_day_zero_purchases_all_fifty_and_protects_exact_holdings(manifest, store):
    service = TradingService(manifest, store, demo=True, rehearsal=True)
    now = datetime.combine(manifest.day_zero, datetime.min.time(), NY).replace(hour=10)
    service.now = lambda: now
    broker = service.broker
    await broker.connect()
    quotes = demo_quotes(manifest, now)
    broker.update_quotes(quotes, now)
    assert not broker.positions
    service.last_quotes = quotes
    service.last_snapshot = broker.snapshot()
    service.last_scenarios = ScenarioEngine(manifest).build(broker.snapshot(), quotes, now)
    assert not service.ledger.closes()  # No fictional pre-purchase closing baseline
    await service.control("setup")
    assert service.mode == "SETUP"
    for _ in range(60):
        await service._setup_slice()
        for order_id in list(broker.orders):
            await broker.fill(order_id)
        while not service.events.empty():
            name, event = service.events.get_nowait()
            if name == "execution":
                store.execution(event)
                service.execution.on_execution(event)
            elif name == "order":
                service.execution.on_order(event)
            service.events.task_done()
        service.last_snapshot = broker.snapshot()
        if service.mode == "SHADOW":
            break
    assert {i: broker.positions.get(i, 0) for i in manifest.mandatory} == manifest.mandatory
    option_fills = [r for r in store.fills(manifest.account) if r["con_id"] in manifest.mandatory]
    assert len(option_fills) == 50
    assert all(row["signed_quantity"] == 1 for row in option_fills)
    assert not service.gate.registry.check(broker.positions)
    assert service.mode == "SHADOW" and broker.readonly
    assert not store.intents(manifest.account, True)
    assert len(store.records("setup_complete", manifest.account)) == 1
    assert not service.ledger.closes()
    broker.readonly = False
    request = OrderRequest(
        intent_id="overbuy",
        con_id=101,
        signed_quantity=1,
        limit_price=Decimal(1),
        account=manifest.account,
        snapshot_id=broker.snapshot().version,
        decision_id="forbidden",
    )
    with pytest.raises(ValueError):
        await broker.setup_mandate(request, broker.reserve_order_id(), now)
    wrong = request.model_copy(update={"account": "DU_OTHER"})
    with pytest.raises(ValueError):
        await broker.setup_mandate(wrong, broker.reserve_order_id(), now)


async def test_setup_rejected_after_day_zero_close(manifest, store):
    service = TradingService(manifest, store, demo=True, rehearsal=True)
    now = datetime.combine(manifest.day_zero, datetime.min.time(), NY).replace(hour=23)
    service.now = lambda: now
    await service.broker.connect()
    service.broker.update_quotes(demo_quotes(manifest, now), now)
    service.last_quotes = service.broker.quotes()
    service.last_scenarios = ScenarioEngine(manifest).build(
        service.broker.snapshot(), service.last_quotes, now
    )
    with pytest.raises(ValueError, match="order window"):
        await service.control("setup")
    assert service.mode == "PAUSED" and service.broker.readonly
    assert not store.intents(manifest.account)


async def test_explicit_day_one_purchase_retains_original_scoring_baseline(manifest, store):
    from hedge.config import Manifest

    modified = Manifest.model_validate(
        {**manifest.model_dump(), "mandatory_purchase_session": manifest.scoring_sessions[0]}
    )
    service = TradingService(modified, store, demo=True, rehearsal=True)
    now = datetime.combine(modified.scoring_sessions[0], datetime.min.time(), NY).replace(hour=10)
    service.now = lambda: now
    await service.broker.connect()
    service.broker.update_quotes(demo_quotes(modified, now), now)
    service.last_quotes = service.broker.quotes()
    service.last_scenarios = ScenarioEngine(modified).build(
        service.broker.snapshot(), service.last_quotes, now
    )
    await service.control("setup")
    await service._setup_slice()
    assert service.broker.orders
    assert service.manifest.day_zero == manifest.day_zero
    assert not service.ledger.closes()
    await service.execution.pause()


async def test_opening_purchase_arm_is_cancelled_by_pause(manifest, store):
    from hedge.config import Manifest

    modified = Manifest.model_validate(
        {**manifest.model_dump(), "mandatory_purchase_session": manifest.scoring_sessions[0]}
    )
    service = TradingService(modified, store, demo=True, rehearsal=True)
    await service.broker.connect()
    # Night before purchase session: no live quote requirement for scheduling;
    # quotes/limits are checked independently when the session actually opens.
    service.now = lambda: datetime.combine(modified.day_zero, datetime.min.time(), NY).replace(hour=23)
    await service.control("arm_setup")
    assert service.mode == "WAITING_SETUP" and service.broker.readonly
    assert service.setup_armed and not service.broker.orders
    await service.control("pause")
    assert not service.setup_armed and not service.auto_hedge_after_setup
    assert service.mode == "PAUSED"
