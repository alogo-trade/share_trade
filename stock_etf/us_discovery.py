from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
import yfinance as yf

logger = logging.getLogger("stock_etf.us.discovery")

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"
USER_AGENT = "share-trade-us-universe/1.0"
CACHE_MAX_AGE_HOURS = 24
CACHE_MAX_STALE_DAYS = 7
PAGE_SIZE = 250

EXCHANGE_NAMES = {
    "Q": "NASDAQ",
    "G": "NASDAQ",
    "S": "NASDAQ",
    "N": "NYSE",
    "A": "NYSE_AMERICAN",
    "P": "NYSE_ARCA",
    "Z": "CBOE_BATS",
    "V": "IEX",
}
NON_COMMON_STOCK = re.compile(
    r"\\b(warrant|right|unit|preferred|depositary|debenture|note|bond|"
    r"when-issued|subscription receipt)\\b",
    re.IGNORECASE,
)


def _parse_directory_file(content: str, *, nasdaq_listed: bool) -> dict[str, dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(content), delimiter="|")
    rows: dict[str, dict[str, Any]] = {}
    for raw in reader:
        symbol = str(
            raw.get("Symbol") if nasdaq_listed else
            (raw.get("CQS Symbol") or raw.get("ACT Symbol")) or ""
        ).strip().upper()
        if not symbol or symbol.startswith("File Creation Time"):
            continue
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\\-]{0,14}", symbol):
            continue
        test_issue = str(raw.get("Test Issue") or "N").strip().upper()
        if test_issue == "Y":
            continue
        security_name = str(raw.get("Security Name") or "").strip()
        if not security_name:
            continue

        is_etf = str(raw.get("ETF") or "N").strip().upper() == "Y"
        if not is_etf and NON_COMMON_STOCK.search(security_name):
            continue

        if nasdaq_listed:
            exchange_code = str(raw.get("Market Category") or "").strip().upper()
            exchange = "NASDAQ"
            financial_status = str(raw.get("Financial Status") or "N").strip().upper()
            if financial_status not in {"", "N"}:
                continue
        else:
            exchange_code = str(raw.get("Exchange") or "").strip().upper()
            exchange = EXCHANGE_NAMES.get(exchange_code, "OTHER")
        rows[symbol] = {
            "symbol": symbol,
            "name": security_name,
            "type": "ETF" if is_etf else "STOCK",
            "primary_exchange": exchange,
            "exchange_code": exchange_code,
            "listing_source": "NASDAQ_TRADER_SYMBOL_DIRECTORY",
        }
    return rows


def load_us_listing_directory(timeout: int = 20) -> dict[str, dict[str, Any]]:
    """Load current Nasdaq- and other-exchange-listed securities."""
    headers = {"User-Agent": USER_AGENT}
    nasdaq_response = requests.get(NASDAQ_LISTED_URL, headers=headers, timeout=timeout)
    nasdaq_response.raise_for_status()
    other_response = requests.get(OTHER_LISTED_URL, headers=headers, timeout=timeout)
    other_response.raise_for_status()

    listings = _parse_directory_file(nasdaq_response.text, nasdaq_listed=True)
    listings.update(_parse_directory_file(other_response.text, nasdaq_listed=False))
    if not listings:
        raise RuntimeError("Nasdaq Trader symbol directory returned no usable listings.")
    return listings


def _screen_query(instrument_type: str):
    """Build conservative liquidity and size filters for the two instrument classes."""
    if instrument_type == "STOCK":
        query = yf.EquityQuery
        return query("and", [
            query("eq", ["region", "us"]),
            query("gte", ["intradaymarketcap", float(os.getenv("US_MIN_MARKET_CAP_USD", "2000000000"))]),
            query("gte", ["avgdailyvol3m", float(os.getenv("US_MIN_AVG_DAILY_VOLUME", "500000"))]),
            query("gte", ["intradayprice", float(os.getenv("US_MIN_SHARE_PRICE_USD", "5"))]),
        ])
    query = yf.ETFQuery
    return query("and", [
        query("eq", ["region", "us"]),
        query("gte", ["fundnetassets", float(os.getenv("US_MIN_ETF_ASSETS_USD", "500000000"))]),
        query("gte", ["avgdailyvol3m", float(os.getenv("US_MIN_ETF_AVG_DAILY_VOLUME", "250000"))]),
        query("gte", ["intradayprice", float(os.getenv("US_MIN_ETF_PRICE_USD", "10"))]),
    ])


def _screen_quotes(instrument_type: str, max_results: int) -> list[dict[str, Any]]:
    query = _screen_query(instrument_type)
    quotes: list[dict[str, Any]] = []
    for offset in range(0, max_results, PAGE_SIZE):
        response = yf.screen(
            query,
            offset=offset,
            size=min(PAGE_SIZE, max_results - offset),
            sortField="avgdailyvol3m",
            sortAsc=False,
        )
        page = response.get("quotes", []) if isinstance(response, dict) else []
        if not isinstance(page, list):
            raise RuntimeError(f"Yahoo screener returned invalid {instrument_type} results.")
        quotes.extend(row for row in page if isinstance(row, dict))
        if len(page) < PAGE_SIZE:
            break
    return quotes


