"""Finite-action shadow evaluator. No broker, order manager, or solver ownership."""

from datetime import timedelta
from math import ceil, floor, sqrt
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
            "expense_remaining": None,
            "expense_mode": "fixed cap" if settings.daily_expense_budget is not None else "remaining surplus",
            "orders_remaining": max(0, settings.daily_order_budget - stats["orders"]),
            "turnover_limit": None,
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
        if result["current_gap"] is not None:
            result["closing_gap"] = result["current_gap"]
            result["closing_score"] = abs(result["current_gap"])
        return finish("CLOSED", "Scoring close reached; no new cycle")
    cutoff = close - timedelta(minutes=max(settings.entry_cutoff_minutes, m.policy.stock_cutoff_minutes))
    result["entry_cutoff"] = cutoff.isoformat()
    remaining = max(0, (cutoff - now).total_seconds())
    result["seconds_for_new_cycles"] = remaining
    if now >= cutoff:
        result["projected_unremovable_surplus"] = max(0, result["current_gap"] or 0)
        return finish("CLOSED", "New-entry cutoff reached; remaining surplus is retained")
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
        protected_surplus=min(max(0, mean), buffer),
    )
    budget = result["budget"]
    # Remaining surplus already contains every prior expense. Do not subtract ledger
    # expenses from it again. A configured FIXED cap is a separate consumed allowance.
    fixed_remaining = (
        max(0, float(settings.daily_expense_budget) - stats["expense_used"])
        if settings.daily_expense_budget is not None
        else None
    )
    budget.update(
        fixed_expense_remaining=fixed_remaining,
        expense_remaining=min(needed, fixed_remaining) if fixed_remaining is not None else needed,
    )
    if now < close - timedelta(minutes=settings.entry_window_minutes):
        return finish("MONITORING", "Monitoring; outside the near-close research entry window")
    threshold = settings.stop_threshold if active else settings.start_threshold
    if needed <= threshold:
        complete = max(0, mean) <= settings.buffer + settings.stop_threshold
        return finish(
            "COMPLETE" if complete else "MONITORING",
            "Reduction reached the configured tolerance and protected buffer"
            if complete
            else "Forecast uncertainty protects the remaining surplus; continue monitoring",
            active,
        )
    if budget["orders_remaining"] < 2:
        return finish("WAITING", "Order budget exhausted; two order equivalents required per cycle", True)
    if budget["expense_remaining"] <= 0:
        return finish("WAITING", "Configured fixed expense budget exhausted", True)
    if remaining < settings.assumed_cycle_seconds:
        result["projected_unremovable_surplus"] = needed
        return finish("WAITING", "Insufficient time for a complete entry and restoration cycle", True)
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
        # Include every smaller integer slice so the final cycle can fit the remaining
        # allowance. Large candidate ladders must not strand a small removable remainder.
        path_risk = {}
        for size in range(1, max(settings.quantities) + 1):
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
            # Reserve the largest modeled adverse loss for this cycle. A fixed cap
            # consumes gross expenses even when a favorable move offsets the net loss.
            move = (
                scenarios.changes[:, column]
                * size
                * sqrt(min(1, settings.assumed_cycle_seconds / max(1, (close - now).total_seconds())))
            )
            turnover = float(buy_cap + sell_floor) * size + float(np.max(np.abs(move)))
            loss = cost - move
            reserve = max(cost, float(np.max(loss)))
            if reserve > budget["expense_remaining"]:
                rejected[label] = (
                    "Insufficient fixed expense reserve"
                    if fixed_remaining is not None and fixed_remaining < needed
                    else "Cycle loss reserve exceeds the remaining surplus allowance"
                )
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
                # Reserve varies by candidate. Cache the cost-free metrics, then check
                # both linear reserve effects explicitly for this candidate.
                if partial not in path_risk:
                    holdings = {**quantities, identifier: quantities.get(identifier, 0) + partial}
                    path_risk[partial] = gate.validate(snap, packet.quotes, scenarios, holdings, now)
                risk = path_risk[partial]
                reserve_fits = risk.metrics.get(
                    "stress_loss", float("inf")
                ) + reserve <= m.risk.stress_loss * risk.metrics.get("budget", 0) + 0.01 and risk.metrics.get(
                    "excess_liquidity_estimate", 0
                ) - reserve >= m.risk.liquidity_buffer * risk.metrics.get("budget", 0)
                if not risk.passed or not reserve_fits:
                    path_valid = False
                    rejected[label] = "Temporary-inventory risk: " + (
                        "; ".join(risk.reasons)
                        if not risk.passed
                        else "Expense reserve exceeds risk/liquidity headroom"
                    )
                    break
            if not path_valid:
                continue
            modeled_loss = float(scenarios.weights @ loss)
            available_cycles = min(
                floor(remaining / settings.assumed_cycle_seconds), budget["orders_remaining"] // 2
            )
            if fixed_remaining is not None:
                available_cycles = min(available_cycles, floor(fixed_remaining / max(reserve, 1e-9)))
            # Re-evaluate after each cycle. This capacity is a frozen-input expectation,
            # not an authorization to run the full sequence without fresh checks.
            cycles_to_target = ceil(needed / max(modeled_loss, 1e-9))
            cycles = min(available_cycles, cycles_to_target)
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
                    "modeled_loss": modeled_loss,
                    "modeled_cost": cost,
                    "expense_reserve": reserve,
                    "turnover": turnover,
                    "loss_std": float(sqrt(scenarios.weights @ (loss - scenarios.weights @ loss) ** 2)),
                    "overshoot_probability": overshoot,
                    "expected_score_improvement": improvement,
                    "improvement_uncertainty": uncertainty,
                    "expected_score_after": float(scenarios.weights @ np.abs(after)),
                    "path_checked_holdings": size + 1,
                    "modeled_loss_per_second": modeled_loss / settings.assumed_cycle_seconds,
                    "remaining_cycles": cycles,
                    "modeled_capacity": min(needed, cycles * modeled_loss),
                    "time_order_capacity": max(0, available_cycles * modeled_loss),
                    "cycles_to_target": cycles_to_target,
                    "expected_seconds_to_target": cycles_to_target * settings.assumed_cycle_seconds,
                    "completion_feasible": available_cycles >= cycles_to_target,
                    "cycle_seconds_assumption": settings.assumed_cycle_seconds,
                }
            )
    result["rejected_candidates"] = rejected
    if not proposals:
        counts = {}
        for reason in rejected.values():
            counts[reason] = counts.get(reason, 0) + 1
        result["blocking_constraints"] = counts
        result["retry_when_forecast_changes"] = any(
            r in counts
            for r in (
                "Score improvement/uncertainty or overshoot buffer failed",
                "Cycle loss reserve exceeds the remaining surplus allowance",
            )
        ) and not any(
            r.startswith(("Stale/", "Temporary-inventory risk:", "Execution spread")) for r in counts
        )
        result["projected_unremovable_surplus"] = needed
        reason = max(counts, key=counts.get) if counts else "No eligible stock/ETF in the scenario universe"
        return finish("WAITING", "No eligible cycle: " + reason, True)
    chosen = max(
        proposals,
        key=lambda p: (
            p["modeled_capacity"],
            p["expected_score_improvement"],
            -p["expense_reserve"],
            -p["turnover"],
        ),
    )
    result["proposal"] = chosen
    result["capacity"] = {
        "modeled": chosen["modeled_capacity"],
        "cycles": chosen["remaining_cycles"],
        "empirically_supported": None,
        "completion_feasible": chosen["completion_feasible"],
        "expected_seconds_to_target": chosen["expected_seconds_to_target"],
        "time_order_capacity": chosen["time_order_capacity"],
    }
    result["projected_unremovable_surplus"] = max(0, needed - chosen["modeled_capacity"])
    result["pace"] = {
        "required": result["required_loss_per_second"],
        "modeled": chosen["modeled_loss_per_second"],
        "observed": stats.get("observed_loss_per_second"),
    }
    return finish(
        "PROPOSED",
        (
            "Surplus-sized cycle proposed; replan after every completed cycle"
            if chosen["completion_feasible"]
            else "Insufficient modeled time/order capacity for full reduction; best feasible cycle proposed"
        ),
        True,
    )
