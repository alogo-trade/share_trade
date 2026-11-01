from stock_etf.portfolio import apply_portfolio_context, build_portfolio_instrument_context


def test_owned_instrument_without_market_score_keeps_holding_context_and_weight():
    records = [
        {"symbol": "HCLTECH", "ranking_score": None, "instrument_type": "STOCK"},
        {"symbol": "TATAPOWER", "ranking_score": 80.0, "instrument_type": "STOCK"},
    ]
    holdings = {
        "HCLTECH": {"current_value": 12500.0, "pnl_pct": 8.5},
        "TATAPOWER": {"current_value": 7500.0, "pnl_pct": 4.0},
        # The portfolio denominator must include broker holdings outside
        # the current research universe as well.
        "UNMATCHED": {"current_value": 10000.0, "pnl_pct": 1.0},
    }
    apply_portfolio_context(records, holdings)

    assert records[0]["already_owned"] is True
    assert records[0]["portfolio_current_value"] == 12500.0
    assert records[0]["portfolio_pnl_pct"] == 8.5
    assert records[0]["portfolio_position_pct"] == 41.67
    context = {
        item["symbol"]: item
        for item in build_portfolio_instrument_context(records, holdings)
    }
    assert context["HCLTECH"]["already_owned"] is True
    assert context["HCLTECH"]["portfolio_current_value"] == 12500.0
    assert context["HCLTECH"]["portfolio_position_pct"] == 41.67


def test_unowned_instrument_is_not_marked_owned_when_score_missing():
    records = [{"symbol": "NEWCO", "ranking_score": None, "instrument_type": "STOCK"}]
    apply_portfolio_context(records, {"HCLTECH": {"current_value": 12500.0}})
    assert records[0]["already_owned"] is False
    assert records[0]["portfolio_current_value"] == 0.0
    assert records[0]["portfolio_position_pct"] == 0.0



def test_portfolio_summary_counts_holdings_outside_screened_universe():
    from stock_etf.portfolio import portfolio_summary

    holdings = {
        "MATCHED": {"current_value": 50.0, "invested_value": 40.0},
        "UNSCREENED_ETF": {"current_value": 30.0, "invested_value": 25.0},
        "UNKNOWN": {"current_value": 20.0, "invested_value": 20.0},
    }
    records = [
        {"symbol": "MATCHED", "instrument_type": "STOCK", "sector": "Technology"}
    ]
    universe = [
        {"symbol": "MATCHED", "type": "STOCK"},
        {"symbol": "UNSCREENED_ETF", "type": "ETF"},
    ]

    summary = portfolio_summary(holdings, records, universe)

    assert summary["holdings_count"] == 3
    assert summary["current_value"] == 100.0
    assert summary["coverage"]["holdings_in_full_universe_count"] == 2
    assert summary["coverage"]["holdings_outside_full_universe_count"] == 1
    assert summary["coverage"]["holdings_in_screened_universe_count"] == 1
    assert summary["coverage"]["holdings_outside_screened_universe_count"] == 2
    assert summary["asset_class_exposure_pct"] == {
        "STOCK": 50.0, "ETF": 30.0, "UNCLASSIFIED": 20.0
    }
    assert summary["sector_exposure_pct"] == {"Technology": 50.0}
    assert summary["sector_unclassified_current_value"] == 50.0


def test_sector_exposure_uses_total_portfolio_denominator():
    from stock_etf.portfolio import calculate_sector_exposure

    holdings = {
        "TECH": {"current_value": 40.0},
        "UNCLASSIFIED": {"current_value": 60.0},
    }
    records = [{"symbol": "TECH", "instrument_type": "STOCK", "sector": "Technology"}]

    assert calculate_sector_exposure(records, holdings) == {"Technology": 40.0}


def test_load_holdings_supports_symbol_keyed_broker_payload(tmp_path):
    import json
    from stock_etf.portfolio import load_holdings

    path = tmp_path / "holdings.json"
    path.write_text(json.dumps({
        "holdings": {
            "HCLTECH": {"current_value": 100.0},
            "GOLDBEES": {"current_value": 200.0},
        }
    }), encoding="utf-8")

    loaded = load_holdings(path)

    assert set(loaded) == {"HCLTECH", "GOLDBEES"}
    assert loaded["HCLTECH"]["symbol"] == "HCLTECH"



