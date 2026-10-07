import asyncio
import os
import re
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from hedge.broker import IBKRBroker, SimBroker
from hedge.demo import demo_manifest
from hedge.execution import ExecutionManager
from hedge.models import Execution, OrderRequest, Target
from hedge.risk import RiskGate
from hedge.scenarios import ScenarioEngine
from hedge.service import TradingService
from hedge.storage import Store
from hedge.supervisor import Recommendation
from hedge.webapp import create_app


def request(manifest, snapshot):
    return OrderRequest(
        intent_id="test-intent",
        con_id=10,
        signed_quantity=100,
        limit_price=Decimal(195),
        account=manifest.account,
        snapshot_id=snapshot.version,
        decision_id="test-decision",
    )


def test_execution_deduplication_and_corrections(store, manifest, now):
    e = Execution(
        exec_id="abc.01",
        order_id=1,
        account=manifest.account,
        con_id=10,
        signed_quantity=10,
        price=Decimal("195.01"),
        at=now,
    )
    assert store.execution(e)
    assert not store.execution(e)
    correction = e.model_copy(
        update={"exec_id": "abc.02", "price": Decimal("195.02"), "correction_of": "abc.01"}
    )
    assert store.execution(correction)
    store.fee(manifest.account, "abc.02", Decimal("1.20"))
    assert len(store.fills(manifest.account)) == 2
    assert len(store.effective_fills(manifest.account)) == 1
    assert store.fills(manifest.account)[0]["at"].hour == 14  # persisted UTC, displayed ET


def test_fills_finish_intent_even_if_order_status_missing(store, manifest, snapshot, now):
    manager = ExecutionManager(manifest, store, None, None)
    r = request(manifest, snapshot)
    store.create_intent(r)
    store.transition(r.intent_id, "VALIDATED")
    store.transition(r.intent_id, "SUBMITTING", 1)
    fill = Execution(
        exec_id="complete.01",
        order_id=1,
        order_ref="hedge:" + r.intent_id,
        account=manifest.account,
        con_id=10,
        signed_quantity=100,
        price=Decimal(195),
        at=now,
    )
    store.execution(fill)
    manager.on_execution(fill)
    assert store.intents(manifest.account)[0]["state"] == "FILLED"


def test_late_order_status_cannot_reopen_fill(store, manifest, snapshot):
    manager = ExecutionManager(manifest, store, None, None)
    r = request(manifest, snapshot)
    store.create_intent(r)
    store.transition(r.intent_id, "VALIDATED")
    store.transition(r.intent_id, "SUBMITTING", 1)
    manager.on_order({"order_ref": "hedge:" + r.intent_id, "status": "Filled", "order_id": 1})
    manager.on_order({"order_ref": "hedge:" + r.intent_id, "status": "Submitted", "order_id": 1, "filled": 0})
    assert store.intents(manifest.account)[0]["state"] == "FILLED"


async def test_crash_after_submission_stays_unknown(store, manifest, snapshot):
    manager = ExecutionManager(manifest, store, None, None)
    r = request(manifest, snapshot)
    store.create_intent(r)
    store.transition(r.intent_id, "VALIDATED")
    store.transition(r.intent_id, "SUBMITTING", 1)
    await manager.reconcile(snapshot)
    assert manager.unresolved()
    assert manager.paused
    assert store.intents(manifest.account)[0]["state"] == "UNKNOWN"


async def test_crash_before_transmission_does_not_resend(store, manifest, snapshot):
    manager = ExecutionManager(manifest, store, None, None)
    r = request(manifest, snapshot)
    store.create_intent(r)
    store.transition(r.intent_id, "VALIDATED")
    await manager.reconcile(snapshot)
    assert store.intents(manifest.account)[0]["state"] == "REJECTED"


async def test_partial_fill_cancel_race(store, manifest, snapshot, quotes, now):
    queue = asyncio.Queue()
    broker = SimBroker(manifest, queue, now)
    await broker.connect()
    synthetic = {i: q.model_copy(update={"data_type": "synthetic"}) for i, q in quotes.items()}
    broker.update_quotes(synthetic, now)
    broker.readonly = False
    manager = ExecutionManager(manifest, store, broker, RiskGate(manifest, demo=True))
    r = request(manifest, snapshot)
    store.create_intent(r)
    store.transition(r.intent_id, "VALIDATED")
    store.transition(r.intent_id, "SUBMITTING", 1)
    await broker.submit(r, 1)
    manager.on_order(queue.get_nowait()[1])
    await broker.fill(1, 40)
    while not queue.empty():
        name, value = queue.get_nowait()
        if name == "execution":
            store.execution(value)
        if name == "order":
            manager.on_order(value)
    assert store.intents(manifest.account)[0]["state"] == "PARTIALLY_FILLED"
    await manager.pause()
    assert not manager.pause_complete()
    manager.on_order(queue.get_nowait()[1])
    assert manager.pause_complete()
    assert broker.snapshot().quantities()[10] == 40
    assert broker.snapshot().quantities()[101] == 25


