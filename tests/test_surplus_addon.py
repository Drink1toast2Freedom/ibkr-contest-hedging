import json
import re
import subprocess
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hedge.config import NY, Manifest
from hedge.models import WorkingOrder
from hedge.service import TradingService
from hedge.webapp import create_app as main_app
from surplus_addon.controller import evaluate
from surplus_addon.ledger import Ledger
from surplus_addon.models import Input, Settings
from surplus_addon.service import Advisor, Service
from surplus_addon.sources import AppSource, DemoSource
from surplus_addon.web import create_app


@pytest.fixture
def demo():
    return DemoSource()


@pytest.fixture
def research(tmp_path):
    ledger = Ledger(tmp_path / "research.sqlite")
    yield ledger
    ledger.close()


def check(demo, ledger, settings=None, packet=None, active=False):
    packet = packet or demo.packet()
    stats = ledger.stats(packet.manifest.active_session(demo.now), packet.snapshot.account, "synthetic")
    return evaluate(settings or Settings(), packet, stats, demo.now, active)


def with_snapshot(packet, **fields):
    snapshot = packet.snapshot.model_copy(update=fields)
    return packet.model_copy(
        update={
            "snapshot": snapshot,
            "snapshot_id": snapshot.version,
            "scenario_snapshot_id": snapshot.version,
        }
    )


def test_finite_action_path_and_identity(demo, research):
    packet = demo.packet()
    decision = check(demo, research, packet=packet)
    assert decision["state"] == "PROPOSED"
    assert decision["execution_enabled"] is False
    proposal = decision["proposal"]
    assert proposal["con_id"] not in packet.manifest.mandatory
    assert proposal["path_checked_holdings"] == proposal["quantity"] + 1
    assert proposal["expense_reserve"] <= 25
    assert proposal["overshoot_probability"] <= 0.01
    assert proposal["expected_score_improvement"] > proposal["improvement_uncertainty"] * 1.645
    assert decision["snapshot_id"] == packet.snapshot.version
    assert decision["config_hash"] == packet.manifest.config_hash
    assert decision["capacity"]["empirically_supported"] is None
    assert proposal["time_in_force"] == "IOC" and proposal["proposal_only"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("execution_busy", True),
        ("target", None),
        ("nav_estimate", None),
        ("scenarios", None),
        ("scenario_revision", -1),
        ("scenario_snapshot_id", "other"),
        ("scenario_at", "stale"),
        ("generated_at", "stale"),
        ("forecast_close", "other_close"),
        ("scenario_at", "future"),
    ],
)
def test_invalid_evidence_clears_proposal(demo, research, field, value):
    packet = demo.packet()
    value = {
        "stale": demo.now - timedelta(seconds=30),
        "future": demo.now + timedelta(seconds=2),
        "other_close": packet.forecast_close + timedelta(days=1),
    }.get(value, value)
    decision = check(demo, research, packet=packet.model_copy(update={field: value}))
    assert decision["state"] == "WAITING" and decision["proposal"] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("connected", False),
        ("reconciled", False),
        ("account_at", "stale"),
        ("account_at", "future"),
        ("positions", ()),
        ("orders", (WorkingOrder(order_id=1, con_id=10, signed_remaining=1),)),
    ],
)
def test_broker_state_and_mandates_block(demo, research, field, value):
    if value == "stale":
        value = demo.now - timedelta(seconds=181)
    elif value == "future":
        value = demo.now + timedelta(seconds=2)
    result = check(demo, research, packet=with_snapshot(demo.packet(), **{field: value}))
    assert result["state"] == "WAITING" and result["proposal"] is None


@pytest.mark.parametrize(
    "time_change,state",
    [("day_zero", "CLOSED"), ("morning", "MONITORING"), ("cutoff", "CLOSED"), ("close", "CLOSED")],
)
def test_session_windows(demo, research, time_change, state):
    m = demo.manifest
    if time_change == "day_zero":
        demo.now = m.session_close(m.day_zero) - timedelta(minutes=5)
    elif time_change == "morning":
        demo.now = m.session_open(m.scoring_sessions[0]) + timedelta(minutes=30)
    elif time_change == "cutoff":
        demo.now = m.session_close(m.scoring_sessions[0]) - timedelta(minutes=2)
    else:
        demo.now = m.session_close(m.scoring_sessions[0])
    result = check(demo, research)
    assert result["state"] == state and result["proposal"] is None


