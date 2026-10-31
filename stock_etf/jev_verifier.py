"""Optional Jev verifier using OpenRouter's typed Decisions API."""
from __future__ import annotations

import logging
import math
import os
from typing import Any

import requests

logger = logging.getLogger("stock_etf.jev")
DEFAULT_MODEL = "typesafe/jev-1.14"
DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
CRITERIA = ["PASS", "REVIEW", "REJECT"]
CHOICES = {"PASS", "REVIEW", "REJECT"}



def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _candidate_state(row: dict[str, Any], horizon: str) -> dict[str, Any]:
    allowed = (
        "symbol", "name", "instrument_type", "type", "sector", "industry",
        "signal", "ranking_score", "market_ranking_score", "portfolio_adjusted_score",
        "data_points", "data_confidence", "last_date", "rsi14",
        "distance_sma20_pct", "distance_sma50_pct", "return_1w_pct",
        "return_1m_pct", "return_3m_pct", "return_6m_pct",
        "volatility_annual_pct", "volume_ratio_20d", "pe_ratio",
        "price_to_book", "roe_pct", "debt_to_equity", "dividend_yield_pct",
        "market_cap", "portfolio_weight_pct", "portfolio_sector_exposure_pct",
    )
    result: dict[str, Any] = {"horizon": horizon}
    for key in allowed:
        value = row.get(key)
        if isinstance(value, (str, bool)):
            result[key] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            parsed = _finite(value)
            if parsed is not None:
                result[key] = parsed
    return result


def _hold(row: dict[str, Any], reason: str, model: str) -> dict[str, Any]:
    row["deterministic_decision_verdict"] = row.get("decision_verdict")
    row["decision_verdict"] = "HOLD"
    row["eligible_for_llm"] = False
    row["jev_verdict"] = "HOLD"
    row["jev_reason"] = reason
    row["jev_model"] = model
    return {"symbol": row.get("symbol"), "verdict": "HOLD", "reason": reason}


def verify_candidates(
    rows: list[dict[str, Any]],
    horizon: str,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
    timeout_seconds: int = 25,
    pass_threshold: float = 0.80,
    reject_threshold: float = 0.80,
    confidence_threshold: float = 0.80,
) -> dict[str, Any]:
    """Classify deterministic PASS candidates. Ambiguous results fail closed to HOLD."""
    if not rows:
        return {"status": "no_candidates", "model": model, "evaluated_count": 0,
                "pass_count": 0, "hold_count": 0, "reject_count": 0, "decisions": []}

    api_key = api_key or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        decisions = [_hold(row, "OPENROUTER_API_KEY is not configured.", model) for row in rows]
        return {"status": "missing_api_key", "model": model, "evaluated_count": len(rows),
                "pass_count": 0, "hold_count": len(rows), "reject_count": 0, "decisions": decisions}

    candidates = [_candidate_state(row, horizon) for row in rows]
    questions = {}
    for index in range(len(rows)):
        questions[f"candidate_{index}"] = {
            "type": "choice",
            "instructions": (
                f"Classify candidates[{index}] for escalation to a separate generative research review. "
                "Use only supplied facts. Do not forecast returns, infer missing facts, or recommend trades. "
                "PASS means coherent and supported for further research; REVIEW means uncertain, "
                "mixed, incomplete, or extended; REJECT means materially contradictory or clearly "
                "unsuitable. Return only the best matching choice."
            ),
            "criteria": CRITERIA,
        }

    try:
        response = requests.post(
            DECISIONS_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "state": {"horizon": horizon, "candidates": candidates},
                  "questions": questions},
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        answers = body.get("answers")
        if not isinstance(answers, dict):
            raise ValueError("Jev response has no answers object.")
    except (requests.RequestException, ValueError, TypeError) as exc:
        logger.warning("Jev verification unavailable: %s", exc)
        decisions = [_hold(row, f"Jev verification unavailable: {exc}", model) for row in rows]
        return {"status": "error", "model": model, "error": str(exc),
                "evaluated_count": len(rows), "pass_count": 0,
                "hold_count": len(rows), "reject_count": 0, "decisions": decisions}

    counts = {"PASS": 0, "HOLD": 0, "REJECT": 0}
    decisions = []
    resolved_model = body.get("model", model)
    for index, row in enumerate(rows):
        answer = answers.get(f"candidate_{index}")
        if not isinstance(answer, dict) or answer.get("type") != "choice":
            decisions.append(_hold(row, "Missing or invalid Jev choice answer.", resolved_model))
            counts["HOLD"] += 1
            continue

        choice = str(answer.get("choice", "")).upper()
        probabilities = answer.get("probabilities")
        confidence = _finite(answer.get("confidence"))
        probability = _finite(probabilities.get(choice)) if isinstance(probabilities, dict) else None
        if choice not in CHOICES or confidence is None or probability is None:
            decisions.append(_hold(row, "Jev returned an incomplete or unsupported answer.", resolved_model))
            counts["HOLD"] += 1
            continue
        if not (0 <= confidence <= 1 and 0 <= probability <= 1):
            decisions.append(_hold(row, "Jev returned probabilities outside [0, 1].", resolved_model))
            counts["HOLD"] += 1
            continue

        if choice == "PASS" and probability >= pass_threshold and confidence >= confidence_threshold:
            verdict = "PASS"
        elif choice == "REJECT" and probability >= reject_threshold and confidence >= confidence_threshold:
            verdict = "REJECT"
        else:
            verdict = "HOLD"

        row["deterministic_decision_verdict"] = row.get("decision_verdict")
        row["decision_verdict"] = verdict
        row["eligible_for_llm"] = verdict == "PASS"
        row["jev_verdict"] = choice
        row["jev_probability"] = probability
        row["jev_confidence"] = confidence
        row["jev_reason"] = {"PASS": "Coherent evidence supports escalation to further research.", "REVIEW": "Evidence is mixed, incomplete, extended, or uncertain.", "REJECT": "Evidence is materially contradictory or clearly unsuitable."}[choice]
        row["jev_model"] = resolved_model
        counts[verdict] += 1
        decisions.append({"symbol": row.get("symbol"), "verdict": verdict,
                          "jev_choice": choice, "probability": probability, "confidence": confidence})

    return {
        "status": "complete", "model": resolved_model, "request_id": body.get("id"),
        "evaluated_count": len(rows), "pass_count": counts["PASS"],
        "hold_count": counts["HOLD"], "reject_count": counts["REJECT"],
        "usage": body.get("usage", {}), "decisions": decisions,
        "policy": {"pass_probability_min": pass_threshold,
                   "reject_probability_min": reject_threshold,
                   "confidence_min": confidence_threshold},
        "description": "Research-escalation suitability only; not a return forecast or trade signal.",
    }