def _quote_number(quote: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = quote.get(key)
        try:
            number = float(value)
            if number == number and number not in (float("inf"), float("-inf")):
                return number
        except (TypeError, ValueError):
            continue
    return None


def _build_dynamic_candidates(
    listings: dict[str, dict[str, Any]],
    stock_quotes: list[dict[str, Any]],
    etf_quotes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for instrument_type, quotes in (("STOCK", stock_quotes), ("ETF", etf_quotes)):
        for quote in quotes:
            symbol = str(quote.get("symbol") or "").strip().upper()
            listing = listings.get(symbol)
            if not symbol or not listing or listing["type"] != instrument_type:
                continue
            market_cap = _quote_number(quote, "marketCap", "intradayMarketCap")
            fund_assets = _quote_number(quote, "totalAssets", "fundNetAssets")
            average_volume = _quote_number(
                quote, "averageDailyVolume3Month", "avgDailyVol3M", "averageDailyVolume10Day"
            )
            price = _quote_number(quote, "regularMarketPrice", "regularMarketPreviousClose")
            row = {
                **listing,
                "name": quote.get("longName") or quote.get("shortName") or listing["name"],
                "zerodha_eligible": False,
                "eligibility_status": "CANDIDATE_UNVERIFIED",
                "currency": quote.get("currency") or "USD",
                "discovery_source": "NASDAQ_TRADER_PLUS_YAHOO_FINANCE_SCREENER",
                "discovery_metrics": {
                    "market_cap_usd": market_cap,
                    "fund_net_assets_usd": fund_assets,
                    "average_daily_volume_3m": average_volume,
                    "last_price_usd": price,
                },
            }
            selected[symbol] = row
    return sorted(
        selected.values(),
        key=lambda row: (
            row["type"],
            -(row["discovery_metrics"]["average_daily_volume_3m"] or 0),
            row["symbol"],
        ),
    )


def _write_cache(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _read_cache(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("instruments"), list):
        return None
    return payload


def discover_liquid_us_universe(
    cache_path: str | Path = "cache/us_discovered_universe.json",
    *,
    refresh: bool = False,
    max_results_per_type: int | None = None,
) -> dict[str, Any]:
    """Discover current US listings and return dynamically screened liquid stocks and ETFs.

    A fresh cache is used for up to 24 hours. If a refresh fails, a cache up to
    seven days old may be used with an explicit stale status. No static ticker
    watchlist is used as a discovery fallback.
    """
    path = Path(cache_path)
    now = datetime.now(timezone.utc)
    cached = _read_cache(path)
    if cached and not refresh:
        try:
            cached_at = datetime.fromisoformat(str(cached.get("refreshed_at_utc")))
            if cached_at.tzinfo is None:
                cached_at = cached_at.replace(tzinfo=timezone.utc)
            if now - cached_at <= timedelta(hours=CACHE_MAX_AGE_HOURS):
                return {**cached, "status": "cached"}
        except (TypeError, ValueError):
            pass

    max_results = max_results_per_type or int(os.getenv("US_DISCOVERY_MAX_RESULTS_PER_TYPE", "1000"))
    max_results = max(1, min(max_results, 5000))
    try:
        listings = load_us_listing_directory()
        stock_quotes = _screen_quotes("STOCK", max_results)
        etf_quotes = _screen_quotes("ETF", max_results)
        instruments = _build_dynamic_candidates(listings, stock_quotes, etf_quotes)
        if not instruments:
            raise RuntimeError(
                "Dynamic US screening returned no listings after exchange-directory validation."
            )
        payload = {
            "status": "refreshed",
            "refreshed_at_utc": now.isoformat(),
            "source": "NASDAQ_TRADER_SYMBOL_DIRECTORY_AND_YAHOO_FINANCE_SCREENER",
            "directory_listing_count": len(listings),
            "directory_stock_count": sum(x["type"] == "STOCK" for x in listings.values()),
            "directory_etf_count": sum(x["type"] == "ETF" for x in listings.values()),
            "screened_stock_count": len(stock_quotes),
            "screened_etf_count": len(etf_quotes),
            "candidate_count": len(instruments),
            "screening_thresholds": {
                "stock_min_market_cap_usd": float(os.getenv("US_MIN_MARKET_CAP_USD", "2000000000")),
                "stock_min_avg_daily_volume": float(os.getenv("US_MIN_AVG_DAILY_VOLUME", "500000")),
                "stock_min_price_usd": float(os.getenv("US_MIN_SHARE_PRICE_USD", "5")),
                "etf_min_assets_usd": float(os.getenv("US_MIN_ETF_ASSETS_USD", "500000000")),
                "etf_min_avg_daily_volume": float(os.getenv("US_MIN_ETF_AVG_DAILY_VOLUME", "250000")),
                "etf_min_price_usd": float(os.getenv("US_MIN_ETF_PRICE_USD", "10")),
            },
            "instruments": instruments,
        }
        _write_cache(path, payload)
        return payload
    except Exception as exc:
        logger.warning("Dynamic US universe refresh failed: %s", exc, exc_info=True)
        if cached:
            try:
                cached_at = datetime.fromisoformat(str(cached.get("refreshed_at_utc")))
                if cached_at.tzinfo is None:
                    cached_at = cached_at.replace(tzinfo=timezone.utc)
                if now - cached_at <= timedelta(days=CACHE_MAX_STALE_DAYS):
                    return {
                        **cached,
                        "status": "stale_cache",
                        "refresh_error": f"{type(exc).__name__}: {exc}"[:300],
                    }
            except (TypeError, ValueError):
                pass
        raise RuntimeError(f"Dynamic US universe discovery failed: {exc}") from exc