def test_early_close_uses_exchange_calendar(demo, research):
    packet = demo.packet()
    data = demo.manifest.model_dump()
    data.update(day_zero=date(2026, 11, 25), scoring_sessions=(date(2026, 11, 27),))
    data["contracts"] = tuple(
        c.model_copy(update={"expiry": date(2026, 12, 18)}) if c.kind == "OPT" else c
        for c in demo.manifest.contracts
    )
    demo.manifest = Manifest.model_validate(data)
    close = demo.manifest.session_close(date(2026, 11, 27))
    assert close.astimezone(NY).hour == 13
    demo.now = close - timedelta(minutes=2)
    # Calendar gate precedes forecast validation; avoid constructing a mismatched demo engine.
    packet = packet.model_copy(update={"manifest": demo.manifest, "config_hash": demo.manifest.config_hash})
    result = check(demo, research, packet=packet)
    assert result["state"] == "CLOSED"


@pytest.mark.parametrize("gap", [-4000, 0, 15])
def test_deficits_and_tiny_surplus_do_not_trigger(demo, research, gap):
    packet = demo.packet().model_copy(update={"nav_estimate": 1000000 + gap})
    result = check(demo, research, packet=packet)
    assert result["proposal"] is None


def test_approximate_target_increases_buffer(demo, research):
    packet = demo.packet()
    before = check(demo, research, packet=packet)
    after = check(demo, research, packet=packet.model_copy(update={"target_estimated": True}))
    assert after["buffer"] == pytest.approx(before["buffer"] + 25)


def test_hysteresis(demo, research):
    packet = demo.packet()
    # Constant future-value scenarios remove movement/mark uncertainty for this fixture.
    scenarios = packet.scenarios.model_copy(
        update={
            "changes": tuple(tuple(0 for _ in packet.scenarios.ids) for _ in packet.scenarios.weights),
        }
    )
    snapshot = packet.snapshot.model_copy(update={"nav": Decimal("1000030")})
    packet = with_snapshot(packet, nav=snapshot.nav).model_copy(
        update={"nav_estimate": 1000030, "scenarios": scenarios}
    )
    assert check(demo, research, packet=packet)["proposal"] is None
    assert check(demo, research, packet=packet, active=True)["state"] == "PROPOSED"


def test_partial_fill_risk_failure_blocks_every_action(demo, research):
    packet = demo.packet()
    # Existing inventory fits, but even the smallest added stock position cannot fit this limit.
    risk = packet.manifest.risk.model_copy(update={"stock_notional": 0.0001})
    m = packet.manifest.model_copy(update={"risk": risk})
    packet = packet.model_copy(update={"manifest": m, "config_hash": m.config_hash})
    result = check(demo, research, packet=packet)
    assert result["proposal"] is None
    assert any("Temporary-inventory risk" in r for r in result["rejected_candidates"].values())


@pytest.mark.parametrize(
    "settings",
    [
        {"daily_expense_budget": "1"},
        {"daily_turnover_budget": "100"},
        {"symbols": ("UNAPPROVED",)},
    ],
)
def test_expense_and_universe_budgets(demo, research, settings):
    assert check(demo, research, Settings(**settings))["proposal"] is None


def test_overshoot_distribution_blocks(demo, research):
    packet = demo.packet()
    changes = np.array(packet.scenarios.changes)
    changes[0, packet.scenarios.ids.index(101)] -= 100
    packet = packet.model_copy(
        update={
            "scenarios": packet.scenarios.model_copy(
                update={
                    "changes": tuple(map(tuple, changes)),
                }
            )
        }
    )
    result = check(demo, research, packet=packet)
    assert result["proposal"] is None


def test_stale_and_delayed_cycle_quotes(demo, research):
    packet = demo.packet()
    for fields in ({"data_type": "delayed"}, {"observed_at": demo.now - timedelta(seconds=6)}):
        quotes = {**packet.quotes, 10: packet.quotes[10].model_copy(update=fields)}
        result = check(demo, research, packet=packet.model_copy(update={"quotes": quotes}))
        assert result["proposal"] is None


def test_future_greek_observation_blocks(demo, research):
    packet = demo.packet()
    quotes = {
        **packet.quotes,
        101: packet.quotes[101].model_copy(update={"greek_at": demo.now + timedelta(seconds=2)}),
    }
    result = check(demo, research, packet=packet.model_copy(update={"quotes": quotes}))
    assert result["state"] == "WAITING" and result["proposal"] is None


def test_actual_gain_does_not_replenish_budget(demo, research):
    packet = demo.packet()
    p = check(demo, research)["proposal"]
    cycle = demo.simulate(packet, p)
    cycle.update(loss="20", fees="2")
    assert research.cycle(cycle)
    assert not research.cycle(cycle)
    cycle.update(id="gain", loss="-10", execution_expense="2")
    assert research.cycle(cycle)
    stats = research.stats(cycle["session"], cycle["account"], "synthetic")
    assert stats["confirmed_loss"] == 10 and stats["expense_used"] == 22
    assert stats["observed_loss_per_second"] == pytest.approx(10 / 30)
    assert stats["orders"] == 4
    assert research.stats(cycle["session"], cycle["account"], "shadow")["cycles"] == 0
    with pytest.raises(ValueError):
        research.cycle({**cycle, "evidence": "paper"})


