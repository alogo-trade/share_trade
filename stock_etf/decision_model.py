from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

MODEL_VERSION = "transparent-decision-tree-v2"
ELIGIBLE_SIGNALS = {"SETUP", "ACCUMULATE", "QUALITY_CANDIDATE"}


@dataclass(frozen=True)
class DecisionRulesConfig:
    max_data_age_days: int = 7
    min_data_confidence: float = 70.0
    min_observations_short: int = 50
    min_observations_mid: int = 100
    min_observations_long: int = 200
    max_rsi_short: float = 85.0
    max_distance_sma20_short_pct: float = 20.0

    def minimum_observations(self, horizon: str) -> int:
        return {
            "SHORT_TERM": self.min_observations_short,
            "MID_TERM": self.min_observations_mid,
            "LONG_TERM": self.min_observations_long,
        }.get(str(horizon).upper(), self.min_observations_mid)


def rules_config_snapshot(config: DecisionRulesConfig) -> dict[str, Any]:
    return asdict(config)


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except (TypeError, ValueError):
        return None


def evaluate_candidate(
    row: dict[str, Any],
    horizon: str,
    as_of: date | None = None,
    config: DecisionRulesConfig | None = None,
) -> dict[str, Any]:
    """Auditable configurable pre-LLM gate. Checks data suitability, not returns."""
    as_of = as_of or date.today()
    config = config or DecisionRulesConfig()
    reasons: list[str] = []
    signal = str(row.get("signal") or "").upper()
    score = row.get("ranking_score")
    observations = int(row.get("data_points") or 0)
    confidence = float(row.get("data_confidence") or 0)
    last_date = _as_date(row.get("last_date"))
    age_days = (as_of - last_date).days if last_date else None
    min_observations = config.minimum_observations(horizon)

    def result(verdict: str, messages: list[str]) -> dict[str, Any]:
        return {
            "symbol": row.get("symbol"), "model": MODEL_VERSION,
            "verdict": verdict, "eligible_for_llm": verdict == "PASS",
            "reasons": messages,
            "features": {
                "signal": signal, "ranking_score": score, "data_points": observations,
                "minimum_observations": min_observations, "data_confidence": confidence,
                "data_age_days": age_days, "rsi14": row.get("rsi14"),
                "distance_sma20_pct": row.get("distance_sma20_pct"),
            },
            "rules_config": rules_config_snapshot(config),
        }

    if row.get("error") or row.get("data_quality_status") == "ERROR":
        return result("REJECT", ["Market-data error."])
    if score is None and row.get("data_quality_status") in {
        "STALE_OR_UNKNOWN", "INSUFFICIENT_HISTORY", "LOW_CONFIDENCE"
    }:
        return result("HOLD", [f"Data-quality gate failed: {row.get('data_quality_status')}."])
    if score is None:
        return result("REJECT", ["No usable quantitative score."])

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
    elif age_days is not None and (age_days < 0 or age_days > config.max_data_age_days):
        reasons.append(
            f"Market data is stale ({age_days} calendar days old; "
            f"maximum is {config.max_data_age_days})."
        )
        verdict = "HOLD"
    if observations < min_observations:
        reasons.append(
            f"Insufficient history: {observations} observations; "
            f"{min_observations} required for {str(horizon).lower()} research."
        )
        verdict = "HOLD"
    if confidence < config.min_data_confidence:
        reasons.append(
            f"Data confidence is {confidence:.1f}%; "
            f"minimum is {config.min_data_confidence:.1f}%."
        )
        verdict = "HOLD"
    if str(horizon).upper() == "SHORT_TERM":
        rsi = row.get("rsi14")
        extension = row.get("distance_sma20_pct")
        if rsi is not None and float(rsi) > config.max_rsi_short:
            reasons.append(
                f"RSI is extremely overbought ({float(rsi):.1f}; "
                f"maximum is {config.max_rsi_short:.1f})."
            )
            verdict = "HOLD"
        if extension is not None and float(extension) > config.max_distance_sma20_short_pct:
            reasons.append(
                f"Price is excessively extended above SMA20 ({float(extension):.1f}%; "
                f"maximum is {config.max_distance_sma20_short_pct:.1f}%)."
            )
            verdict = "HOLD"
    if verdict == "PASS":
        reasons.append("Passed all deterministic pre-LLM checks.")
    return result(verdict, reasons)


def evaluate_candidates(
    rows: list[dict[str, Any]],
    horizon: str,
    as_of: date | None = None,
    config: DecisionRulesConfig | None = None,
) -> dict[str, Any]:
    config = config or DecisionRulesConfig()
    decisions = [evaluate_candidate(row, horizon, as_of, config) for row in rows]
    by_symbol = {str(item.get("symbol") or "").upper(): item for item in decisions}
    for row in rows:
        decision = by_symbol.get(str(row.get("symbol") or "").upper())
        if decision:
            row["decision_model"] = decision
            row["decision_verdict"] = decision["verdict"]
            row["eligible_for_llm"] = decision["eligible_for_llm"]
    counts = {v: sum(item["verdict"] == v for item in decisions) for v in ("PASS", "HOLD", "REJECT")}
    return {
        "model": MODEL_VERSION,
        "description": "Configurable deterministic pre-LLM data-quality and signal-consistency gate; not a return predictor.",
        "rules_config": rules_config_snapshot(config),
        "evaluated_count": len(decisions), "pass_count": counts["PASS"],
        "hold_count": counts["HOLD"], "reject_count": counts["REJECT"],
        "decisions": decisions,
    }
