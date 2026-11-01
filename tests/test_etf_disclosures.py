import json
from datetime import date, timedelta

import pandas as pd

from stock_etf.etf_disclosures import (
    _column_index,
    _extract_rows,
    _parse_as_of,
    refresh_etf_disclosure_cache,
)


def test_parse_as_of_handles_full_and_abbreviated_months():
    assert _parse_as_of("Monthly portfolio as on 30 September 2026") == "2026-09-30"
    assert _parse_as_of("Monthly Portfolio Sep 2026.xlsx") == "2026-09-30"
    assert _parse_as_of("Portfolio as on 30th September 2026") == "2026-09-30"
    assert _parse_as_of("period ended September 30, 2026") == "2026-09-30"


def test_column_index_normalises_percent_headers():
    headers = ["Name of Instrument", "ISIN", "% to Net Assets", "Industry"]
    assert _column_index(headers, ("% to net assets", "percent to net assets")) == 2


def test_extract_rows_requires_scheme_match_and_validates_weight_total():
    frame = pd.DataFrame([
        ["Nippon India ETF Nifty 50 BeES", "", "", ""],
        ["Name of Instrument", "ISIN", "% to Net Assets", "Industry"],
        ["Reliance Industries Limited", "INE002A01018", "8.25", "Petroleum Products"],
        ["HDFC Bank Limited", "INE040A01034", "7.50", "Banks"],
    ])
    rows, error = _extract_rows([frame], ("Nippon India ETF Nifty 50 BeES",))
    assert error is None
    assert len(rows) == 2
    assert rows[0]["weight_pct"] == 8.25
    assert rows[0]["sector"] == "Petroleum Products"

    wrong_scheme, error = _extract_rows([frame], ("HDFC Gold ETF",))
    assert wrong_scheme == []
    assert error == "matching_scheme_table_not_found"


def test_fresh_cache_is_not_downloaded_again(tmp_path):
    cache = tmp_path / "etf_holdings"
    cache.mkdir()
    (cache / "NIFTYBEES.json").write_text(json.dumps({
        "source": "https://example.invalid/disclosure.xlsx",
        "as_of": date.today().isoformat(),
        "underlying_asset_class": "EQUITY",
        "holdings": [],
    }), encoding="utf-8")

    result = refresh_etf_disclosure_cache(
        {"NIFTYBEES": {"isin": "INF204KB14I2"}},
        tmp_path,
        max_age_days=40,
    )
    assert result["unchanged"] == 1
    assert result["attempted"] == 0



def test_extract_rows_accepts_isin_free_table_only_with_verified_scheme_context():
    frame = pd.DataFrame([
        ["Motilal Oswal Nasdaq 100 ETF"],
        ["Name of Security", "% to NAV"],
        ["Apple Inc", "7.5"],
        ["Microsoft Corp", "6.0"],
    ])
    rows, error = _extract_rows(
        [frame],
        ("Motilal Oswal Nasdaq 100 ETF",),
        scheme_context_verified=True,
    )
    assert error is None
    assert [row["asset_class"] for row in rows] == ["EQUITY", "EQUITY"]
    assert sum(row["weight_pct"] for row in rows) == 13.5


def test_stale_disclosure_is_never_fresh_even_when_fetched_today(tmp_path):
    from stock_etf.etf_disclosures import _cache_is_fresh

    cache = tmp_path / "old.json"
    old_date = (date.today() - timedelta(days=90)).isoformat()
    cache.write_text(json.dumps({
        "as_of": old_date,
        "fetched_at": date.today().isoformat(),
    }), encoding="utf-8")
    assert not _cache_is_fresh(cache, max_age_days=40)


def test_future_dated_disclosure_is_not_fresh(tmp_path):
    from stock_etf.etf_disclosures import _cache_is_fresh

    cache = tmp_path / "future.json"
    future_date = (date.today() + timedelta(days=1)).isoformat()
    cache.write_text(json.dumps({"as_of": future_date}), encoding="utf-8")
    assert not _cache_is_fresh(cache, max_age_days=40)


def test_month_only_disclosure_date_resolves_to_month_end():
    assert _parse_as_of("Monthly portfolio September 2026") == "2026-09-30"