def test_completed_loss_is_not_subtracted_twice(demo, research):
    packet = demo.packet()
    before = check(demo, research, packet=packet)
    cycle = demo.simulate(packet, before["proposal"])
    research.cycle(cycle)
    # Holding NAV fixed represents a cost already accounted in this particular anchor.
    after = check(demo, research, packet=packet)
    assert after["predicted_closing_gap"] == before["predicted_closing_gap"]
    assert after["budget"]["expense_remaining"] < before["budget"]["expense_remaining"]


@pytest.mark.parametrize(
    "change",
    [
        {"source_url": "https://example.com"},
        {"source_url": "http://127.0.0.1@evil.test"},
        {"source_url": "http://localhost:8765/?token=x"},
        {"quantities": (101,)},
        {"stop_threshold": 10},
        {"symbols": ("BA", "BA")},
        {"execution_enabled": True},
    ],
)
def test_invalid_configuration_rejected(change):
    with pytest.raises(ValidationError):
        Settings(**change)


@pytest.mark.parametrize(
    "field,value", [("config_hash", "other"), ("snapshot_id", "other"), ("protocol", "v2")]
)
def test_input_identity_validation(demo, field, value):
    data = demo.packet().model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        Input.model_validate(data)


async def test_app_reader_only_gets_one_fixed_endpoint(demo):
    packet = demo.packet()
    requests = []

    def respond(request):
        requests.append((request.method, str(request.url)))
        return httpx.Response(200, json=packet.model_dump(mode="json"))

    source = AppSource(Settings(), httpx.MockTransport(respond))
    assert (await source.fetch()).snapshot_id == packet.snapshot_id
    assert requests == [("GET", "http://127.0.0.1:8765/api/addons/surplus-input")]
    await source.close()


async def test_reader_does_not_follow_redirects():
    source = AppSource(
        Settings(),
        httpx.MockTransport(lambda r: httpx.Response(302, headers={"location": "https://example.com/"})),
    )
    with pytest.raises(httpx.HTTPStatusError):
        await source.fetch()
    await source.close()


async def test_service_feedback_pause_and_isolation(tmp_path):
    service = Service(Settings(), tmp_path / "research.sqlite", demo=True, ai_path=tmp_path / "ai.json")
    first = await service.tick()
    account = first["account"]
    assert first["state"] == "PROPOSED" and not service.advisor.enabled
    after = (await service.control("simulate_cycle"))["decision"]
    assert after["stats"]["cycles"] == 1 and after["current_gap"] < first["current_gap"]
    assert service.packet.snapshot.quantities() == service.packet.manifest.mandatory
    assert after["stats"]["expense_used"] <= 25 and after["stats"]["paper_calibrated"] is False
    await service.control("pause")
    assert service.decision["proposal"] is None
    with pytest.raises(ValueError):
        await service.control("simulate_cycle")
    await service.control("new_demo")
    assert service.decision["account"] != account
    assert service.decision["stats"]["cycles"] == 0
    await service.control("main_feed")
    with pytest.raises(ValueError):
        await service.control("simulate_cycle")
    await service.stop()


async def test_fetch_failure_revokes_old_proposal(tmp_path):
    service = Service(Settings(), tmp_path / "research.sqlite", demo=True, ai_path=tmp_path / "ai.json")
    assert (await service.tick())["state"] == "PROPOSED"

    async def failed():
        raise ConnectionError("Unavailable")

    service.demo.fetch = failed
    assert (await service.tick())["proposal"] is None
    assert service.packet is None
    await service.stop()


def test_api_and_controls(tmp_path):
    service = Service(Settings(), tmp_path / "research.sqlite", demo=True, ai_path=tmp_path / "ai.json")
    with TestClient(create_app(service)) as client:
        page = client.get("/")
        assert "NO BROKER ORDERS" in page.text
        token = re.search('name="control-token" content="([^"]+)"', page.text)[1]
        headers = {"x-control-token": token}
        assert client.post("/api/control", json={"action": "simulate_cycle"}).status_code == 403
        assert client.post("/api/control", json={"action": "paper"}, headers=headers).status_code == 422
        assert (
            client.post(
                "/api/control", json={"action": "simulate_cycle", "quantity": 1000}, headers=headers
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/control",
                json={"action": "simulate_cycle"},
                headers={**headers, "origin": "https://evil.test"},
            ).status_code
            == 403
        )
        result = client.post("/api/control", json={"action": "simulate_cycle"}, headers=headers)
        assert result.status_code == 200 and len(result.json()["cycles"]) == 1
        assert result.json()["execution_enabled"] is False
        assert client.get("/api/export").json()["cycles"][0]["real_orders"] == 0
        assert client.get("/api/state", headers={"host": "evil.test"}).status_code == 400
        assert (
            client.post(
                "/api/ai/settings", json={"model": "arbitrary", "enabled": True}, headers=headers
            ).status_code
            == 409
        )


