from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from itertools import pairwise

import numpy as np
import pandas as pd

from .config import NY, Manifest
from .models import Contract, PortfolioSnapshot, Position, Quote, digest
from .optimizer import HedgeOptimizer, execution_cost
from .risk import RiskGate
from .scenarios import ScenarioEngine


@dataclass(frozen=True)
class Variant:
    strategy: str = "dynamic_l1"
    stock_minutes: int = 1
    option_minutes: int = 15
    threshold: float = 100
    history_sessions: int = 60
    policy: str = "threshold"  # threshold, periodic, daily, static, unhedged

    @property
    def name(self):
        return f"{self.strategy}/{self.policy}/{self.stock_minutes}m/{self.option_minutes}m/${self.threshold:g}/{self.history_sessions}d"


def normalized_quotes(frame):
    frame = frame.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame["day"] = frame.timestamp.dt.tz_convert(NY).dt.date
    if "con_id" not in frame:
        if not {"symbol", "expiry", "strike", "right"}.issubset(frame.columns):
            raise ValueError("Archive needs contract IDs or complete option identity")
        frame["con_id"] = [
            int(digest([r.symbol, str(r.expiry), r.strike, r.right])[:7], 16) + 1000
            for r in frame.itertuples()
        ]
    frame["con_id"] = frame.con_id.astype(int)
    if not {"bid", "ask", "bid_size", "ask_size", "spot"}.issubset(frame):
        raise ValueError(
            "Replay requires bid/ask, sizes, and underlying prices; OHLC bars cannot imply fills"
        )
    if (frame.ask < frame.bid).any() or (frame.bid < 0).any():
        raise ValueError("Invalid archived quotes")
    return frame.sort_values("timestamp")


def match_window(template, frame, days, reference_spots):
    """Choose only contracts quoted at the initial Day 0 decision timestamp."""
    day_zero, scored = days[0], tuple(days[1:])
    initial = frame[frame.day == day_zero]
    if initial.empty:
        raise ValueError("No Day 0 quotes")
    first = initial.timestamp.min()
    available = initial[initial.timestamp == first].copy()
    available = available[available.right.isin(["C", "P"])]
    if not {"expiry", "strike", "right", "symbol", "iv"}.issubset(available):
        raise ValueError("Historical option identities and IV required for matching")
    available["expiry"] = pd.to_datetime(available.expiry).dt.date
    contracts, mandatory, selected = {}, {}, set()
    for i, quantity in template.mandatory.items():
        original = template.contract_map[i]
        reference = reference_spots.get(original.symbol)
        if reference is None:
            raise ValueError(f"Explicit reference spot needed to match moneyness for {original.symbol}")
        options = available[
            (available.symbol == original.symbol)
            & (available.right == original.right)
            & (available.expiry >= scored[-1])
        ].copy()
        if options.empty:
            raise ValueError("No eligible Day 0 contract for mandatory book")
        desired_days = (original.expiry - template.day_zero).days
        options["distance"] = [
            (
                abs((r.expiry - day_zero).days - desired_days),
                abs(r.strike / r.spot - original.strike / reference),
                int(r.con_id),
            )
            for r in options.itertuples()
        ]
        row = options.sort_values("distance").iloc[0]
        identifier = int(row.con_id)
        if identifier in selected:
            raise ValueError("Distinct mandatory contracts collapsed during matching")
        selected.add(identifier)
        mandatory[identifier] = quantity
    for row in available.itertuples():
        identifier = int(row.con_id)
        if row.expiry < scored[-1]:
            continue
        stock_id = int(digest([row.symbol, "STK"])[:7], 16) + 1
        contracts[stock_id] = Contract(con_id=stock_id, symbol=row.symbol, kind="STK")
        contracts[identifier] = Contract(
            con_id=identifier,
            symbol=row.symbol,
            kind="OPT",
            multiplier=100,
            strike=float(row.strike),
            right=str(row.right),
            expiry=row.expiry,
            underlying_id=stock_id,
        )
    policy = template.policy
    return Manifest(
        name=f"Historical matched window {day_zero}",
        account="DU_REPLAY",
        day_zero=day_zero,
        scoring_sessions=scored,
        valuation_convention="Archived midpoint NAV proxy; not official IBKR marks",
        report_source="Historical NBBO replay",
        contracts=tuple(contracts.values()),
        mandatory=mandatory,
        allowlist=tuple(i for i in contracts if i not in mandatory),
        confirmed=True,
        initial_nav=template.initial_nav,
        risk=template.risk,
        policy=policy,
        risk_free_rate=template.risk_free_rate,
        strategy=template.strategy,
    )


