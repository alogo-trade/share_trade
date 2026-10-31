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



def test_custom_rule_thresholds_are_applied():
    from stock_etf.decision_model import DecisionRulesConfig

    config = DecisionRulesConfig(
        max_data_age_days=2,
        min_data_confidence=90,
        min_observations_short=75,
        max_rsi_short=80,
        max_distance_sma20_short_pct=15,
    )
    result = evaluate_candidate(
        good(data_points=60, data_confidence=85, rsi14=82),
        "SHORT_TERM",
        date(2026, 10, 31),
        config,
    )
    assert result["verdict"] == "HOLD"
    assert result["features"]["minimum_observations"] == 75
    assert len(result["reasons"]) >= 3


def test_rules_configuration_is_in_batch_summary():
    from stock_etf.decision_model import DecisionRulesConfig

    config = DecisionRulesConfig(max_data_age_days=3)
    summary = evaluate_candidates([good()], "SHORT_TERM", date(2026, 10, 31), config)
    assert summary["rules_config"]["max_data_age_days"] == 3


def test_market_session_age_ignores_weekends():
    from stock_etf.decision_model import _trading_session_age

    assert _trading_session_age(date(2026, 10, 30), date(2026, 11, 2)) == 1
    assert _trading_session_age(date(2026, 10, 29), date(2026, 11, 2)) == 2
    assert _trading_session_age(date(2026, 11, 3), date(2026, 11, 2)) == -1


def test_market_timezone_is_selected_from_exchange_metadata():
    from stock_etf.decision_model import _market_local_date

    fallback = date(2026, 1, 1)
    assert _market_local_date({"exchange": "NSE"}, fallback).isoformat()
    assert _market_local_date({"currency": "USD"}, fallback).isoformat()