async def test_submission_failure_never_retries(store, manifest, quotes, now):
    queue = asyncio.Queue()
    broker = SimBroker(manifest, queue, now)
    await broker.connect()
    broker.update_quotes({i: q.model_copy(update={"data_type": "synthetic"}) for i, q in quotes.items()}, now)
    broker.readonly = False
    snap = broker.snapshot()
    sc = ScenarioEngine(manifest).build(snap, broker.quotes(), now)
    manager = ExecutionManager(manifest, store, broker, RiskGate(manifest, demo=True))
    manager.paused = False

    async def fail(*args):
        raise ConnectionError("Disconnected after write")

    broker.submit = fail
    target = Target(
        quantities={**snap.quantities(), 10: -10},
        expected_score=1,
        current_score=100,
        uncertainty=0,
        status="OPTIMAL",
        snapshot_id=snap.version,
        scenario_id=sc.identifier,
        config_hash=manifest.config_hash,
    )
    result = await manager.submit_slice(target, "decision", snap, broker.quotes(), sc, now)
    assert not result["submitted"]
    assert manager.unresolved()
    second = await manager.submit_slice(target, "decision", snap, broker.quotes(), sc, now)
    assert not second["submitted"]
    assert len(store.intents(manifest.account)) == 1


async def test_ibkr_boundary_rejects_live_and_mandatory_orders(manifest, snapshot):
    broker = IBKRBroker(manifest, asyncio.Queue())
    with pytest.raises(ValueError):
        await broker.submit(request(manifest, snapshot), 1)


async def test_own_position_update_preserves_plan_but_manual_change_invalidates(store, manifest, snapshot):
    service = TradingService(manifest, store, demo=True)
    service.last_snapshot = snapshot
    service.pending_target = Target(
        quantities={**snapshot.quantities(), 10: -100},
        expected_score=1,
        current_score=200,
        uncertainty=0,
        status="OPTIMAL",
        snapshot_id=snapshot.version,
        scenario_id="test",
        config_hash=manifest.config_hash,
    )
    service.execution.owned_inventory_ranges[10] = (-100, 0)
    consumer = asyncio.create_task(service._event_loop())
    try:
        await service.events.put(("position", {"con_id": 10, "quantity": "-50"}))
        await service.events.join()
        assert service.pending_target is not None
        await service.events.put(("position", {"con_id": 10, "quantity": "-200"}))
        await service.events.join()
        assert service.pending_target is None
        assert service.force_optimize
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)


async def test_paper_activation_can_correct_existing_delta_breach(store, manifest, quotes, now):
    service = TradingService(manifest, store, demo=True)
    await service.broker.connect()
    service.broker.update_quotes(
        {i: q.model_copy(update={"data_type": "synthetic"}) for i, q in quotes.items()}, now
    )
    service.broker.positions[10] = 1000
    snapshot = service.broker.snapshot()
    service.last_snapshot = snapshot
    service.last_quotes = service.broker.quotes()
    service.last_scenarios = ScenarioEngine(manifest).build(snapshot, service.last_quotes, now)
    service.ledger.import_close(manifest.day_zero, manifest.initial_nav, "fixture", manifest.account)
    service.last_target = Target(
        quantities=manifest.mandatory,
        expected_score=1,
        current_score=200,
        uncertainty=0,
        status="OPTIMAL",
        snapshot_id=snapshot.version,
        scenario_id=service.last_scenarios.identifier,
        config_hash=manifest.config_hash,
    )

    async def reconcile():
        return snapshot

    service.reconcile = reconcile
    result = await service.control("paper")
    assert result["mode"] == "PAPER"
    assert not service.execution.paused


def test_ai_cannot_invent_trades_or_limits():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Recommendation.model_validate(
            {"snapshot_id": "x", "profile": "ARBITRARY", "explanation": "trade", "positions": {10: 100}}
        )


def test_dashboard_readonly_gets_and_csrf_protected_controls(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    service = TradingService(demo_manifest(), Store(f"sqlite:///{tmp_path}/web.db"), demo=True)
    with TestClient(create_app(service)) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "Trades &" not in page.text or "trade" in page.text.lower()
        token = re.search(r'name="control-token" content="([^"]+)"', page.text).group(1)
        assert client.post("/api/control", json={"action": "paper"}).status_code == 403
        response = client.post("/api/control", json={"action": "pause"}, headers={"X-Control-Token": token})
        assert response.status_code == 200
        assert response.json()["demo"]
        assert response.json()["mode"] == "PAUSED"
        cross = client.post(
            "/api/control",
            json={"action": "shadow"},
            headers={"X-Control-Token": token, "Origin": "https://evil.example"},
        )
        assert cross.status_code == 403
        assert client.get("/api/export/scores").status_code == 200
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]


@pytest.mark.skipif(not os.environ.get("TEST_POSTGRES_URL"), reason="PostgreSQL integration runs in CI")
def test_postgres_exact_decimal_ledger(manifest):
    from hedge.scoring import ScoreLedger

    store = Store(os.environ["TEST_POSTGRES_URL"])
    store.initialize()
    ledger = ScoreLedger(store, manifest.model_copy(update={"name": "PostgreSQL fixture"}))
    ledger.import_close(manifest.day_zero, Decimal("1000000.12345678"), "precision", manifest.account)
    assert ledger.closes()[manifest.day_zero] == Decimal("1000000.12345678")
    store.close()


def test_dashboard_controls_recover_stale_tokens_and_show_rejections():
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the dashboard JavaScript regression test")
    result = subprocess.run(
        [node, "tests/dashboard_controls.cjs"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