class ReplayEngine:
    """Quote-based execution with latency, conservative fills, and shared risk/optimizer.

    Each fill occurs at a subsequent observed quote, never at the signal quote
    or a future bar's low/high. Full archival valuation is a scoring proxy.
    """

    def __init__(self, manifest, variant=None, adverse_spreads=1.0):
        self.manifest, self.variant, self.adverse_spreads = manifest, variant or Variant(), adverse_spreads

    def run(self, frame, history=None):
        frame = normalized_quotes(frame)
        m = self.manifest.model_copy(
            update={
                "policy": self.manifest.policy.model_copy(
                    update={"history_sessions": self.variant.history_sessions}
                )
            }
        )
        optimizer, gate = HedgeOptimizer(m), RiskGate(m)
        scenario_engine = ScenarioEngine(m, history if history is not None else frame)
        q = dict(m.mandatory)
        quotes = {}
        cash = float(m.initial_nav)
        initialized = False
        closes, transactions, costs, rejected = {}, 0, 0.0, 0
        last_decision, last_option = None, None
        pending = []
        last_marks = {}
        max_gross, max_stress = 0.0, 0.0
        previous_time = None
        close_times = {}
        for timestamp, rows in frame.groupby("timestamp", sort=True):
            now = timestamp.to_pydatetime()
            day = now.astimezone(NY).date()
            if day not in (m.day_zero, *m.scoring_sessions):
                continue
            if now > m.session_close(day):
                continue
            for row in rows.itertuples():
                identifier = int(row.con_id)
                if identifier not in m.contract_map:
                    continue
                c = m.contract_map[identifier]
                iv = getattr(row, "iv", None)
                if c.kind == "OPT" and (iv is None or not np.isfinite(iv) or iv <= 0):
                    raise ValueError("Archived IV required; do not fabricate historical option Greeks")
                valuation = scenario_engine.valuation
                greeks = valuation.greeks(c, float(row.spot), float(iv), now) if c.kind == "OPT" else {}
                midpoint = (row.bid + row.ask) / 2
                width = (row.ask - row.bid) * self.adverse_spreads
                quotes[identifier] = Quote(
                    con_id=identifier,
                    bid=max(0, midpoint - width / 2),
                    ask=midpoint + width / 2,
                    bid_size=int(row.bid_size),
                    ask_size=int(row.ask_size),
                    observed_at=now,
                    spot=float(row.spot),
                    iv=float(iv) if c.kind == "OPT" else None,
                    greek_at=now,
                    **greeks,
                )
                # Cboe's underlying BBO supports stock execution; absent BBO
                # means stock cannot be traded, rather than inventing a spread.
                if c.kind == "OPT" and hasattr(row, "underlying_bid") and hasattr(row, "underlying_ask"):
                    quotes[c.underlying_id] = Quote(
                        con_id=c.underlying_id,
                        bid=float(row.underlying_bid),
                        ask=float(row.underlying_ask),
                        bid_size=int(getattr(row, "underlying_bid_size", 0)),
                        ask_size=int(getattr(row, "underlying_ask_size", 0)),
                        shortable_shares=int(getattr(row, "shortable_shares", 0)),
                        spot=float(row.spot),
                        observed_at=now,
                    )
            if not set(q).issubset(quotes):
                continue
            if not initialized:
                cash -= sum(q[i] * m.contract_map[i].multiplier * quotes[i].ask for i in q)
                cash -= sum(
                    max(m.policy.minimum_commission, abs(q[i]) * m.policy.commission_option) for i in q
                )
                initialized = True
            if previous_time:
                calendar_days = (now - previous_time).total_seconds() / 86400
                from .cashflows import cash_carry

                cash += cash_carry(
                    m,
                    cash,
                    sum(
                        max(0, -amount) * quotes[i].spot
                        for i, amount in q.items()
                        if m.contract_map[i].kind == "STK"
                    ),
                    calendar_days,
                    cash + sum(a * m.contract_map[i].multiplier * quotes[i].mid for i, a in q.items()),
                )
            previous_time = now
            # Pending signals execute only when a subsequent fresh quote exists.
            for signal, identifier, signed in list(pending):
                quote = quotes[identifier]
                if quote.observed_at <= signal:
                    continue
                size = min(
                    abs(signed),
                    1 if m.contract_map[identifier].kind == "OPT" else 100,
                    quote.ask_size if signed > 0 else quote.bid_size,
                )
                if not size:
                    continue
                amount = size if signed > 0 else -size
                cash_before = cash
                nav_before = cash + sum(
                    amount_held * m.contract_map[i].multiplier * quotes[i].mid for i, amount_held in q.items()
                )
                snap = PortfolioSnapshot(
                    account=m.account,
                    at=now,
                    account_at=now,
                    nav=Decimal(str(nav_before)),
                    cash=Decimal(str(cash)),
                    excess_liquidity=Decimal(str(max(0, nav_before * 0.8))),
                    initial_margin=Decimal(str(nav_before * 0.2)),
                    positions=tuple(Position(con_id=i, quantity=a) for i, a in q.items() if a),
                )
                scenarios = scenario_engine.build(snap, quotes, now, sorted(quotes))
                target = dict(q)
                target[identifier] = target.get(identifier, 0) + amount
                c = m.contract_map[identifier]
                check = gate.validate(
                    snap,
                    quotes,
                    scenarios,
                    target,
                    now,
                    costs=execution_cost(m, quotes, [identifier], [amount]),
                    corrective=not gate.engine.evaluate(snap, quotes, scenarios, q).passed,
                )
                pending.remove((signal, identifier, signed))
                if not check.passed:
                    rejected += 1
                    continue
                rate = m.policy.commission_option if c.kind == "OPT" else m.policy.commission_share
                fee = max(m.policy.minimum_commission, size * rate)
                price = quote.ask if amount > 0 else quote.bid
                price += (
                    (quote.ask - quote.bid) * m.policy.slippage_spread_fraction * (1 if amount > 0 else -1)
                )
                cash -= amount * c.multiplier * price + fee
                q[identifier] = target[identifier]
                costs += abs(amount) * c.multiplier * abs(price - quote.mid) + fee
                transactions += 1
                if signed != amount:
                    pending.append((now, identifier, signed - amount))
                if cash == cash_before:
                    raise AssertionError("Trade did not change cash")
            nav = cash + sum(amount * m.contract_map[i].multiplier * quotes[i].mid for i, amount in q.items())
            closes[day] = nav
            close_times[day] = now
            if (m.session_close(day) - now).total_seconds() <= 60:
                stale = [
                    i
                    for i, amount in q.items()
                    if amount and (now - quotes[i].observed_at).total_seconds() > 60
                ]
                if stale:
                    raise ValueError(f"Stale closing marks for held instruments {stale}")
            last_marks = dict(quotes)
            previous = (
                closes.get(m.day_zero)
                if day == m.day_zero
                else closes.get(
                    (m.day_zero, *m.scoring_sessions)[(m.day_zero, *m.scoring_sessions).index(day) - 1]
                )
            )
            if previous is None or self.variant.policy == "unhedged" or pending:
                continue
            if day != m.day_zero:
                previous = float(m.scoring_target(Decimal(str(previous)), day))
            if self.variant.policy == "static" and day != m.day_zero:
                continue
            if (
                self.variant.policy == "daily"
                and last_decision
                and last_decision.astimezone(NY).date() == day
            ):
                continue
            if last_decision and now - last_decision < timedelta(minutes=self.variant.stock_minutes):
                continue
            stock_only = bool(
                last_option and now - last_option < timedelta(minutes=self.variant.option_minutes)
            )
            if not m.can_trade(now, "STK"):
                continue
            snap = PortfolioSnapshot(
                account=m.account,
                at=now,
                account_at=now,
                nav=Decimal(str(nav)),
                cash=Decimal(str(cash)),
                excess_liquidity=Decimal(str(nav * 0.8)),
                initial_margin=Decimal(str(nav * 0.2)),
                positions=tuple(Position(con_id=i, quantity=a) for i, a in q.items() if a),
            )
            scenarios = scenario_engine.build(snap, quotes, now, sorted(quotes))
            risk = gate.engine.evaluate(snap, quotes, scenarios, q)
            max_gross = max(max_gross, risk.metrics.get("gross_notional", 0))
            max_stress = max(max_stress, risk.metrics.get("stress_loss", 0))
            result = optimizer.solve(
                snap, quotes, scenarios, previous, stock_only=stock_only, strategy=self.variant.strategy
            )
            last_decision = now
            if not stock_only:
                last_option = now
            improvement = result.current_score - result.expected_score
            required = (
                max(self.variant.threshold, m.policy.uncertainty_multiplier * result.uncertainty)
                if self.variant.policy == "threshold"
                else 0
            )
            if result.status != "OPTIMAL" or improvement <= required:
                continue
            for i in sorted(set(result.quantities) | set(q)):
                difference = result.quantities.get(i, 0) - q.get(i, 0)
                if difference:
                    pending.append((now, i, difference))
        days = (m.day_zero, *m.scoring_sessions)
        missing = [str(day) for day in days if day not in closes]
        if missing:
            raise ValueError(f"Missing closing observations for {missing}")
        for day in days:
            if (m.session_close(day) - close_times[day]).total_seconds() > 60:
                raise ValueError(f"Closing quote missing/stale for {day}; never substitute an intraday value")
        # A stale last observation is not silently called a close.
        if not last_marks:
            raise ValueError("No marks")
        scores = [
            abs(closes[b] - float(m.scoring_target(Decimal(str(closes[a])), b))) for a, b in pairwise(days)
        ]
        return {
            "variant": self.variant.name,
            "weekly_score": sum(scores),
            "daily_scores": scores,
            "closes": {str(k): v for k, v in closes.items()},
            "transactions": transactions,
            "execution_costs": costs,
            "rejected_slices": rejected,
            "max_gross": max_gross,
            "max_stress": max_stress,
            "mandate_compliant": all(q.get(i) == amount for i, amount in m.mandatory.items()),
            "evidence": "historical_quote_NAV_proxy"
            if "synthetic" not in str(frame.get("source", ""))
            else "synthetic_model_test",
            "valuation": "Archived midpoint NAV proxy, not official IBKR contest marks",
        }


