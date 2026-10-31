from __future__ import annotations

import math


def build_profile(age: int, capital_inr: float, horizon: str) -> dict:
    """Build investor capital context used by risk-based position sizing."""
    age = int(age)
    capital_inr = float(capital_inr)
    if not 18 <= age <= 100:
        raise ValueError("Age must be between 18 and 100.")
    if capital_inr <= 0:
        raise ValueError("Capital must be greater than zero.")

    horizon_share = {
        "SHORT_TERM": 0.15,
        "MID_TERM": 0.40,
        "LONG_TERM": 0.70,
    }.get(horizon, 0.40)
    strategy_capital = capital_inr * horizon_share
    return {
        "age": age,
        "capital_inr": round(capital_inr, 2),
        "horizon_capital_inr": round(strategy_capital, 2),
        "horizon_capital_share_pct": round(horizon_share * 100, 1),
        "risk_per_position_pct": 0.50,
        "max_position_weight_pct": 10.0,
        "max_deployment_pct": 100.0,
        "note": (
            "Position sizing uses a fixed capital risk budget and volatility-derived "
            "stop distance. It is a research estimate, not an order instruction."
        ),
    }


def _positive_number(row: dict, key: str) -> float | None:
    try:
        value = float(row.get(key))
        return value if math.isfinite(value) and value > 0 else None
    except (TypeError, ValueError):
        return None


def add_position_context(rows: list[dict], profile: dict) -> None:
    """
    Size only PASS candidates by risk budget divided by estimated stop distance.

    A high ranking score does not directly create a larger allocation. ATR is
    preferred; annualized volatility is the fallback. Individual and aggregate
    deployment caps prevent oversized allocations.
    """
    risk_budget_pct = float(profile.get("risk_per_position_pct", 0.50))
    max_position_pct = float(profile.get("max_position_weight_pct", 10.0))
    max_deployment_pct = float(profile.get("max_deployment_pct", 100.0))

    eligible = []
    for row in rows:
        if (
            row.get("decision_verdict") != "PASS"
            or row.get("signal") not in {"SETUP", "ACCUMULATE", "QUALITY_CANDIDATE"}
        ):
            continue
        stop_distance = None
        atr_pct = _positive_number(row, "atr_pct")
        if atr_pct is not None:
            stop_distance = max(3.0, atr_pct * 2.0)
        else:
            annual_vol = _positive_number(row, "volatility_pct")
            if annual_vol is not None:
                # Approximate a two-standard-deviation, one-month move.
                stop_distance = max(3.0, annual_vol * 2.0 / math.sqrt(12.0))
        if stop_distance is None:
            row["position_sizing_status"] = "INSUFFICIENT_VOLATILITY_DATA"
            row["risk_stop_distance_pct"] = None
            continue

        weight_pct = min(max_position_pct, risk_budget_pct / stop_distance * 100.0)
        row["risk_stop_distance_pct"] = round(stop_distance, 2)
        row["position_sizing_status"] = "RISK_BUDGETED"
        eligible.append((row, weight_pct))

    total_weight = sum(weight for _, weight in eligible)
    scale = min(1.0, max_deployment_pct / total_weight) if total_weight > 0 else 1.0

    for row in rows:
        weight_pct = 0.0
        for candidate, candidate_weight in eligible:
            if candidate is row:
                weight_pct = candidate_weight * scale
                break
        weight = weight_pct / 100.0
        row["suggested_weight_pct"] = round(weight_pct, 2)
        row["capital_context_inr"] = round(
            float(profile["horizon_capital_inr"]) * weight, 2
        )
        if row.get("market_ranking_score") is None:
            row["market_ranking_score"] = row.get("ranking_score")
        if row.get("portfolio_adjusted_score") is None:
            row["portfolio_adjusted_score"] = row.get("ranking_score")
