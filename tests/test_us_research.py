import json

from stock_etf.us_engine import load_us_candidates, load_us_universe


def test_verified_loader_requires_explicit_eligibility_and_valid_type(tmp_path):
    path = tmp_path / "us.json"
    path.write_text(json.dumps({"instruments": [
        {"symbol": "aapl", "type": "STOCK", "zerodha_eligible": True},
        {"symbol": "SPY", "type": "ETF"},
        {"symbol": "bad", "type": "OTHER", "zerodha_eligible": True},
    ]}))
    assert [x["symbol"] for x in load_us_universe(str(path))] == ["AAPL"]


def test_candidate_loader_excludes_verified_symbols(tmp_path):
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps({"instruments": [
        {"symbol": "spy", "type": "ETF", "zerodha_eligible": False},
        {"symbol": "AAPL", "type": "STOCK", "zerodha_eligible": True},
    ]}))
    rows = load_us_candidates(str(path))
    assert [x["symbol"] for x in rows] == ["SPY"]
    assert rows[0]["eligibility_status"] == "CANDIDATE_UNVERIFIED"


def test_missing_universe_is_empty(tmp_path):
    assert load_us_universe(str(tmp_path / "missing.json")) == []
    assert load_us_candidates(str(tmp_path / "missing.json")) == []


def test_duplicate_symbols_are_deduplicated(tmp_path):
    path = tmp_path / "us.json"
    path.write_text(json.dumps({"instruments": [
        {"symbol": "AAPL", "type": "STOCK", "zerodha_eligible": True},
        {"symbol": "AAPL", "type": "STOCK", "zerodha_eligible": True, "name": "Apple"},
    ]}))
    assert len(load_us_universe(str(path))) == 1
