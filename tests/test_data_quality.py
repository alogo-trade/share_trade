from datetime import date

from stock_etf.scoring import add_scores
from stock_etf.investor import add_position_context
from stock_etf.market import _yield_pct
from stock_etf.portfolio import calculate_sector_exposure


def test_dividend_yield_ratio_and_percent_are_normalised():
    assert _yield_pct(0.0602) == 6.02
    assert _yield_pct(6.02) == 6.02
    assert _yield_pct(None) is None


def test_stale_prices_are_not_rankable():
    rows = [{
        "symbol": "OLD", "instrument_type": "STOCK", "data_points": 500,
        "data_confidence": 100, "last_date": "2026-10-01",
        "return_1w_pct": 5, "return_1m_pct": 10,
        "distance_sma20_pct": 2, "distance_sma50_pct": 3,
        "volume_ratio_20d": 1.5, "rsi14": 60,
    }]
    add_scores(rows, "SHORT_TERM", as_of=date(2026, 10, 31))
    assert rows[0]["signal"] == "STALE_DATA"
    assert rows[0]["ranking_score"] is None


def test_new_listing_does_not_get_actionable_score():
    rows = [{
        "symbol": "NEW", "instrument_type": "STOCK", "data_points": 6,
        "data_confidence": 100, "last_date": "2026-10-30",
        "return_1w_pct": 30, "return_1m_pct": 30,
        "distance_sma20_pct": 20, "distance_sma50_pct": 25,
        "volume_ratio_20d": 2, "rsi14": 95,
    }]
    add_scores(rows, "SHORT_TERM", as_of=date(2026, 10, 31))
    assert rows[0]["signal"] == "LOW_CONFIDENCE"
    assert rows[0]["ranking_score"] is None


def test_position_sizing_only_allocates_passed_setup_rows():
    rows = [
        {"symbol": "PASS", "ranking_score": 80, "decision_verdict": "PASS", "signal": "SETUP", "volatility_pct": 20},
        {"symbol": "AVOID", "ranking_score": 90, "decision_verdict": "REJECT", "signal": "AVOID"},
        {"symbol": "HOLD", "ranking_score": 85, "decision_verdict": "HOLD", "signal": "SETUP"},
    ]
    add_position_context(rows, {"horizon_capital_inr": 150000})
    assert rows[0]["suggested_weight_pct"] == 10.0
    assert rows[0]["position_sizing_status"] == "RISK_BUDGETED"
    assert rows[1]["suggested_weight_pct"] == 0
    assert rows[2]["suggested_weight_pct"] == 0


def test_sector_exposure_excludes_etfs_and_unknown_sectors():
    records = [
        {"symbol": "ABC", "instrument_type": "STOCK", "sector": "Technology"},
        {"symbol": "ETF1", "instrument_type": "ETF", "sector": None},
        {"symbol": "ETF2", "instrument_type": "ETF", "sector": "Technology"},
        {"symbol": "UNK", "instrument_type": "STOCK", "sector": None},
    ]
    holdings = {
        "ABC": {"current_value": 100},
        "ETF1": {"current_value": 500},
        "ETF2": {"current_value": 500},
        "UNK": {"current_value": 100},
    }
    exposure = calculate_sector_exposure(records, holdings)
    assert exposure == {"Technology": 100.0}

def test_stale_cache_is_not_rankable_even_when_market_date_looks_recent():
    rows = [{
        "symbol": "CACHEOLD", "instrument_type": "STOCK", "data_points": 500,
        "data_confidence": 100, "last_date": "2026-10-30",
        "cache_status": "STALE_CACHE",
        "return_1w_pct": 5, "return_1m_pct": 10,
        "distance_sma20_pct": 2, "distance_sma50_pct": 3,
        "volume_ratio_20d": 1.5, "rsi14": 60,
    }]
    add_scores(rows, "SHORT_TERM", as_of=date(2026, 10, 31))
    assert rows[0]["signal"] == "STALE_DATA"
    assert rows[0]["ranking_score"] is None
    assert rows[0]["data_quality_status"] == "STALE_OR_UNKNOWN"