def test_portfolio_summary_classifies_zerodha_isins_without_market_universe():
    from stock_etf.portfolio import portfolio_summary

    holdings = {
        "HCLTECH": {"isin": "INE860A01027", "current_value": 60.0, "invested_value": 50.0},
        "GOLDBEES": {"isin": "INF204KB17I5", "current_value": 30.0, "invested_value": 25.0},
        "SGBDC27VII": {"isin": "IN0020190461", "current_value": 10.0, "invested_value": 8.0},
    }

    summary = portfolio_summary(
        holdings,
        universe=[{"symbol": "HCLTECH", "type": "STOCK", "sector": "Technology"}],
    )

    assert summary["coverage"]["asset_classified_holdings_count"] == 3
    assert summary["coverage"]["unclassified_holdings_count"] == 0
    assert summary["asset_class_exposure_pct"] == {
        "STOCK": 60.0,
        "ETF": 30.0,
        "SOVEREIGN_GOLD_BOND": 10.0,
    }
    assert summary["sector_exposure_pct"] == {"Technology": 60.0}
    assert summary["sector_unclassified_current_value"] == 40.0
    assert summary["sector_classified_holdings_count"] == 1
    assert summary["sector_unclassified_holdings_count"] == 2


def test_load_cached_portfolio_metadata_reads_exchange_specific_fundamentals(tmp_path):
    import json
    from stock_etf.portfolio import load_cached_portfolio_metadata

    fundamentals = tmp_path / "fundamentals"
    fundamentals.mkdir()
    (fundamentals / "HCLTECH.NS.json").write_text(
        json.dumps({"sector": "Technology", "industry": "IT Services"}),
        encoding="utf-8",
    )
    (fundamentals / "INDHOTEL.NS.json").write_text(
        json.dumps({"sector": "Consumer Cyclical", "industry": "Hotels"}),
        encoding="utf-8",
    )
    holdings = {
        "HCLTECH": {"exchange": "NSE", "isin": "INE860A01027"},
        "INDHOTEL": {"exchange": "BSE", "isin": "INE053A01029"},
        "GOLDBEES": {"exchange": "NSE", "isin": "INF204KB17I5"},
    }

    rows = load_cached_portfolio_metadata(holdings, tmp_path)
    by_symbol = {row["symbol"]: row for row in rows}

    assert by_symbol["HCLTECH"]["sector"] == "Technology"
    assert by_symbol["INDHOTEL"]["sector"] == "Consumer Cyclical"
    assert by_symbol["HCLTECH"]["type"] == "STOCK"
    assert "GOLDBEES" not in by_symbol



def test_cached_etf_constituents_are_looked_through_into_sector_and_asset_exposure(tmp_path):
    import json
    from stock_etf.portfolio import load_cached_etf_constituents, portfolio_summary

    cache = tmp_path / "etf_holdings"
    cache.mkdir()
    (cache / "NIFTYETF.json").write_text(json.dumps({
        "source": "TEST_EXCHANGE_FILE",
        "as_of": "2026-10-31",
        "underlying_asset_class": "EQUITY",
        "holdings": [
            {"symbol": "AAA", "weight_pct": 60.0, "sector": "Technology", "asset_class": "EQUITY"},
            {"symbol": "BBB", "weight_pct": 30.0, "sector": "Financial Services", "asset_class": "EQUITY"},
        ],
    }), encoding="utf-8")
    (cache / "GOLDETF.json").write_text(json.dumps({
        "source": "TEST_AMC_FACTSHEET",
        "as_of": "2026-10-31",
        "underlying_asset_class": "COMMODITY",
    }), encoding="utf-8")

    holdings = {
        "NIFTYETF": {"isin": "INF000000001", "current_value": 1000.0},
        "GOLDETF": {"isin": "INF000000002", "current_value": 500.0},
        "DIRECT": {"isin": "INE000000001", "current_value": 500.0},
    }
    metadata = [{"symbol": "DIRECT", "type": "STOCK", "sector": "Industrials"}]
    constituents = load_cached_etf_constituents(holdings, tmp_path)
    summary = portfolio_summary(holdings, universe=metadata, etf_constituents=constituents)

    assert summary["asset_class_exposure_pct"] == {"ETF": 75.0, "STOCK": 25.0}
    assert summary["sector_exposure_pct"] == {
        "Technology": 30.0,
        "Industrials": 25.0,
        "Financial Services": 15.0,
    }
    assert summary["direct_stock_sector_exposure_pct"] == {"Industrials": 25.0}
    assert summary["etf_lookthrough_sector_exposure_pct"] == {
        "Technology": 30.0,
        "Financial Services": 15.0,
    }
    assert summary["etf_underlying_asset_class_exposure_pct"] == {
        "EQUITY": 45.0,
        "COMMODITY": 25.0,
    }
    # Only the equity ETF has constituent rows. The gold ETF cache is
    # classification-only and must not inflate constituent look-through coverage.
    assert summary["etf_lookthrough"]["etf_lookthrough_available_count"] == 1
    assert summary["etf_lookthrough"]["etf_lookthrough_missing_count"] == 1
    assert summary["etf_lookthrough"]["etf_classification_only_count"] == 1
    assert summary["etf_lookthrough"]["etf_value_with_cached_data"] == 1000.0
    assert summary["etf_lookthrough"]["etf_value_without_cached_data"] == 500.0
    assert summary["sector_exposure_coverage_pct"] == 70.0


