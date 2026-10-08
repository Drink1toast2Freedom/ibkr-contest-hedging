"""Offline add-on compute benchmark. Synthetic quotes, no broker or database."""

import argparse
import json
import statistics
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hedge.config import load_manifest  # noqa: E402
from hedge.demo import demo_manifest, demo_quotes  # noqa: E402
from hedge.models import PortfolioSnapshot, Position  # noqa: E402
from hedge.scenarios import ScenarioEngine  # noqa: E402
from surplus_addon.controller import evaluate  # noqa: E402
from surplus_addon.models import Input, Settings  # noqa: E402
from surplus_addon.sources import scenario_payload  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config")
    parser.add_argument("--repeats", type=int, default=10)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 50:
        parser.error("Use between 1 and 50 repeats")
    m = load_manifest(args.config) if args.config else demo_manifest()
    m = m.model_copy(update={"account": "DU_SURPLUS_BENCHMARK"})
    now = m.session_close(m.scoring_sessions[0]) - timedelta(minutes=6)
    quotes = demo_quotes(m, now)
    nav = m.initial_nav + Decimal("5000")
    holdings = sum(
        Decimal(str(quotes[i].mid)) * q * m.contract_map[i].multiplier for i, q in m.mandatory.items()
    )
    snapshot = PortfolioSnapshot(
        account=m.account,
        at=now,
        account_at=now,
        nav=nav,
        cash=nav - holdings,
        excess_liquidity=Decimal("800000"),
        initial_margin=Decimal("200000"),
        positions=tuple(Position(con_id=i, quantity=q) for i, q in m.mandatory.items()),
        source="synthetic",
    )
    scenarios = ScenarioEngine(m).build(snapshot, quotes, now)
    packet = Input(
        generated_at=now,
        manifest=m,
        snapshot=snapshot,
        quotes=quotes,
        scenarios=scenario_payload(scenarios),
        scenario_at=now,
        scenario_snapshot_id=snapshot.version,
        scenario_revision=0,
        broker_revision=0,
        baseline_nav=m.initial_nav,
        nav_estimate=float(nav),
        target=m.initial_nav,
        target_estimated=False,
        forecast_close=m.session_close(m.scoring_sessions[0]),
        main_mode="SYNTHETIC",
        execution_busy=False,
        config_hash=m.config_hash,
        snapshot_id=snapshot.version,
    )
    stats = {"expense_used": 0, "orders": 0, "turnover": 0}
    decisions = [evaluate(Settings(), packet, stats, now) for _ in range(args.repeats)]
    times = [d["compute_ms"] for d in decisions]
    print(
        json.dumps(
            {
                "evidence": "synthetic computation only; excludes HTTP, scenario building, LLM and broker execution",
                "instruments": len(scenarios.ids),
                "scenario_rows": len(scenarios.weights),
                "stress_rows": len(scenarios.stress),
                "samples": len(times),
                "median_ms": statistics.median(times),
                "maximum_ms": max(times),
                "states": sorted({d["state"] for d in decisions}),
                "partial_holdings_in_selected_path": (decisions[0].get("proposal") or {}).get(
                    "path_checked_holdings"
                ),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
