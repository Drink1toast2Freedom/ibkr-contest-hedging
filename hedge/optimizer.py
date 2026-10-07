from __future__ import annotations

from time import monotonic

import cvxpy as cp
import numpy as np

from .cashflows import cash_carry
from .config import NY
from .models import Target


def execution_cost(manifest, quotes, identifiers, delta):
    """Fixed, executable slice model; no freely adjustable cost variables."""
    cost = 0.0
    for i, d in zip(identifiers, delta, strict=True):
        if not d:
            continue
        c, q = manifest.contract_map[i], quotes[i]
        available = q.ask_size if d > 0 else q.bid_size
        size = max(1, min(1 if c.kind == "OPT" else 100, available))
        rate = manifest.policy.commission_option if c.kind == "OPT" else manifest.policy.commission_share
        # All full slices have the same modeled fee; final partial slice is
        # conservatively charged a full slice. Actual fees always come from IBKR.
        fee = max(manifest.policy.minimum_commission, rate * size)
        spread = (q.ask - q.bid) * (0.5 + manifest.policy.slippage_spread_fraction) * c.multiplier
        cost += abs(d) * spread + np.ceil(abs(d) / size) * fee
    return float(cost)


class HedgeOptimizer:
    def __init__(self, manifest):
        self.manifest = manifest
        self.baseline_nav = float(manifest.initial_nav)

    def evaluate_target(self, snapshot, quotes, scenarios, target, previous_nav, nav_estimate):
        """Revalidate a remaining execution plan against fresh marks and paid costs."""
        m = self.manifest
        actual = snapshot.quantities()
        ids = scenarios.ids
        current = np.array([actual.get(i, 0) for i in ids])
        desired = np.array([target.quantities.get(i, 0) for i in ids])
        delta = desired - current
        costs = execution_cost(m, quotes, ids, delta)
        days = scenarios.calendar_days

        def carry(quantities, cash):
            borrow = sum(
                max(0, -amount) * quotes[i].spot
                for i, amount in zip(ids, quantities, strict=True)
                if m.contract_map[i].kind == "STK"
            )
            return cash_carry(m, cash, borrow, days, float(snapshot.nav))

        cash = float(snapshot.cash)
        planned_cash = (
            cash
            - sum(
                amount * quotes[i].mid * m.contract_map[i].multiplier
                for i, amount in zip(ids, delta, strict=True)
            )
            - costs
        )
        unscored_setup = snapshot.at.astimezone(NY).date() == m.day_zero
        error = 0 if unscored_setup else nav_estimate - float(previous_nav)
        old = error + scenarios.changes @ current + carry(current, cash)
        new = (
            error
            + scenarios.changes @ desired
            + carry(desired, planned_cash)
            - (0 if unscored_setup else costs)
        )
        improvements = np.abs(old) - np.abs(new)
        uncertainty = (
            float(np.std(improvements, ddof=1) / np.sqrt(len(improvements))) if len(improvements) > 1 else 0
        )
        return target.model_copy(
            update={
                "current_score": float(scenarios.weights @ np.abs(old)),
                "expected_score": float(scenarios.weights @ np.abs(new)),
                "estimated_cost": costs,
                "uncertainty": uncertainty,
                "snapshot_id": snapshot.version,
                "scenario_id": scenarios.identifier,
            }
        )

    def solve(
        self, snapshot, quotes, scenarios, previous_nav, nav_estimate=None, stock_only=False, strategy=None
    ):
        m = self.manifest
        strategy = strategy or m.strategy
        actual = snapshot.quantities()
        ids = scenarios.ids
        budget = min(self.baseline_nav, float(snapshot.nav))
        if budget <= 0:
            raise ValueError("Nonpositive NAV")
        quantities = np.array([actual.get(i, 0) for i in ids], dtype=float)
        unscored_setup = snapshot.at.astimezone(NY).date() == m.day_zero
        error = (
            0
            if unscored_setup
            else (nav_estimate if nav_estimate is not None else float(snapshot.nav)) - float(previous_nav)
        )
        candidates = [
            i for i in ids if i in m.allowlist and (not stock_only or m.contract_map[i].kind == "STK")
        ]
        allowed_type = "synthetic" if snapshot.source == "synthetic" else "live"
        candidates = [
            i
            for i in candidates
            if quotes[i].data_type == allowed_type
            and -1
            <= (snapshot.at - quotes[i].observed_at).total_seconds()
            <= (m.risk.option_quote_age if m.contract_map[i].kind == "OPT" else m.risk.stock_quote_age)
            and (
                m.contract_map[i].kind == "STK"
                or (
                    quotes[i].iv is not None
                    and quotes[i].greek_at is not None
                    and -1 <= (snapshot.at - quotes[i].greek_at).total_seconds() <= m.risk.greek_age
                )
            )
        ]
        frozen = [i for i in ids if i not in candidates]
        if not candidates:
            return self._unchanged(snapshot, scenarios, error, quotes, "NO_CANDIDATES")
        idx = [ids.index(i) for i in candidates]
        current = quantities[idx]
        n = len(candidates)
        bounds = np.array(
            [
                m.risk.contracts_per_option
                if m.contract_map[i].kind == "OPT"
                else max(1, int(m.risk.stock_notional * budget / quotes[i].spot))
                for i in candidates
            ]
        )
        trade_bounds = bounds + np.abs(current)
        hpos, hneg = cp.Variable(n, integer=True), cp.Variable(n, integer=True)
        sign, selected = cp.Variable(n, boolean=True), cp.Variable(n, boolean=True)
        h, ah = hpos - hneg, hpos + hneg
        buy, sell = cp.Variable(n, integer=True), cp.Variable(n, integer=True)
        buying, selling = cp.Variable(n, boolean=True), cp.Variable(n, boolean=True)
        buy_count, sell_count = cp.Variable(n, integer=True), cp.Variable(n, integer=True)
        constraints = [
            hpos >= 0,
            hneg >= 0,
            hpos <= cp.multiply(bounds, sign),
            hneg <= cp.multiply(bounds, 1 - sign),
            ah <= cp.multiply(bounds, selected),
            ah >= selected,
            buy >= 0,
            sell >= 0,
            buy <= cp.multiply(trade_bounds, buying),
            sell <= cp.multiply(trade_bounds, selling),
            buy >= buying,
            sell >= selling,
            buying + selling <= 1,
            h - current == buy - sell,
        ]
        for j, i in enumerate(candidates):
            if m.contract_map[i].kind == "STK":
                borrow_limit = quotes[i].shortable_shares or 0
                constraints += [hneg[j] <= max(borrow_limit, max(0, -actual.get(i, 0)))]
        buy_sizes, sell_sizes, fees_buy, fees_sell, spread_cost = [], [], [], [], []
        for i in candidates:
            c, q = m.contract_map[i], quotes[i]
            buy_size = max(1, min(1 if c.kind == "OPT" else 100, q.ask_size))
            sell_size = max(1, min(1 if c.kind == "OPT" else 100, q.bid_size))
            rate = m.policy.commission_option if c.kind == "OPT" else m.policy.commission_share
            buy_sizes.append(buy_size)
            sell_sizes.append(sell_size)
            fees_buy.append(max(m.policy.minimum_commission, rate * buy_size))
            fees_sell.append(max(m.policy.minimum_commission, rate * sell_size))
            spread_cost.append((q.ask - q.bid) * (0.5 + m.policy.slippage_spread_fraction) * c.multiplier)
        # Exact ceil of integer quantity / slice size, including zero trade.
        constraints += [
            buy_count >= 0,
            sell_count >= 0,
            cp.multiply(buy_sizes, buy_count) >= buy,
            cp.multiply(buy_sizes, buy_count) <= buy + np.array(buy_sizes) - 1,
            cp.multiply(sell_sizes, sell_count) >= sell,
            cp.multiply(sell_sizes, sell_count) <= sell + np.array(sell_sizes) - 1,
        ]
        costs = (
            np.array(spread_cost) @ (buy + sell)
            + np.array(fees_buy) @ buy_count
            + np.array(fees_sell) @ sell_count
        )
        all_h = [h[candidates.index(i)] if i in candidates else actual.get(i, 0) for i in ids]
        total_h = cp.hstack(all_h)
        absolute_h = cp.hstack(
            [ah[candidates.index(i)] if i in candidates else abs(actual.get(i, 0)) for i in ids]
        )
        # Cash sign is exact, so financing is not a fictitious adjustable loss.
        cash_plus, cash_minus, cash_sign = (
            cp.Variable(nonneg=True),
            cp.Variable(nonneg=True),
            cp.Variable(boolean=True),
        )
        prices = np.array([quotes[i].mid * m.contract_map[i].multiplier for i in candidates])
        cash_after = float(snapshot.cash) - prices @ (h - current) - costs
        cash_bound = 10 * budget
        constraints += [
            cash_plus - cash_minus == cash_after,
            cash_plus <= cash_bound * cash_sign,
            cash_minus <= cash_bound * (1 - cash_sign),
        ]
        days = scenarios.calendar_days
        short_stock = (
            cp.sum(
                cp.hstack(
                    [
                        hneg[j] * quotes[i].spot
                        for j, i in enumerate(candidates)
                        if m.contract_map[i].kind == "STK"
                    ]
                )
            )
            if any(m.contract_map[i].kind == "STK" for i in candidates)
            else 0
        )
        frozen_short = sum(
            max(0, -actual.get(i, 0)) * quotes[i].spot for i in frozen if m.contract_map[i].kind == "STK"
        )
        # Exact positive eligible-cash split: cannot invent interest to fit NAV.
        eligible_plus, eligible_minus, eligible_sign = (
            cp.Variable(nonneg=True),
            cp.Variable(nonneg=True),
            cp.Variable(boolean=True),
        )
        eligible_cash = cash_after - 1.02 * (short_stock + frozen_short) - m.policy.cash_interest_free_balance
        eligible_bound = 20 * budget + m.policy.cash_interest_free_balance
        constraints += [
            eligible_plus - eligible_minus == eligible_cash,
            eligible_plus <= eligible_bound * eligible_sign,
            eligible_minus <= eligible_bound * (1 - eligible_sign),
        ]
        carry = (
            days
            / m.policy.cash_day_count
            * (
                m.policy.cash_rate * min(1, float(snapshot.nav) / 100000) * eligible_plus
                - m.policy.financing_rate * cash_minus
                - m.policy.borrow_rate * (short_stock + frozen_short)
            )
        )
        scored_costs = 0 if unscored_setup else costs
        pnl = error + scenarios.changes @ total_h + carry - scored_costs
        objective_score = scenarios.weights @ cp.abs(pnl)
        if strategy == "historical_l1":
            objective_score = scenarios.weights @ cp.abs(scenarios.changes @ total_h + carry - costs)
        if strategy == "two_close" and scenarios.next_changes is not None:
            objective_score += scenarios.weights @ cp.abs(scenarios.next_changes @ total_h)
        constraints += [scenarios.stress @ total_h - 2 * costs >= -m.risk.stress_loss * budget]
        spots = np.array([quotes[i].spot * m.contract_map[i].multiplier for i in ids])
        constraints += [spots @ absolute_h <= m.risk.gross_notional * budget]
        increment = cp.Variable(n, nonneg=True)
        constraints += [increment >= ah - np.abs(current)]
        margin_rates = np.array(
            [
                (quotes[i].mid + 0.2 * quotes[i].spot) * m.contract_map[i].multiplier
                if m.contract_map[i].kind == "OPT"
                else 0.5 * quotes[i].spot
                for i in candidates
            ]
        )
        constraints += [
            margin_rates @ increment + costs
            <= float(snapshot.excess_liquidity) - m.risk.liquidity_buffer * budget
        ]
        symbols = sorted({m.contract_map[i].symbol for i in ids})
        delta_vectors = {}
        for symbol in symbols:
            delta_vector = np.array(
                [
                    quotes[i].delta * quotes[i].spot * m.contract_map[i].multiplier
                    if m.contract_map[i].symbol == symbol
                    else 0
                    for i in ids
                ]
            )
            delta_vectors[symbol] = delta_vector
            constraints += [cp.abs(delta_vector @ total_h) <= m.risk.residual_delta * budget]
            stock_vector = np.array(
                [
                    quotes[i].spot
                    if m.contract_map[i].symbol == symbol and m.contract_map[i].kind == "STK"
                    else 0
                    for i in ids
                ]
            )
            constraints += [stock_vector @ absolute_h <= m.risk.stock_notional * budget]
            option_j = [
                j
                for j, i in enumerate(candidates)
                if m.contract_map[i].symbol == symbol and m.contract_map[i].kind == "OPT"
            ]
            frozen_names = sum(
                actual.get(i, 0) != 0
                for i in frozen
                if i not in m.mandatory
                and m.contract_map[i].symbol == symbol
                and m.contract_map[i].kind == "OPT"
            )
            if option_j:
                constraints += [cp.sum(selected[option_j]) + frozen_names <= m.risk.option_names]
        # Screening baselines choose numerical quantities, still using same hard constraints.
        baseline = None
        if strategy == "delta":
            baseline = cp.sum(cp.hstack([cp.abs(v @ total_h) for v in delta_vectors.values()]))
        elif strategy == "greek":
            rows = []
            for symbol in symbols:
                for greek, scale in [("delta", 1), ("gamma", 0.05), ("vega", 0.01)]:
                    vector = np.array(
                        [
                            getattr(quotes[i], greek)
                            * m.contract_map[i].multiplier
                            * (
                                quotes[i].spot
                                if greek == "delta"
                                else (quotes[i].spot ** 2 * scale if greek == "gamma" else scale)
                            )
                            if m.contract_map[i].symbol == symbol
                            else 0
                            for i in ids
                        ]
                    )
                    rows.append(cp.abs(vector @ total_h))
            baseline = cp.sum(cp.hstack(rows))
        if strategy == "min_variance":
            # QP baseline supplies a desired continuous target, then integer L1
            # projection enforces identical trade/risk constraints.
            continuous = cp.Variable(n)
            fixed = scenarios.changes @ quantities - scenarios.changes[:, idx] @ current
            matrix = scenarios.changes[:, idx]
            centered = matrix - matrix.mean(axis=0)
            covariance = centered.T @ centered / max(1, len(matrix) - 1)
            covariance += np.eye(n) * max(1e-6, np.trace(covariance) / max(n, 1) * 0.01)
            problem = cp.Problem(
                cp.Minimize(
                    cp.quad_form(continuous, cp.psd_wrap(covariance))
                    + 2 * (centered.T @ (fixed - fixed.mean()) / max(1, len(matrix) - 1)) @ continuous
                ),
                [continuous <= bounds, continuous >= -bounds],
            )
            problem.solve(solver=cp.OSQP, max_iter=10000)
            if problem.status != "optimal":
                return self._unchanged(snapshot, scenarios, error, quotes, "BASELINE_FAILED")
            baseline = cp.norm1(h - continuous.value)
        options = {
            "threads": 1,
            "random_seed": m.policy.seed,
            "time_limit": m.policy.solver_seconds,
            "mip_rel_gap": 0.0,
            "mip_abs_gap": 1e-6,
        }
        start = monotonic()
        primary = baseline if baseline is not None else objective_score
        problem = cp.Problem(cp.Minimize(primary), constraints)
        try:
            problem.solve(solver=cp.HIGHS, highs_options=options)
            if problem.status != "optimal":
                return self._unchanged(snapshot, scenarios, error, quotes, problem.status.upper())
            optimum = float(primary.value)
            constraints.append(primary <= optimum + (1 if baseline is None else 0.01))
            if baseline is None and scenarios.next_changes is not None:
                next_objective = scenarios.weights @ cp.abs(scenarios.next_changes @ total_h)
                second = cp.Problem(cp.Minimize(next_objective), constraints)
                options["time_limit"] = max(0.01, m.policy.solver_seconds - (monotonic() - start))
                second.solve(solver=cp.HIGHS, highs_options=options)
                if second.status != "optimal":
                    return self._unchanged(snapshot, scenarios, error, quotes, "NEXT_CLOSE_TIMEOUT")
                constraints.append(next_objective <= float(next_objective.value) + 1)
            # Fixed ID ordering and single-thread solver; quantity tie-breaks are
            # tiny and subordinate to dollar turnover and explicit cardinality.
            tie = np.array([quotes[i].mid * m.contract_map[i].multiplier for i in candidates]) @ (buy + sell)
            tie += cp.sum(selected) * 0.001 + np.arange(1, n + 1) @ h * 1e-9
            final = cp.Problem(cp.Minimize(tie), constraints)
            options["time_limit"] = max(0.01, m.policy.solver_seconds - (monotonic() - start))
            final.solve(solver=cp.HIGHS, highs_options=options)
            if final.status != "optimal" or monotonic() - start > m.policy.solver_seconds + 0.5:
                return self._unchanged(snapshot, scenarios, error, quotes, "TIEBREAK_TIMEOUT")
            result = dict(actual)
            for i, value in zip(candidates, h.value, strict=True):
                result[i] = int(round(value))
            result = {i: q for i, q in result.items() if q}
            delta = [result.get(i, 0) - actual.get(i, 0) for i in candidates]
            cost = execution_cost(m, quotes, candidates, delta)
            target_vector = np.array([result.get(i, 0) for i in ids])
            new_pnl = (
                error
                + scenarios.changes @ target_vector
                + float(carry.value)
                - (0 if unscored_setup else cost)
            )
            # No-trade includes its own carry, rather than comparing net against gross.
            current_carry = cash_carry(
                m,
                float(snapshot.cash),
                sum(
                    max(0, -actual.get(i, 0)) * quotes[i].spot for i in ids if m.contract_map[i].kind == "STK"
                ),
                days,
                float(snapshot.nav),
            )
            old_pnl = error + scenarios.changes @ quantities + current_carry
            differences = np.abs(old_pnl) - np.abs(new_pnl)
            uncertainty = (
                float(np.std(differences, ddof=1) / np.sqrt(len(differences))) if len(differences) > 1 else 0
            )
            return Target(
                quantities=result,
                expected_score=float(scenarios.weights @ np.abs(new_pnl)),
                current_score=float(scenarios.weights @ np.abs(old_pnl)),
                uncertainty=uncertainty,
                status="OPTIMAL",
                snapshot_id=snapshot.version,
                scenario_id=scenarios.identifier,
                config_hash=m.config_hash,
                estimated_cost=cost,
                explanation=f"{strategy}: integer scenario hedge; {scenarios.source}. Costs are included once.",
            )
        except cp.error.SolverError as exc:
            return self._unchanged(snapshot, scenarios, error, quotes, f"SOLVER_ERROR:{exc}")

    def _unchanged(self, snapshot, scenarios, error, quotes, status):
        q = np.array([snapshot.quantities().get(i, 0) for i in scenarios.ids])
        borrow = sum(
            max(0, -amount) * quotes[i].spot
            for i, amount in snapshot.quantities().items()
            if i in quotes and self.manifest.contract_map[i].kind == "STK"
        )
        carry = cash_carry(
            self.manifest, float(snapshot.cash), borrow, scenarios.calendar_days, float(snapshot.nav)
        )
        score = float(scenarios.weights @ np.abs(error + scenarios.changes @ q + carry))
        return Target(
            quantities=snapshot.quantities(),
            expected_score=score,
            current_score=score,
            uncertainty=0,
            status=status,
            snapshot_id=snapshot.version,
            scenario_id=scenarios.identifier,
            config_hash=self.manifest.config_hash,
            explanation="No trade: optimizer did not return an accepted optimum.",
        )
