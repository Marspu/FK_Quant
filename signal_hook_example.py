from __future__ import annotations

from typing import Any, Dict


def predict_signal(context: Dict[str, Any]) -> Dict[str, float]:
    """
    Example signal hook for lof_t0_grid_xtquant.py.

    Input:
        context["symbol"]   -> str
        context["features"] -> dict
        context["state"]    -> dict

    Return:
        {
            "score": float in [-1, 1],
            "grid_multiplier": float
        }
    """
    features = context["features"]
    ret_20 = float(features.get("ret_20", 0.0))
    range_pos = float(features.get("range_pos", 0.0))
    imbalance = float(features.get("imbalance", 0.0))
    spread_pct = float(features.get("spread_pct", 0.0))

    score = 0.0
    score += 8.0 * ret_20
    score += 0.2 * range_pos
    score += 0.2 * imbalance
    score -= min(0.2, spread_pct * 20.0)

    if score > 1.0:
        score = 1.0
    if score < -1.0:
        score = -1.0

    grid_multiplier = 1.0
    if abs(score) < 0.2:
        grid_multiplier = 1.1

    return {"score": score, "grid_multiplier": grid_multiplier}
