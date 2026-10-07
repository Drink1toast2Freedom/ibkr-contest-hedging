"""Chronological forecast evaluation. OHLC results never grant paper promotion."""

from __future__ import annotations

from datetime import datetime, time

import numpy as np
import pandas as pd

from .config import NY
from .models import digest
from .recovery import fit_models, infer, normalize_bars


def walk_forward_forecasts(frame, manifest, config, train_sessions=60, test_sessions=5):
    f = normalize_bars(frame)
    days = sorted(f.day.unique())
    if len(days) < train_sessions + test_sessions:
        raise ValueError("Insufficient sessions for chronological forecast validation")
    predictions, folds = [], []
    for start in range(train_sessions, len(days), test_sessions):
        test_days = days[start : start + test_sessions]
        if len(test_days) < test_sessions:
            break
        before = datetime.combine(test_days[0], time(9, 30), NY)
        bundle = fit_models(
            f, manifest, config.model_copy(update={"window_sessions": train_sessions}), before
        )
        trained_last = max(m["trained_through"] for m in bundle["models"])
        folds.append(
            {
                "trained_through": trained_last,
                "test_start": str(test_days[0]),
                "test_end": str(test_days[-1]),
                "model_hash": bundle["model_hash"],
            }
        )
        for day in test_days:
            close = manifest.session_close(day)
            current = f[f.day == day]
            # Fixed models for the entire test fold; labels remain unavailable
            # until that session's closing bar has completed.
            for at in sorted(current.timestamp.unique())[60:-15:15]:
                now = pd.Timestamp(at).to_pydatetime()
                forecasts = infer(bundle, f, config, now, manifest)
                for forecast in forecasts:
                    symbol = forecast["symbol"]
                    bar = current[(current.symbol == symbol) & (current.timestamp <= pd.Timestamp(now))]
                    closing = current[(current.symbol == symbol) & (current.timestamp == pd.Timestamp(close))]
                    if bar.empty or closing.empty:
                        continue
                    actual = float(closing.close.iloc[-1] / bar.close.iloc[-1] - 1)
                    predictions.append({**forecast, "day": str(day), "actual_return": actual})
    if not predictions:
        raise ValueError("No complete held-out forecast observations")
    table = pd.DataFrame(predictions)
    metrics = {}
    for kind, part in table.groupby("strategy"):
        prediction = part.mean_return.to_numpy()
        actual = part.actual_return.to_numpy()
        conservative = part.conservative_return.to_numpy()
        active = conservative != 0
        metrics[kind] = {
            "observations": len(part),
            "test_sessions": part.day.nunique(),
            "rmse": float(np.sqrt(np.mean((prediction - actual) ** 2))),
            "zero_forecast_rmse": float(np.sqrt(np.mean(actual**2))),
            "active_fraction": float(active.mean()),
            "direction_hit_rate": float((np.sign(conservative[active]) == np.sign(actual[active])).mean())
            if active.any()
            else None,
            "mean_directional_return_before_costs": float(
                (np.sign(conservative[active]) * actual[active]).mean()
            )
            if active.any()
            else None,
        }
    return {
        "schema": 1,
        "evidence_class": "bar_based_forecast_validation",
        "paper_eligible": False,
        "promotion_blockers": [
            "Historical bars do not establish executions or combined contest scores",
            "Combined quote-based held-out contest replay, forward shadow and fault evidence required",
        ],
        "folds": folds,
        "metrics": metrics,
        "data_hash": digest(f.drop(columns="day").to_json(date_format="iso")),
        "predictions": predictions,
    }
