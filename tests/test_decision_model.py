from datetime import date

from stock_etf.decision_model import evaluate_candidate, evaluate_candidates


def good(**overrides):
    row = {"symbol": "TEST", "signal": "SETUP", "ranking_score": 82.0,
           "data_points": 260, "data_confidence": 95.0, "last_date": "2026-10-30",
           "rsi14": 60.0, "distance_sma20_pct": 4.0}
    row.update(overrides)
    return row


def test_healthy_candidate_passes():
    result = evaluate_candidate(good(), "SHORT_TERM", date(2026, 10, 31))
    assert result["verdict"] == "PASS"
    assert result["eligible_for_llm"] is True


def test_avoid_is_rejected():
    result = evaluate_candidate(good(signal="AVOID"), "SHORT_TERM", date(2026, 10, 31))
    assert result["verdict"] == "REJECT"


def test_stale_data_is_held():
    result = evaluate_candidate(good(last_date="2026-10-01"), "SHORT_TERM", date(2026, 10, 31))
    assert result["verdict"] == "HOLD"
    assert any("stale" in reason.lower() for reason in result["reasons"])


def test_short_history_is_held():
    result = evaluate_candidate(good(data_points=6), "SHORT_TERM", date(2026, 10, 31))
    assert result["verdict"] == "HOLD"


def test_extreme_momentum_is_held():
    result = evaluate_candidate(good(rsi14=96), "SHORT_TERM", date(2026, 10, 31))
    assert result["verdict"] == "HOLD"


def test_mid_term_requires_more_history():
    result = evaluate_candidate(good(data_points=75), "MID_TERM", date(2026, 10, 31))
    assert result["verdict"] == "HOLD"


def test_batch_gate_attaches_decisions():
    rows = [good(), good(symbol="BAD", signal="AVOID")]
    summary = evaluate_candidates(rows, "SHORT_TERM", date(2026, 10, 31))
    assert summary["pass_count"] == 1
    assert summary["reject_count"] == 1
    assert rows[0]["decision_verdict"] == "PASS"
    assert rows[1]["eligible_for_llm"] is False