def walk_forward(
    template, frame, reference_spots, variants, training_days=120, validation_days=20, test_days=10
):
    frame = normalized_quotes(frame)
    days = sorted(frame.day.unique())
    results = []
    span = len(template.scoring_sessions) + 1
    for start in range(training_days, len(days) - validation_days - test_days + 1, test_days):
        validation = days[start : start + validation_days]
        tested = days[start + validation_days : start + validation_days + test_days]
        rankings = []
        for variant in variants:
            scores = []
            for offset in range(0, len(validation) - span + 1, span):
                window = validation[offset : offset + span]
                manifest = match_window(template, frame, window, reference_spots)
                result = ReplayEngine(manifest, variant).run(
                    frame[frame.day.isin(window)], frame[frame.day < window[0]]
                )
                scores.append(result["weekly_score"])
            if scores:
                rankings.append(
                    (float(np.mean(scores)), float(np.quantile(scores, 0.9)), variant.name, variant)
                )
        if not rankings:
            continue
        selected = min(rankings, key=lambda item: item[:3])[-1]
        for offset in range(0, len(tested) - span + 1, span):
            window = tested[offset : offset + span]
            manifest = match_window(template, frame, window, reference_spots)
            result = ReplayEngine(manifest, selected).run(
                frame[frame.day.isin(window)], frame[frame.day < window[0]]
            )
            results.append({"selection": "preceding validation ONLY", "start": str(window[0]), **result})
    scores = [r["weekly_score"] for r in results]
    return {
        "test_windows": results,
        "count": len(results),
        "mean_score": float(np.mean([r["weekly_score"] for r in results])) if results else None,
        "median_score": float(np.median(scores)) if scores else None,
        "p90_score": float(np.quantile(scores, 0.9)) if scores else None,
        "worst_score": max(scores) if scores else None,
        "performance_claim": False if not results else "Quote NAV proxy; validate against paper reports",
    }
