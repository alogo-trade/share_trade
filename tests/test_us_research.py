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
        "status": "partial",
        "instruments": [{"symbol": "AAPL"}],
        "news": [{"symbol": "US_MACRO", "category": "US_MACRO", "title": "Fed rates update"}],
        "news_errors": ["Google News timeout"],
        "ranking": {
            "status": "complete",
            "ranked": [{"symbol": "AAPL"}],
            "shortlist": [{"symbol": "AAPL"}],
            "actionable": [],
            "held": [],
            "rejected": [],
            "unevaluated": [],
            "llm_candidates": [],
        },
    })
    monkeypatch.setattr(engine, "load_universe", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("NSE universe must not load in US_ONLY mode")
    ))

    result = engine.run("SHORT_TERM", settings, age=40, capital_inr=100000.0)

    assert result["metadata"]["market_scope"] == "US_ONLY"
    assert result["evaluated_instruments"] == []
    assert result["us_research"]["instruments"] == [{"symbol": "AAPL"}]
    assert result["market"]["relevant_news"] == [
        {"symbol": "US_MACRO", "category": "US_MACRO", "title": "Fed rates update"}
    ]
    assert result["market"]["news_found"] == 1
    assert result["market"]["news_errors"] == ["Google News timeout"]
    assert result["local_ranking"]["ranked"] == [{"symbol": "AAPL"}]
    assert result["local_ranking"]["shortlist"] == [{"symbol": "AAPL"}]
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



def test_us_research_ranking_keeps_unverified_candidates_out_of_actionable():
    from stock_etf.us_engine import _build_us_ranking

    result = _build_us_ranking([
        {
            "symbol": "AAPL",
            "research_score": 70.0,
            "signal": "POSITIVE",
            "data_status": "latest_available",
            "eligibility_status": "CANDIDATE_UNVERIFIED",
        },
        {
            "symbol": "MSFT",
            "research_score": 65.0,
            "signal": "POSITIVE",
            "data_status": "latest_available",
            "eligibility_status": "ZERODHA_VERIFIED",
        },
        {
            "symbol": "BAD",
            "research_score": None,
            "signal": "INSUFFICIENT_DATA",
            "data_status": "unavailable",
            "eligibility_status": "CANDIDATE_UNVERIFIED",
        },
    ])

    assert [row["symbol"] for row in result["ranked"]] == ["AAPL", "MSFT"]
    assert [row["symbol"] for row in result["shortlist"]] == ["AAPL", "MSFT"]
    assert [row["symbol"] for row in result["actionable"]] == ["MSFT"]
    assert result["ranked"][0]["research_only"] is True
    assert [row["symbol"] for row in result["unevaluated"]] == ["BAD"]


def test_us_metadata_provenance_does_not_claim_provider_reporting_date():
    import pandas as pd
    import stock_etf.us_engine as us_engine

    class FakeTicker:
        history = None

        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, **kwargs):
            return pd.DataFrame(
                {"Close": [100.0, 101.0]},
                index=pd.to_datetime(["2026-10-29", "2026-10-30"]),
            )

        @property
        def info(self):
            return {
                "currency": "USD",
                "sector": "Technology",
                "industry": "Consumer Electronics",
                "marketCap": 1000000,
            }

        @property
        def dividends(self):
            return pd.Series(dtype=float)

        @property
        def news(self):
            return []

    class FakeFundsData:
        top_holdings = pd.DataFrame(
            {"Holding Percent": [0.5]}, index=["Apple Inc"]
        )
        sector_weightings = {"technology": 0.5}
        asset_classes = {"stockPosition": 1.0}

    class FakeETF(FakeTicker):
        @property
        def funds_data(self):
            return FakeFundsData()

    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.setattr(us_engine.yf, "Ticker", lambda symbol: FakeETF(symbol))
    try:
        from pathlib import Path
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            candidate_path = Path(directory) / "candidates.json"
            candidate_path.write_text(json.dumps({"instruments": [
                {"symbol": "TEST", "type": "ETF", "category": "Broad market", "zerodha_eligible": False}
            ]}))
            result = us_engine.run_us_research(
                universe_path=str(Path(directory) / "verified.json"),
                candidate_path=str(candidate_path),
                limit=1,
            )
        row = result["instruments"][0]
        assert row["metadata_observed_at_utc"]
        assert row["metadata_reporting_date"] is None
        assert row["metadata_freshness"] == "REPORTING_DATE_UNAVAILABLE"
        assert row["metadata_provenance"]["sector"]["source"] == "YAHOO_FINANCE"
        assert row["metadata_provenance"]["top_holdings"]["reporting_date"] is None
        assert result["metadata_coverage"]["provenance_observed_count"] == 1
    finally:
        monkeypatch.undo()



