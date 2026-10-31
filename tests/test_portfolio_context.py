from stock_etf.portfolio import apply_portfolio_context, build_portfolio_instrument_context


def test_owned_instrument_without_market_score_keeps_holding_context():
    records = [
        {"symbol": "HCLTECH", "ranking_score": None, "instrument_type": "STOCK"},
        {"symbol": "TATAPOWER", "ranking_score": 80.0, "instrument_type": "STOCK"},
    ]
    holdings = {
        "HCLTECH": {"current_value": 12500.0, "pnl_pct": 8.5},
        "TATAPOWER": {"current_value": 7500.0, "pnl_pct": 4.0},
    }
    apply_portfolio_context(records, holdings)
    assert records[0]["already_owned"] is True
    assert records[0]["portfolio_current_value"] == 12500.0
    assert records[0]["portfolio_pnl_pct"] == 8.5
    context = {item["symbol"]: item for item in build_portfolio_instrument_context(records, holdings)}
    assert context["HCLTECH"]["already_owned"] is True
    assert context["HCLTECH"]["portfolio_current_value"] == 12500.0


def test_unowned_instrument_is_not_marked_owned_when_score_missing():
    records = [{"symbol": "NEWCO", "ranking_score": None, "instrument_type": "STOCK"}]
    apply_portfolio_context(records, {"HCLTECH": {"current_value": 12500.0}})
    assert records[0]["already_owned"] is False
    assert records[0]["portfolio_current_value"] == 0.0