def test_advisor_numeric_allowlist():
    safe = Advisor.context(
        {
            "snapshot_id": "x",
            "account": "SECRET",
            "api_key": "SECRET",
            "proposal": {"quantity": 1000},
            "needed_reduction": 3,
            "execution_enabled": False,
        }
    )
    assert "SECRET" not in json.dumps(safe) and "proposal" not in safe
    assert safe["needed_reduction"] == 3 and not safe["execution_enabled"]


@pytest.mark.parametrize(
    "profile,extra", [("STANDARD", {}), ("GREEK_NEUTRAL", {}), ("STANDARD", {"orders": [{"quantity": 1000}]})]
)
async def test_addon_advisor_is_bounded_and_never_applied(tmp_path, monkeypatch, profile, extra):
    monkeypatch.setenv("GEMINI_API_KEY", "test-only-key")

    def response(request):
        body = json.loads(request.content)
        safe = json.loads(body["input"])
        assert "PRIVATE" not in body["input"] and body["tools"] == []
        assert safe["execution_enabled"] is False
        recommendation = {"snapshot_id": "x", "profile": profile, "explanation": "Research only", **extra}
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "steps": [
                    {
                        "type": "model_output",
                        "content": [{"type": "text", "text": json.dumps(recommendation)}],
                    }
                ],
            },
        )

    advisor = Advisor(settings_path=tmp_path / "ai.json", transport=httpx.MockTransport(response))
    state = {
        "snapshot_id": "x",
        "account": "PRIVATE",
        "proposal": {"quantity": 1000},
        "execution_enabled": False,
        "needed_reduction": 15,
    }
    if profile != "STANDARD" or extra:
        with pytest.raises(ValueError):
            await advisor.review(state)
    else:
        assert (await advisor.review(state))["advisory_only"]
    assert state["proposal"]["quantity"] == 1000 and not state["execution_enabled"]
    assert await advisor.review(state) is None


async def test_repeated_simulation_respects_all_budgets(tmp_path):
    settings = Settings()
    service = Service(settings, tmp_path / "research.sqlite", demo=True, ai_path=tmp_path / "ai.json")
    await service.tick()
    for _ in range(60):
        if service.decision["state"] != "PROPOSED":
            break
        await service.control("simulate_cycle")
        stats = service.decision["stats"]
        assert stats["expense_used"] <= float(settings.daily_expense_budget) + 1e-7
        assert stats["turnover"] <= float(settings.daily_turnover_budget) + 1e-7
        assert stats["orders"] <= settings.daily_order_budget
    assert service.decision["proposal"] is None
    assert service.packet.snapshot.quantities() == service.packet.manifest.mandatory
    await service.stop()


@pytest.mark.parametrize(
    "field,value",
    [("loss", "NaN"), ("turnover", "-1"), ("seconds", 0), ("fees", "-1"), ("execution_expense", "-2")],
)
def test_invalid_cycle_accounting_rejected(demo, research, field, value):
    cycle = demo.simulate(demo.packet(), check(demo, research)["proposal"])
    cycle[field] = value
    with pytest.raises(ValueError):
        research.cycle(cycle)


def test_no_execution_adapter_import():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import surplus_addon.service,sys; "
            "assert 'hedge.broker' not in sys.modules; "
            "assert 'hedge.execution' not in sys.modules; "
            "assert 'ib_async' not in sys.modules; "
            "assert 'moomoo' not in sys.modules",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


async def test_main_export_is_read_only(demo, store):
    packet = demo.packet()
    service = TradingService(packet.manifest, store, demo=True)
    service.last_snapshot, service.last_quotes = packet.snapshot, packet.quotes
    service.last_scenarios = packet.scenarios.array()
    service.last_scenario_at = packet.scenario_at
    service.last_scenario_snapshot_id = packet.snapshot_id
    service.last_scenario_revision = 0
    service.demo_start = demo.now
    service.ledger.import_close(packet.manifest.day_zero, Decimal("1000000"), "test", packet.manifest.account)
    before = store.intents(packet.manifest.account)
    # No startup: this ASGI request must not connect/reconcile a broker.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main_app(service)), base_url="http://testserver"
    ) as client:
        response = await client.get("/api/addons/surplus-input")
        assert response.status_code == 200
        exported = Input.model_validate(response.json())
    assert exported.snapshot_id == packet.snapshot_id
    assert exported.target == Decimal("1000000")
    assert store.intents(packet.manifest.account) == before


def test_dashboard_javascript_controls():
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("surplus_controls.cjs"))], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
