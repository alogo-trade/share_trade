from stock_etf.ranking import partition_ranked


def test_ranked_rows_are_partitioned_by_decision_eligibility():
    pass_row = {"symbol": "PASS", "ranking_score": 90, "decision_verdict": "PASS", "eligible_for_llm": True}
    hold_row = {"symbol": "HOLD", "ranking_score": 95, "decision_verdict": "HOLD", "eligible_for_llm": False}
    reject_row = {"symbol": "REJECT", "ranking_score": 99, "decision_verdict": "REJECT", "eligible_for_llm": False}
    unknown_row = {"symbol": "UNKNOWN", "ranking_score": 70}

    result = partition_ranked([pass_row, hold_row, reject_row, unknown_row])

    assert result["actionable"] == [pass_row]
    assert result["held"] == [hold_row]
    assert result["rejected"] == [reject_row]
    assert result["unevaluated"] == [unknown_row]


def test_hold_never_becomes_actionable_even_when_signal_is_positive():
    row = {
        "symbol": "WATCH",
        "ranking_score": 92,
        "signal": "SETUP",
        "decision_verdict": "HOLD",
        "eligible_for_llm": False,
    }

    assert partition_ranked([row])["actionable"] == []
    assert partition_ranked([row])["held"] == [row]
