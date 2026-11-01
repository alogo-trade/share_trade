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
    assert result["dividend_yield_status"] == "HISTORY_UNAVAILABLE"
    assert result["dividend_yield_history_status"] == "UNAVAILABLE"


def test_us_ttm_yield_uses_actual_dividend_events():
    result = _yield_metadata(
        0.31,
        [("2026-01-15", 1.0), ("2026-07-15", 1.0)],
        100.0,
        as_of="2026-10-31",
    )
    assert result["dividend_yield_pct"] == 2.0
    assert result["dividend_yield_status"] == "VERIFIED_TTM_CASH_DIVIDENDS"
    assert result["dividend_yield_ttm_event_count"] == 2
    assert result["dividend_yield_ttm_dividend_amount"] == 2.0
    assert result["dividend_yield_calculation_price"] == 100.0
    assert result["dividend_yield_period_start"] == "2025-10-31"
    assert result["dividend_yield_period_end"] == "2026-10-31"


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
    assert instrument["dividend_yield_status"] == "HISTORY_FETCH_FAILED"
    assert instrument["dividend_yield_history_status"] == "FETCH_FAILED"
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


def test_us_dividend_history_fetch_failure_is_distinct_from_empty_history():
    from stock_etf.us_engine import _yield_metadata

    failed = _yield_metadata(
        0.31, None, 100.0, as_of="2026-10-31",
        history_status="FETCH_FAILED", history_error="TimeoutError: request timed out",
    )
    assert failed["dividend_yield_status"] == "HISTORY_FETCH_FAILED"
    assert failed["dividend_yield_pct"] is None

    empty = _yield_metadata(
        0.31, [], 100.0, as_of="2026-10-31",
        history_status="FETCHED",
    )
    assert empty["dividend_yield_status"] == "NO_DIVIDENDS_IN_TRAILING_365D"
    assert empty["dividend_yield_pct"] == 0.0


def test_us_research_exports_dividend_coverage_and_history_failure(monkeypatch, tmp_path):
    import pandas as pd
    import stock_etf.us_engine as us_engine

    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps({"instruments": [
        {"symbol": "FAIL", "type": "STOCK", "category": "Technology", "zerodha_eligible": False}
    ]}), encoding="utf-8")

    class FakeTicker:
        @property
        def news(self):
            return []

        def history(self, **kwargs):
            return pd.DataFrame(
                {"Close": [100.0, 101.0]},
                index=pd.to_datetime(["2026-10-29", "2026-10-30"]),
            )

        @property
        def info(self):
            return {"dividendYield": 0.5}

        @property
        def dividends(self):
            raise TimeoutError("Yahoo dividend endpoint timed out")

    monkeypatch.setattr(us_engine.yf, "Ticker", lambda _symbol: FakeTicker())
    result = run_us_research(
        universe_path=str(tmp_path / "verified.json"),
        candidate_path=str(candidates),
        limit=1,
    )

    row = result["instruments"][0]
    assert row["dividend_yield_status"] == "HISTORY_FETCH_FAILED"
    assert row["dividend_yield_history_status"] == "FETCH_FAILED"
    assert row["dividend_yield_calculation_price"] == 101.0
    assert result["dividend_yield_coverage"]["history_fetch_failure_count"] == 1
    assert result["dividend_yield_coverage"]["history_failure_samples"][0]["symbol"] == "FAIL"



def test_us_only_mode_skips_nse_pipeline_and_writes_compatible_result(tmp_path, monkeypatch):
    from stock_etf.config import Settings
    import stock_etf.engine as engine

    output = tmp_path / "us_only.json"
    settings = Settings(
        output_path=str(output),
        profile_path=str(tmp_path / "profile.json"),
        holdings_path=str(tmp_path / "holdings.json"),
        cache_dir=str(tmp_path / "cache"),
        us_research_enabled=True,
        market_scope="US_ONLY",
    )
    monkeypatch.setattr(engine, "load_profile", lambda *args: {"age": 40, "capital_inr": 100000.0})
    monkeypatch.setattr(engine, "build_profile", lambda age, capital, horizon: {"age": age, "capital_inr": capital})
    holdings = {
        "HCLTECH": {"isin": "INE860A01027", "exchange": "NSE", "current_value": 60.0, "invested_value": 50.0},
        "GOLDBEES": {"isin": "INF204KB17I5", "exchange": "NSE", "current_value": 30.0, "invested_value": 25.0},
        "SGBDC27VII": {"isin": "IN0020190461", "exchange": "BSE", "current_value": 10.0, "invested_value": 8.0},
    }
    monkeypatch.setattr(engine, "load_holdings", lambda path: holdings)
    monkeypatch.setattr(engine, "load_cached_portfolio_metadata", lambda holdings, cache_dir: [
        {"symbol": "HCLTECH", "type": "STOCK", "sector": "Technology"}
    ])
    monkeypatch.setattr(engine, "run_us_research", lambda *args: {
        "status": "partial", "instruments": [{"symbol": "AAPL"}], "news": []
    })
    monkeypatch.setattr(engine, "load_universe", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("NSE universe must not load in US_ONLY mode")
    ))

    result = engine.run("SHORT_TERM", settings, age=40, capital_inr=100000.0)

    assert result["metadata"]["market_scope"] == "US_ONLY"
    assert result["evaluated_instruments"] == []
    assert result["us_research"]["instruments"] == [{"symbol": "AAPL"}]
    assert result["local_ranking"]["ranked"] == []
    portfolio = result["portfolio"]["summary"]
    assert portfolio["holdings_count"] == 3
    assert portfolio["coverage"]["asset_classified_holdings_count"] == 3
    assert portfolio["coverage"]["unclassified_holdings_count"] == 0
    assert portfolio["asset_class_exposure_pct"] == {
        "STOCK": 60.0, "ETF": 30.0, "SOVEREIGN_GOLD_BOND": 10.0
    }
    assert portfolio["sector_exposure_pct"] == {"Technology": 60.0}
    assert output.exists()




def test_india_only_scope_skips_us_research(monkeypatch):
    from stock_etf.config import Settings
    from stock_etf.engine import _run_us_research_for_scope

    settings = Settings(market_scope="INDIA_ONLY", us_research_enabled=True)
    monkeypatch.setattr("stock_etf.engine.run_us_research", lambda *args: (_ for _ in ()).throw(
        AssertionError("US research must not run in INDIA_ONLY mode")
    ))

    result = _run_us_research_for_scope(settings, "INDIA_ONLY")

    assert result["status"] == "skipped"
    assert result["instruments"] == []


def test_us_research_disabled_is_respected():
    from stock_etf.config import Settings
    from stock_etf.engine import _run_us_research_for_scope

    result = _run_us_research_for_scope(Settings(us_research_enabled=False), "HYBRID")
    assert result["status"] == "disabled"