def test_history_download_uses_bounded_request_timeout(tmp_path, monkeypatch):
    import stock_etf.market as market

    calls = []

    class ClosedCircuitBreaker:
        open = False

    def timed_out_download(*args, **kwargs):
        calls.append(kwargs)
        raise TimeoutError("simulated request timeout")

    monkeypatch.setattr(market, "CB", ClosedCircuitBreaker())
    monkeypatch.setattr(market.yf, "download", timed_out_download)

    result = market._history_batch(
        ["CONFIPET.NS"],
        batch_size=1,
        retries=0,
        cache_dir=str(tmp_path),
        request_timeout_seconds=15,
    )

    assert result == {}
    assert len(calls) == 1
    assert calls[0]["timeout"] == 15


def test_liquidity_screen_processes_exact_batch_slice_and_logs_coverage(monkeypatch, caplog):
    import logging
    import pandas as pd
    import stock_etf.market as market

    universe = [
        {"symbol": f"SYM{i}", "type": "STOCK", "exchange": "NSE"}
        for i in range(4)
    ]
    calls = []

    def fake_download(symbols, **kwargs):
        calls.append(list(symbols))
        columns = pd.MultiIndex.from_product([symbols, ["Close", "Volume"]])
        values = [[10.0, 1000.0] for _ in range(len(columns))]
        return pd.DataFrame([values[0]], columns=columns)

    class ClosedCircuitBreaker:
        open = False

    monkeypatch.setattr(market, "CB", ClosedCircuitBreaker())
    monkeypatch.setattr(market.yf, "download", fake_download)

    with caplog.at_level(logging.INFO, logger="stock_etf"):
        result = market.liquidity_screen(
            universe, max_instruments=2, batch_size=2, request_timeout_seconds=3
        )

    assert len(calls) == 2
    assert calls == [["SYM0.NS", "SYM1.NS"], ["SYM2.NS", "SYM3.NS"]]
    assert len(result) == 2
    assert "scored=4/4" in caplog.text


def test_market_metadata_uses_known_exchange_currency_and_preserves_provider_units():
    import pandas as pd
    from stock_etf.market import _merge_record

    frame = pd.DataFrame(
        {"Open": [99.0], "High": [101.0], "Low": [98.0], "Close": [100.0], "Volume": [1000]},
        index=pd.to_datetime(["2026-10-30"]),
    )

    nse = _merge_record(
        {"symbol": "ABC", "type": "STOCK", "exchange": "NSE"},
        frame,
        {},
    )
    assert nse["currency"] == "INR"
    assert nse["price_currency"] == "INR"
    assert nse["market_cap_currency"] == "INR"
    assert nse["sector_metadata_source"] == "UNAVAILABLE"

    provider_metadata = _merge_record(
        {"symbol": "ABC", "type": "STOCK", "exchange": "NSE"},
        frame,
        {"currency": "USD", "marketCap": 1234, "sector": "Technology", "industry": "Software"},
    )
    assert provider_metadata["currency"] == "USD"
    assert provider_metadata["market_cap"] == 1234.0
    assert provider_metadata["market_cap_currency"] == "USD"
    assert provider_metadata["market_cap_inr"] is None
    assert provider_metadata["sector"] == "Technology"
    assert provider_metadata["sector_metadata_source"] == "YAHOO_FINANCE"


def test_position_sizing_uses_volatility_not_ranking_score():
    rows = [
        {"symbol": "LOWVOL", "ranking_score": 60, "decision_verdict": "PASS", "signal": "SETUP", "atr_pct": 2.0},
        {"symbol": "HIGHVOL", "ranking_score": 99, "decision_verdict": "PASS", "signal": "SETUP", "atr_pct": 8.0},
    ]
    add_position_context(rows, {"horizon_capital_inr": 100000})
    assert rows[0]["suggested_weight_pct"] == 10.0
    assert rows[1]["suggested_weight_pct"] == 3.12
    assert rows[0]["suggested_weight_pct"] > rows[1]["suggested_weight_pct"]


def test_position_sizing_caps_total_deployment_and_skips_missing_volatility():
    rows = [
        {"symbol": f"RISK{i}", "decision_verdict": "PASS", "signal": "SETUP", "atr_pct": 1.0}
        for i in range(20)
    ]
    rows.append({"symbol": "NOVOL", "decision_verdict": "PASS", "signal": "SETUP"})
    add_position_context(rows, {"horizon_capital_inr": 100000, "max_deployment_pct": 50})
    assert round(sum(row["suggested_weight_pct"] for row in rows), 2) == 50.0
    assert rows[-1]["suggested_weight_pct"] == 0.0
    assert rows[-1]["position_sizing_status"] == "INSUFFICIENT_VOLATILITY_DATA"