def test_hybrid_market_news_merge_deduplicates_india_and_us_articles():
    from stock_etf.engine import _merge_news_items

    merged = _merge_news_items(
        [
            {"title": "Global rates update", "link": "https://example.com/story?utm_source=india"},
            {"title": "India market update", "link": "https://example.com/india"},
        ],
        [
            {"title": "Duplicate global rates update", "link": "https://example.com/story?utm_source=us"},
            {"title": "US market update", "link": "https://example.com/us"},
        ],
    )

    assert len(merged) == 3
    assert {row["link"].split("?")[0] for row in merged} == {
        "https://example.com/story",
        "https://example.com/india",
        "https://example.com/us",
    }



def test_manual_us_symbol_verification_requires_explicit_user_confirmation(tmp_path):
    import pytest
    from stock_etf.us_universe import save_verified_us_instrument

    with pytest.raises(ValueError, match="Explicit confirmation"):
        save_verified_us_instrument(
            tmp_path / "zerodha_us_universe.json",
            {
                "symbol": "AAPL",
                "name": "Apple",
                "type": "STOCK",
                "primary_exchange": "NASDAQ",
            },
            user_confirmed=False,
        )
    assert not (tmp_path / "zerodha_us_universe.json").exists()


def test_manual_us_symbol_verification_upserts_and_loads_verified_rows(tmp_path):
    import json

    from stock_etf.us_engine import load_us_universe
    from stock_etf.us_universe import save_verified_us_instrument

    path = tmp_path / "zerodha_us_universe.json"
    path.write_text(json.dumps({
        "source": "previous manual verification",
        "verified_on": "2026-10-01",
        "instruments": [
            {
                "symbol": "AAPL",
                "name": "Old Apple name",
                "type": "STOCK",
                "zerodha_eligible": True,
            },
            {
                "symbol": "MSFT",
                "name": "Microsoft",
                "type": "STOCK",
                "zerodha_eligible": True,
            },
        ],
    }), encoding="utf-8")

    saved = save_verified_us_instrument(
        path,
        {
            "symbol": "aapl",
            "name": "Apple Inc.",
            "type": "STOCK",
            "primary_exchange": "NASDAQ",
            "category": "Technology hardware",
        },
        user_confirmed=True,
    )

    rows = load_us_universe(str(path))
    assert saved["verification_method"] == "USER_CONFIRMED_IN_KITE_GLOBAL_STOCKS"
    assert [row["symbol"] for row in rows] == ["AAPL", "MSFT"]
    apple = next(row for row in rows if row["symbol"] == "AAPL")
    assert apple["name"] == "Apple Inc."
    assert apple["eligibility_status"] == "ZERODHA_VERIFIED"
    assert apple["zerodha_eligible"] is True
    assert apple["category"] == "Technology hardware"


def test_manual_us_symbol_verification_rejects_invalid_symbols_and_types(tmp_path):
    import pytest
    from stock_etf.us_universe import save_verified_us_instrument

    base = {
        "name": "Test",
        "type": "STOCK",
        "primary_exchange": "NASDAQ",
    }
    with pytest.raises(ValueError, match="valid US ticker"):
        save_verified_us_instrument(
            tmp_path / "universe.json",
            {**base, "symbol": "AAPL/EVIL"},
            user_confirmed=True,
        )
    with pytest.raises(ValueError, match="STOCK or ETF"):
        save_verified_us_instrument(
            tmp_path / "universe.json",
            {**base, "symbol": "AAPL", "type": "OPTION"},
            user_confirmed=True,
        )
    assert not (tmp_path / "universe.json").exists()