def test_etf_constituent_cache_rejects_invalid_weight_totals(tmp_path):
    import json
    from stock_etf.portfolio import load_cached_etf_constituents

    cache = tmp_path / "etf_holdings"
    cache.mkdir()
    (cache / "BADETF.json").write_text(json.dumps({
        "holdings": [
            {"symbol": "AAA", "weight_pct": 70.0, "sector": "Technology"},
            {"symbol": "BBB", "weight_pct": 40.0, "sector": "Financials"},
        ],
    }), encoding="utf-8")

    rows = load_cached_etf_constituents(
        {"BADETF": {"isin": "INF000000001", "current_value": 100.0}},
        tmp_path,
    )
    assert rows == []


def test_portfolio_sector_exposure_includes_etf_constituents_for_ranking():
    from stock_etf.portfolio import calculate_sector_exposure

    holdings = {
        "NIFTYETF": {"isin": "INF000000001", "current_value": 800.0},
        "DIRECT": {"isin": "INE000000001", "current_value": 200.0},
    }
    constituents = [{
        "symbol": "NIFTYETF",
        "underlying_asset_class": "EQUITY",
        "constituents": [
            {"symbol": "AAA", "weight_pct": 50.0, "sector": "Technology", "asset_class": "EQUITY"},
            {"symbol": "BBB", "weight_pct": 50.0, "sector": "Financials", "asset_class": "EQUITY"},
        ],
    }]
    records = [{"symbol": "DIRECT", "instrument_type": "STOCK", "sector": "Technology"}]

    assert calculate_sector_exposure(records, holdings, etf_constituents=constituents) == {
        "Technology": 60.0,
        "Financials": 40.0,
    }



def test_stale_etf_constituent_cache_is_excluded_from_portfolio_and_ranking(tmp_path):
    import json
    from datetime import date, timedelta
    from stock_etf.portfolio import load_cached_etf_constituents, calculate_sector_exposure, portfolio_summary

    cache = tmp_path / "etf_holdings"
    cache.mkdir()
    stale_date = (date.today() - timedelta(days=90)).isoformat()
    (cache / "STALEETF.json").write_text(json.dumps({
        "source": "OLD_AMC_FILE",
        "as_of": stale_date,
        "underlying_asset_class": "EQUITY",
        "holdings": [
            {"symbol": "OLDTECH", "weight_pct": 100.0, "sector": "Technology", "asset_class": "EQUITY"}
        ],
    }), encoding="utf-8")
    holdings = {"STALEETF": {"isin": "INF000000099", "current_value": 1000.0}}
    constituents = load_cached_etf_constituents(holdings, tmp_path, max_age_days=40)

    assert constituents == []
    summary = portfolio_summary(holdings, etf_constituents=constituents)
    assert summary["etf_lookthrough"]["etf_lookthrough_available_count"] == 0
    assert summary["etf_lookthrough"]["etf_value_without_cached_data"] == 1000.0
    assert calculate_sector_exposure([], holdings, etf_constituents=constituents) == {}
