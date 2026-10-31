import json
from stock_etf.us_engine import load_us_universe


def test_requires_explicit_eligibility_and_valid_type(tmp_path):
    path = tmp_path / "us.json"
    path.write_text(json.dumps({"instruments": [
        {"symbol": "aapl", "type": "STOCK", "zerodha_eligible": True},
        {"symbol": "SPY", "type": "ETF"},
        {"symbol": "bad", "type": "OTHER", "zerodha_eligible": True},
    ]}))
    assert [x["symbol"] for x in load_us_universe(str(path))] == ["AAPL"]


def test_missing_universe_is_empty(tmp_path):
    assert load_us_universe(str(tmp_path / "missing.json")) == []


def test_duplicate_symbols_are_deduplicated(tmp_path):
    path = tmp_path / "us.json"
    path.write_text(json.dumps({"instruments": [
        {"symbol": "AAPL", "type": "STOCK", "zerodha_eligible": True},
        {"symbol": "AAPL", "type": "STOCK", "zerodha_eligible": True, "name": "Apple"},
    ]}))
    assert len(load_us_universe(str(path))) == 1