def test_manual_us_symbol_verification_ignores_index_membership(tmp_path):
    import json

    from stock_etf.us_universe import save_verified_us_instrument

    path = tmp_path / "zerodha_us_universe.json"
    saved = save_verified_us_instrument(
        path,
        {
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "type": "STOCK",
            "primary_exchange": "NASDAQ",
            "indices": ["S&P 500", "Nasdaq-100"],
            "index_membership": ["S&P 500", "Nasdaq-100"],
            "index_membership_source": "should_not_be_saved",
        },
        user_confirmed=True,
    )

    persisted = json.loads(path.read_text(encoding="utf-8"))["instruments"][0]
    assert saved["symbol"] == "AAPL"
    assert persisted["zerodha_eligible"] is True
    assert "indices" not in persisted
    assert "index_membership" not in persisted
    assert "index_membership_source" not in persisted



def test_us_listing_directory_parser_excludes_test_and_non_common_securities():
    from stock_etf.us_discovery import _parse_directory_file

    nasdaq = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF
AAPL|Apple Inc. - Common Stock|Q|N|N|100|N
BADW|Bad Corp - Warrant|Q|N|N|100|N
TEST|Test Corp - Common Stock|Q|Y|N|100|N
SPY|SPDR S&P 500 ETF Trust|Q|N|N|100|Y
File Creation Time: 010120261200||||||
"""
    rows = _parse_directory_file(nasdaq, nasdaq_listed=True)
    assert set(rows) == {"AAPL", "SPY"}
    assert rows["SPY"]["type"] == "ETF"
    assert rows["AAPL"]["primary_exchange"] == "NASDAQ"


def test_us_other_exchange_directory_parser_maps_exchange_and_etf():
    from stock_etf.us_discovery import _parse_directory_file

    other = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue
XYZ|Example Inc. - Common Stock|N|XYZ|N|100|N
FND|Example Fund - ETF|P|FND|Y|100|N
"""
    rows = _parse_directory_file(other, nasdaq_listed=False)
    assert rows["XYZ"]["primary_exchange"] == "NYSE"
    assert rows["FND"]["type"] == "ETF"
    assert rows["FND"]["primary_exchange"] == "NYSE_ARCA"


def test_dynamic_us_candidates_require_directory_match_and_preserve_screen_metrics():
    from stock_etf.us_discovery import _build_dynamic_candidates

    listings = {
        "AAPL": {"symbol": "AAPL", "name": "Apple", "type": "STOCK", "primary_exchange": "NASDAQ"},
        "SPY": {"symbol": "SPY", "name": "SPDR", "type": "ETF", "primary_exchange": "NYSE_ARCA"},
    }
    rows = _build_dynamic_candidates(
        listings,
        [{"symbol": "AAPL", "marketCap": 3000000000000, "averageDailyVolume3Month": 50000000}],
        [{"symbol": "SPY", "totalAssets": 500000000000, "averageDailyVolume3Month": 80000000}],
    )
    assert {row["symbol"] for row in rows} == {"AAPL", "SPY"}
    assert next(row for row in rows if row["symbol"] == "SPY")["type"] == "ETF"
    assert next(row for row in rows if row["symbol"] == "AAPL")["discovery_metrics"]["market_cap_usd"] == 3000000000000
    assert all(row["zerodha_eligible"] is False for row in rows)


def test_dynamic_discovery_failure_does_not_drop_manually_verified_symbols(tmp_path, monkeypatch):
    import stock_etf.us_discovery as discovery
    import stock_etf.us_engine as us_engine

    verified_path = tmp_path / "verified.json"
    verified_path.write_text(json.dumps({"instruments": [
        {"symbol": "AAPL", "name": "Apple", "type": "STOCK", "zerodha_eligible": True}
    ]}), encoding="utf-8")

    monkeypatch.setattr(discovery, "discover_liquid_us_universe", lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("directory unavailable")
    ))
    class FakeTicker:
        @property
        def news(self):
            return []
        def history(self, **kwargs):
            import pandas as pd
            return pd.DataFrame()
        @property
        def info(self):
            return {"currency": "USD"}
        @property
        def dividends(self):
            import pandas as pd
            return pd.Series(dtype=float)

    monkeypatch.setattr(us_engine.yf, "Ticker", lambda _symbol: FakeTicker())
    result = run_us_research(
        universe_path=str(verified_path),
        limit=1,
        cache_dir=str(tmp_path / "cache"),
        dynamic_discovery=True,
    )
    assert result["instruments"][0]["symbol"] == "AAPL"
    assert result["status"] == "partial"
    assert "only manually verified symbols" in result["status_reason"]
    assert result["discovery"]["dynamic_enabled"] is True
    assert "directory unavailable" in result["discovery"]["error"]
