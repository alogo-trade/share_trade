import json

from stock_etf.us_engine import (
    _normalise_yield_pct,
    load_us_candidates,
    load_us_universe,
    run_us_research,
)


def test_yahoo_dividend_yield_ratio_is_converted_to_percentage_points():
    assert _normalise_yield_pct(0.0602) == 6.02
    assert _normalise_yield_pct(0.006) == 0.6
    # Yahoo's dividendYield is a ratio, even when the numeric value exceeds 1.
    assert _normalise_yield_pct(1.2) == 120.0


def test_invalid_yahoo_dividend_yield_values_are_ignored():
    assert _normalise_yield_pct(None) is None
    assert _normalise_yield_pct(True) is None
    assert _normalise_yield_pct(float("nan")) is None
    assert _normalise_yield_pct(float("inf")) is None
    assert _normalise_yield_pct(-0.01) is None
    assert _normalise_yield_pct("not-a-number") is None


def test_us_research_emits_normalized_dividend_yield_from_yahoo(tmp_path, monkeypatch):
    import pandas as pd
    import stock_etf.us_engine as us_engine

    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps({"instruments": [
        {"symbol": "YIELD", "type": "STOCK", "category": "Semiconductors", "zerodha_eligible": False}
    ]}), encoding="utf-8")

    class FakeTicker:
        @property
        def news(self):
            return []

        def history(self, **kwargs):
            return pd.DataFrame()

        @property
        def info(self):
            return {"dividendYield": 0.006}

    monkeypatch.setattr(us_engine.yf, "Ticker", lambda _symbol: FakeTicker())

    result = run_us_research(
        universe_path=str(tmp_path / "verified.json"),
        candidate_path=str(candidates),
        limit=1,
    )

    instrument = result["instruments"][0]
    assert instrument["dividend_yield_pct"] == 0.6
    assert instrument["currency"] == "USD"
    assert instrument["price_currency"] == "USD"
    assert instrument["sector"] == "Information Technology"
    assert instrument["sector_metadata_source"] == "CURATED_CATEGORY"


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


def test_exchange_metadata_is_present_in_candidate_data():
    from pathlib import Path
    from stock_etf.us_engine import load_us_candidates

    path = Path("data/us_candidate_universe.json")
    rows = load_us_candidates(str(path))
    assert rows
    assert all(row.get("primary_exchange") in {"NASDAQ", "NYSE", "NYSE_ARCA"} for row in rows)
    assert all(row.get("india_inx_exchange_supported") is True for row in rows)
    assert all(row.get("india_inx_symbol_verified") is False for row in rows)
    assert all(row.get("zerodha_eligible") is False for row in rows)
