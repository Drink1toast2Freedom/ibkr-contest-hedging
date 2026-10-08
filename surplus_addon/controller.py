"""Finite-action shadow evaluator. No broker, order manager, or solver ownership."""

from datetime import timedelta
from math import floor, sqrt
from time import perf_counter

import numpy as np

from hedge.cashflows import cash_carry
from hedge.models import digest
from hedge.optimizer import execution_cost
from hedge.order_policy import rounded_price, spread_error
from hedge.risk import RiskGate


def quantile(values, weights, probability):
    order = np.argsort(values)
    return float(values[order][np.searchsorted(np.cumsum(weights[order]), probability, side="left")])


def evaluate(settings, packet, stats, now, active=False):
    started = perf_counter()
    m, snap = packet.manifest, packet.snapshot
    day = m.active_session(now)
    close = m.session_close(day) if day is not None else None
    evidence = "synthetic" if snap.source == "synthetic" else "shadow"
    result = {
        "at": now.isoformat(),
        "state": "WAITING",
        "reason": "",
        "evidence": evidence,
        "execution_enabled": False,
        "account": snap.account,
        "session": str(day) if day else None,
        "snapshot_id": packet.snapshot_id,
        "config_hash": packet.config_hash,
        "settings_hash": digest(settings),
        "input_hash": digest(packet),
        "scenario_id": packet.scenarios.identifier if packet.scenarios else None,
        "scenario_source": packet.scenarios.source if packet.scenarios else None,
        "target_estimated": packet.target_estimated,
        "main_mode": packet.main_mode,
        "close": close.isoformat() if close else None,
        "proposal": None,
        "stats": stats,
        "nav": packet.nav_estimate,
        "reported_nav": float(snap.nav),
        "target": float(packet.target) if packet.target is not None else None,
        "current_gap": (
            packet.nav_estimate - float(packet.target)
            if packet.nav_estimate is not None and packet.target is not None
            else None
        ),
        "budget": {
            "expense_remaining": max(0, float(settings.daily_expense_budget) - stats["expense_used"]),
            "orders_remaining": max(0, settings.daily_order_budget - stats["orders"]),
            "turnover_remaining": max(0, float(settings.daily_turnover_budget) - stats["turnover"]),
        },
        "completion_calibrated": False,
        "capacity": {"modeled": 0.0, "empirically_supported": None, "cycles": 0},
        "assumptions": [
            "Completed stock round trips; spread, slippage and commissions are model assumptions.",
            "Cycle price movement uses square-root time scaling, without a directional forecast.",
            "No paper fill/fee calibration or guaranteed reduction capacity exists.",
        ],
    }

    def finish(state, reason, engaged=False):
        result.update(
            state=state,
            reason=reason,
            engaged=engaged,
            compute_ms=round((perf_counter() - started) * 1000, 3),
        )
        return result

    if day is None:
        return finish("CLOSED", "Outside the configured contest scoring sessions")
    if day == m.day_zero:
        return finish("CLOSED", "Day 0 is unscored; surplus reduction is disabled")
    if now >= close:
        return finish("CLOSED", "Scoring close reached; no new cycle")
    cutoff = close - timedelta(minutes=max(settings.entry_cutoff_minutes, m.policy.stock_cutoff_minutes))
    result["entry_cutoff"] = cutoff.isoformat()
    remaining = max(0, (cutoff - now).total_seconds())
    result["seconds_for_new_cycles"] = remaining
    if now >= cutoff:
        return finish("CLOSED", "New-entry cutoff reached")
    if packet.target is None or packet.nav_estimate is None:
        return finish("WAITING", "Previous closing NAV or approved cash benchmark target unavailable")
    if not 0 <= (now - packet.generated_at).total_seconds() <= 10:
        return finish("WAITING", "Stale/future input export")
    if snap.account_at > now + timedelta(seconds=1):
        return finish("WAITING", "Future account-value observation")
    if (
        packet.scenarios is None
        or packet.scenario_at is None
        or not 0 <= (now - packet.scenario_at).total_seconds() <= settings.scenario_age_seconds
    ):
        return finish("WAITING", "Missing/stale/future scenario forecast")
    if (
        packet.scenario_snapshot_id != snap.version
        or packet.scenario_revision != packet.broker_revision
        or packet.forecast_close != close
    ):
        return finish("WAITING", "Scenario provenance changed; wait for a consistent current-close forecast")
    if packet.execution_busy or snap.orders:
        return finish("WAITING", "Main execution or working orders have priority")
    scenarios, quantities = packet.scenarios.array(), snap.quantities()
    if not set(scenarios.ids).issubset(packet.quotes):
        return finish("WAITING", "Incomplete quote coverage for the exported scenarios")
    if any(
        packet.quotes[i].greek_at is not None and packet.quotes[i].greek_at > now + timedelta(seconds=1)
        for i in quantities
        if i in packet.quotes
    ):
        return finish("WAITING", "Future Greek observation; wait for consistent timestamps")
    gate = RiskGate(m, demo=snap.source == "synthetic")
    if packet.baseline_nav is not None:
        gate.engine.baseline_nav = float(packet.baseline_nav)
    original = gate.validate(snap, packet.quotes, scenarios, quantities, now)
    result["risk"] = original.model_dump(mode="json")
    if not original.passed:
        return finish("WAITING", "Risk correction takes priority: " + "; ".join(original.reasons))
    vector = np.array([quantities.get(i, 0) for i in scenarios.ids])
    short = sum(
        max(0, -quantities.get(i, 0)) * packet.quotes[i].spot
        for i in scenarios.ids
        if m.contract_map[i].kind == "STK"
    )
    carry = cash_carry(m, float(snap.cash), short, scenarios.calendar_days, float(snap.nav))
    # Prior cycle losses, fees and accrued interest are ALREADY in this NAV anchor.
    z = packet.nav_estimate - float(packet.target) + scenarios.changes @ vector + carry
    mean = float(scenarios.weights @ z)
    lower = quantile(z, scenarios.weights, 0.01)
    buffer = max(settings.buffer, abs(packet.nav_estimate - float(snap.nav)), mean - lower)
    if packet.target_estimated:
        buffer += settings.target_estimate_buffer
    needed = max(0, mean - buffer)
    result.update(
        predicted_closing_gap=mean,
        lower_closing_gap=lower,
        buffer=buffer,
        needed_reduction=needed,
        required_loss_per_second=needed / remaining if remaining else None,
        current_modeled_score=float(scenarios.weights @ np.abs(z)),
    )
    if now < close - timedelta(minutes=settings.entry_window_minutes):
        return finish("MONITORING", "Monitoring; outside the near-close research entry window")
    threshold = settings.stop_threshold if active else settings.start_threshold
    if needed <= threshold:
        return finish("MONITORING", "No surplus above the movement buffer and hysteresis threshold")
    budget = result["budget"]
    if (
        budget["orders_remaining"] < 2
        or budget["expense_remaining"] <= 0
        or budget["turnover_remaining"] <= 0
    ):
        return finish("WAITING", "Research daily budget exhausted", True)
    proposals, rejected = [], {}
    for column, identifier in enumerate(scenarios.ids):
        c, q = m.contract_map[identifier], packet.quotes[identifier]
        if (
            c.kind != "STK"
            or c.symbol not in settings.symbols
            or identifier not in m.allowlist
            or identifier in m.mandatory
        ):
            continue
        spread_issue = spread_error(m, c, q)
        for size in sorted(set(settings.quantities)):
            label = f"{c.symbol}:{size}"
            if (
                size > min(q.ask_size, q.bid_size, m.execution.stock_slice)
                or size * q.mid < m.execution.minimum_stock_notional
            ):
                rejected[label] = "Outside displayed-size, slice or minimum-notional limits"
                continue
            if spread_issue:
                rejected[label] = spread_issue
                continue
            if (
                q.data_type != ("synthetic" if gate.demo else "live")
                or not 0 <= (now - q.observed_at).total_seconds() <= m.risk.stock_quote_age
            ):
                rejected[label] = "Stale/future/non-live cycle quote"
                continue
            cost = execution_cost(m, packet.quotes, (identifier,), (size,)) + execution_cost(
                m, packet.quotes, (identifier,), (-size,)
            )
            slip = (q.ask - q.bid) * m.policy.slippage_spread_fraction
            buy_cap = rounded_price(q.ask + slip, 0.01, -1)
            sell_floor = rounded_price(q.bid - slip, 0.01, 1)
            if sell_floor <= 0:
                rejected[label] = "Nonpositive exit collar"
                continue
            # Tick rounding must not disappear from the expense projection.
            fee = max(
                m.policy.minimum_commission,
                m.policy.commission_share * min(m.execution.stock_slice, q.ask_size),
            )
            fee += max(
                m.policy.minimum_commission,
                m.policy.commission_share * min(m.execution.stock_slice, q.bid_size),
            )
            cost = max(cost, float(buy_cap - sell_floor) * size + fee)
            # Conservative expense and risk reserve. Favorable outcomes never refill this budget.
            move = (
                scenarios.changes[:, column]
                * size
                * sqrt(min(1, settings.assumed_cycle_seconds / max(1, (close - now).total_seconds())))
            )
            turnover = float(buy_cap + sell_floor) * size + float(np.max(np.abs(move)))
            loss = cost - move
            reserve = max(2 * cost, float(np.max(loss)))
            if reserve > budget["expense_remaining"] or turnover > budget["turnover_remaining"]:
                rejected[label] = "Insufficient expense/turnover reserve"
                continue
            after = z - loss
            overshoot = float(scenarios.weights @ (after < 0))
            improvements = np.abs(z) - np.abs(after)
            improvement = float(scenarios.weights @ improvements)
            # Weighted effective sample size; uncertainty is not execution calibration.
            uncertainty = float(
                sqrt(scenarios.weights @ (improvements - improvement) ** 2 * np.sum(scenarios.weights**2))
            )
            if (
                overshoot > settings.maximum_overshoot_probability
                or quantile(after, scenarios.weights, 0.01) < settings.buffer
                or improvement
                <= max(settings.minimum_improvement, m.policy.uncertainty_multiplier * uncertainty)
            ):
                rejected[label] = "Score improvement/uncertainty or overshoot buffer failed"
                continue
            # Every entry/exit partial-fill holding is checked, including zero. No corrective exception.
            path_valid = True
            for partial in range(size + 1):
                holdings = {**quantities, identifier: quantities.get(identifier, 0) + partial}
                risk = gate.validate(snap, packet.quotes, scenarios, holdings, now, costs=reserve)
                if not risk.passed:
                    path_valid = False
                    rejected[label] = "Temporary-inventory risk: " + "; ".join(risk.reasons)
                    break
            if not path_valid:
                continue
            cycles = max(
                0,
                min(
                    floor(remaining / settings.assumed_cycle_seconds),
                    budget["orders_remaining"] // 2,
                    floor(budget["expense_remaining"] / max(reserve, 1e-9)),
                    floor(budget["turnover_remaining"] / max(turnover, 1e-9)),
                ),
            )
            if not cycles:
                rejected[label] = "Insufficient modeled time or budget for a complete cycle"
                continue
            proposals.append(
                {
                    "con_id": identifier,
                    "symbol": c.symbol,
                    "quantity": size,
                    "order_type": "LMT",
                    "time_in_force": "IOC",
                    "proposal_only": True,
                    "buy_cap": str(buy_cap),
                    "sell_floor": str(sell_floor),
                    "original_quantity": quantities.get(identifier, 0),
                    "modeled_loss": float(scenarios.weights @ loss),
                    "modeled_cost": cost,
                    "expense_reserve": reserve,
                    "turnover": turnover,
                    "loss_std": float(sqrt(scenarios.weights @ (loss - scenarios.weights @ loss) ** 2)),
                    "overshoot_probability": overshoot,
                    "expected_score_improvement": improvement,
                    "improvement_uncertainty": uncertainty,
                    "expected_score_after": float(scenarios.weights @ np.abs(after)),
                    "path_checked_holdings": size + 1,
                    "modeled_loss_per_second": cost / settings.assumed_cycle_seconds,
                    "remaining_cycles": cycles,
                    "modeled_capacity": cycles * cost,
                    "cycle_seconds_assumption": settings.assumed_cycle_seconds,
                }
            )
    result["rejected_candidates"] = rejected
    if not proposals:
        return finish("WAITING", "No bounded cycle passes the score, data, budget and path checks", True)
    chosen = max(proposals, key=lambda p: (p["expected_score_improvement"], -p["turnover"]))
    result["proposal"] = chosen
    result["capacity"] = {
        "modeled": chosen["modeled_capacity"],
        "cycles": chosen["remaining_cycles"],
        "empirically_supported": None,
    }
    result["projected_unremovable_surplus"] = max(0, needed - chosen["modeled_capacity"])
    return finish(
        "PROPOSED", "Shadow proposal only; completed-cycle fills and fees remain uncalibrated", True
    )
