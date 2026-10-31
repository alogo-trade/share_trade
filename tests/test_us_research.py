import json

from stock_etf.us_engine import (
    _normalise_yield_pct,
    _yield_metadata,
    load_us_candidates,
    load_us_universe,
    run_us_research,
)


def test_provider_yield_is_not_published_without_dividend_history():
    result = _yield_metadata(0.31, None, 100.0, as_of="2026-10-31")
    assert result["dividend_yield_raw_unit"] == "YAHOO_UNIT_UNVERIFIED"
    assert result["dividend_yield_pct"] is None
    assert result["dividend_yield_status"] == "UNVERIFIED_HISTORY_OR_PRICE"


def test_us_ttm_yield_uses_actual_dividend_events():
    result = _yield_metadata(
        0.31,
        [("2026-01-15", 1.0), ("2026-07-15", 1.0)],
        100.0,
        as_of="2026-10-31",
    )
    assert result["dividend_yield_pct"] == 2.0
    assert result["dividend_yield_status"] == "VERIFIED_TTM_CASH_DIVIDENDS"


def test_invalid_provider_yield_does_not_affect_history_calculation():
    result = _yield_metadata(
        float("nan"),
        [("2026-01-15", 0.5)],
        100.0,
        as_of="2026-10-31",
    )
    assert result["dividend_yield_pct"] == 0.5
    assert result["dividend_yield_provider_raw"] is None


def test_normalise_yield_does_not_assume_yahoo_units():
    assert _normalise_yield_pct(0.006) is None
    assert _normalise_yield_pct(0.31) is None
    assert _normalise_yield_pct(1.2) is None


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
    assert instrument["dividend_yield_pct"] is None
    assert instrument["dividend_yield_raw"] == 0.006
    assert instrument["dividend_yield_raw_unit"] == "YAHOO_UNIT_UNVERIFIED"
    assert instrument["dividend_yield_status"] == "UNVERIFIED_HISTORY_OR_PRICE"
    assert instrument["currency"] == "USD"
    assert instrument["price_currency"] == "USD"
    assert instrument["sector"] == "Information Technology"
    assert instrument["sector_metadata_source"] == "CURATED_CATEGORY"
    assert result["status"] == "partial"
    assert result["scope"] == "RESEARCH_ONLY"
    assert result["verified_count"] == 0


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
