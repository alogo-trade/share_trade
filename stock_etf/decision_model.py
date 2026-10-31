from __future__ import annotations

from datetime import date, datetime
from typing import Any

MODEL_VERSION = "transparent-decision-tree-v1"
ELIGIBLE_SIGNALS = {"SETUP", "ACCUMULATE", "QUALITY_CANDIDATE"}


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except (TypeError, ValueError):
        return None


def evaluate_candidate(row: dict[str, Any], horizon: str, as_of: date | None = None) -> dict[str, Any]:
    """Auditable pre-LLM decision tree. It checks data suitability, not future returns."""
    as_of = as_of or date.today()
    reasons: list[str] = []
    signal = str(row.get("signal") or "").upper()
    score = row.get("ranking_score")
    observations = int(row.get("data_points") or 0)
    confidence = float(row.get("data_confidence") or 0)
    last_date = _as_date(row.get("last_date"))
    age_days = (as_of - last_date).days if last_date else None
    min_observations = {"SHORT_TERM": 50, "MID_TERM": 100, "LONG_TERM": 200}.get(str(horizon).upper(), 100)

    if row.get("error") or row.get("data_quality_status") == "ERROR":
        return {"symbol": row.get("symbol"), "model": MODEL_VERSION, "verdict": "REJECT",
                "eligible_for_llm": False, "reasons": ["Market-data error."],
                "features": {"data_points": observations, "data_confidence": confidence,
                             "data_age_days": age_days, "signal": signal}}
    if score is None and row.get("data_quality_status") in {"STALE_OR_UNKNOWN", "INSUFFICIENT_HISTORY", "LOW_CONFIDENCE"}:
        return {"symbol": row.get("symbol"), "model": MODEL_VERSION, "verdict": "HOLD",
                "eligible_for_llm": False,
                "reasons": [f"Data-quality gate failed: {row.get('data_quality_status')}."],
                "features": {"data_points": observations, "data_confidence": confidence,
                             "data_age_days": age_days, "signal": signal}}
    if score is None:
        return {"symbol": row.get("symbol"), "model": MODEL_VERSION, "verdict": "REJECT",
                "eligible_for_llm": False, "reasons": ["No usable quantitative score."],
                "features": {"data_points": observations, "data_confidence": confidence,
                             "data_age_days": age_days, "signal": signal}}

    verdict = "PASS"
    if signal == "AVOID":
        reasons.append("Quantitative signal is AVOID.")
        verdict = "REJECT"
    elif signal not in ELIGIBLE_SIGNALS:
        reasons.append(f"Signal {signal or 'UNKNOWN'} is not eligible for LLM escalation.")
        verdict = "HOLD"
    if last_date is None:
        reasons.append("Market-data date is missing or invalid.")
        verdict = "HOLD"
    elif age_days is not None and (age_days < 0 or age_days > 7):
        reasons.append(f"Market data is stale ({age_days} calendar days old; maximum is 7).")
        verdict = "HOLD"
    if observations < min_observations:
        reasons.append(f"Insufficient history: {observations} observations; {min_observations} required for {str(horizon).lower()} research.")
        verdict = "HOLD"
    if confidence < 70:
        reasons.append(f"Data confidence is {confidence:.1f}%; minimum is 70%.")
        verdict = "HOLD"
    if str(horizon).upper() == "SHORT_TERM":
        rsi = row.get("rsi14")
        extension = row.get("distance_sma20_pct")
        if rsi is not None and float(rsi) > 85:
            reasons.append(f"RSI is extremely overbought ({float(rsi):.1f}).")
            verdict = "HOLD"
        if extension is not None and float(extension) > 20:
            reasons.append(f"Price is excessively extended above SMA20 ({float(extension):.1f}%).")
            verdict = "HOLD"
    if verdict == "PASS":
        reasons.append("Passed all deterministic pre-LLM checks.")
    return {"symbol": row.get("symbol"), "model": MODEL_VERSION, "verdict": verdict,
            "eligible_for_llm": verdict == "PASS", "reasons": reasons,
            "features": {"signal": signal, "ranking_score": float(score),
                         "data_points": observations, "minimum_observations": min_observations,
                         "data_confidence": confidence, "data_age_days": age_days,
                         "rsi14": row.get("rsi14"), "distance_sma20_pct": row.get("distance_sma20_pct")}}


def evaluate_candidates(rows: list[dict[str, Any]], horizon: str, as_of: date | None = None) -> dict[str, Any]:
    decisions = [evaluate_candidate(row, horizon, as_of) for row in rows]
    by_symbol = {str(item.get("symbol") or "").upper(): item for item in decisions}
    for row in rows:
        decision = by_symbol.get(str(row.get("symbol") or "").upper())
        if decision:
            row["decision_model"] = decision
            row["decision_verdict"] = decision["verdict"]
            row["eligible_for_llm"] = decision["eligible_for_llm"]
    counts = {v: sum(item["verdict"] == v for item in decisions) for v in ("PASS", "HOLD", "REJECT")}
    return {"model": MODEL_VERSION,
            "description": "Deterministic pre-LLM data-quality and signal-consistency gate; not a return predictor.",
            "evaluated_count": len(decisions), "pass_count": counts["PASS"],
            "hold_count": counts["HOLD"], "reject_count": counts["REJECT"], "decisions": decisions}
