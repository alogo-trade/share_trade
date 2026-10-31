from stock_etf.jev_verifier import verify_candidates


class FakeResponse:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("bad status")

    def json(self):
        return self._body


def test_jev_pass_updates_only_research_eligibility(monkeypatch):
    import stock_etf.jev_verifier as verifier

    row = {"symbol": "ABC", "decision_verdict": "PASS", "eligible_for_llm": True,
           "signal": "SETUP", "ranking_score": 80, "data_points": 100}
    monkeypatch.setattr(
        verifier.requests,
        "post",
        lambda *args, **kwargs: FakeResponse({
            "model": "typesafe/jev-1.14-20261014",
            "answers": {
                "candidate_0": {
                    "type": "choice", "choice": "PASS",
                    "probabilities": {"PASS": 0.96, "REVIEW": 0.03, "REJECT": 0.01},
                    "confidence": 0.95,
                }
            },
            "id": "test-request",
            "usage": {"input_tokens": 100},
        }),
    )
    result = verify_candidates([row], "SHORT_TERM", api_key="test-key")
    assert result["pass_count"] == 1
    assert row["decision_verdict"] == "PASS"
    assert row["eligible_for_llm"] is True
    assert row["jev_probability"] == 0.96


def test_missing_key_fails_closed(monkeypatch):
    import stock_etf.jev_verifier as verifier

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    row = {"symbol": "ABC", "decision_verdict": "PASS", "eligible_for_llm": True}
    result = verifier.verify_candidates([row], "SHORT_TERM", api_key=None)
    assert result["status"] == "missing_api_key"
    assert row["decision_verdict"] == "HOLD"
    assert row["eligible_for_llm"] is False


def test_low_probability_becomes_hold(monkeypatch):
    import stock_etf.jev_verifier as verifier

    row = {"symbol": "ABC", "decision_verdict": "PASS", "eligible_for_llm": True}
    monkeypatch.setattr(
        verifier.requests,
        "post",
        lambda *args, **kwargs: FakeResponse({
            "answers": {
                "candidate_0": {
                    "type": "choice", "choice": "PASS",
                    "probabilities": {"PASS": 0.72, "REVIEW": 0.20, "REJECT": 0.08},
                    "confidence": 0.92,
                }
            }
        }),
    )
    result = verifier.verify_candidates([row], "SHORT_TERM", api_key="test-key")
    assert result["hold_count"] == 1
    assert row["decision_verdict"] == "HOLD"
