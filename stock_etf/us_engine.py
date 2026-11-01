from __future__ import annotations

import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

import numpy as np
import yfinance as yf

from .market import _yield_metadata as _shared_yield_metadata
from .news import fetch_us_market_news

logger = logging.getLogger("stock_etf.us")


def _normalise_yield_pct(value: Any) -> float | None:
    """Provider dividendYield is intentionally not scaled without unit proof."""
    return None


def _yield_metadata(
    provider_value: Any,
    dividend_history: Any = None,
    price: Any = None,
    as_of: Any = None,
    history_status: str | None = None,
    history_error: str | None = None,
    price_date: Any = None,
    currency: str | None = None,
) -> dict[str, Any]:
    """Use the shared auditable TTM cash-dividend calculation."""
    return _shared_yield_metadata(
        provider_value,
        dividend_history,
        price,
        as_of=as_of,
        history_status=history_status,
        history_error=history_error,
        price_date=price_date,
        currency=currency,
    )


def _sector_from_category(category: Any, instrument_type: str) -> str | None:
    """Map the curated US watchlist category to a broad sector hint."""
    text = str(category or "").strip().lower()
    kind = str(instrument_type or "").upper()
    if not text:
        return None

    # Order matters for mixed category labels such as e-commerce and cloud.
    rules = [
        ("Consumer Discretionary", ("e-commerce", "automotive", "consumer discretionary")),
        ("Financials", ("financial", "banking", "payment")),
        ("Health Care", ("healthcare", "health care")),
        ("Consumer Staples", ("consumer staples",)),
        ("Communication Services", ("internet", "media", "streaming", "entertainment", "communication")),
        ("Energy", ("energy",)),
        ("Industrials", ("industrial", "aerospace")),
        ("Utilities", ("utilities",)),
        ("Materials", ("materials",)),
        ("Real Estate", ("real estate",)),
        ("Information Technology", (
            "technology", "software", "semiconductor", "cloud", "hardware",
            "networking", "analytics", "robotics", "artificial intelligence",
        )),
    ]
    for sector, phrases in rules:
        if any(phrase in text for phrase in phrases):
            return sector

    # Broad-market, dividend, and thematic ETFs do not imply a single sector.
    if kind == "ETF":
        return None
    return None


def _read_rows(path: str) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    payload = json.loads(source.read_text(encoding="utf-8"))
    rows = payload.get("instruments", []) if isinstance(payload, dict) else payload
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _normalise_rows(rows: list[dict[str, Any]], eligibility: str) -> list[dict[str, Any]]:
    accepted: dict[str, dict[str, Any]] = {}
    for raw in rows:
        symbol = str(raw.get("symbol", "")).strip().upper()
        kind = str(raw.get("type", "")).strip().upper()
        if not symbol or kind not in {"STOCK", "ETF"}:
            continue
        if not all(ch.isalnum() or ch in ".-" for ch in symbol):
            continue
        accepted[symbol] = {
            **raw,
            "symbol": symbol,
            "type": kind,
            "eligibility_status": eligibility,
        }
    return list(accepted.values())


def load_us_universe(path: str) -> list[dict[str, Any]]:
    """Load only instruments explicitly verified as Zerodha-eligible."""
    rows = _read_rows(path)
    return _normalise_rows(
        [row for row in rows if row.get("zerodha_eligible") is True],
        "ZERODHA_VERIFIED",
    )


def load_us_candidates(path: str) -> list[dict[str, Any]]:
    """Load discovery candidates without asserting Zerodha eligibility."""
    rows = _read_rows(path)
    return _normalise_rows(
        [row for row in rows if row.get("zerodha_eligible") is not True],
        "CANDIDATE_UNVERIFIED",
    )


def _dividend_yield_coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize dividend calculation and retrieval coverage without hiding failures."""
    status_counts: dict[str, int] = {}
    history_counts: dict[str, int] = {}
    failed_symbols = []
    for row in rows:
        status = str(row.get("dividend_yield_status") or "MISSING")
        history_status = str(row.get("dividend_yield_history_status") or "UNAVAILABLE")
        status_counts[status] = status_counts.get(status, 0) + 1
        history_counts[history_status] = history_counts.get(history_status, 0) + 1
        if history_status not in {"FETCHED"}:
            failed_symbols.append({
                "symbol": row.get("symbol"),
                "history_status": history_status,
                "reason": row.get("dividend_yield_history_error"),
            })
    return {
        "instrument_count": len(rows),
        "yield_status_counts": status_counts,
        "history_status_counts": history_counts,
        "history_fetch_failure_count": history_counts.get("FETCH_FAILED", 0),
        "history_unavailable_count": history_counts.get("UNAVAILABLE", 0),
        "history_stale_cache_count": history_counts.get("STALE_CACHE", 0),
        "history_circuit_breaker_skip_count": history_counts.get("CIRCUIT_BREAKER_OPEN", 0),
        "history_failure_samples": failed_symbols[:20],
    }



def _dedupe_us_news(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate ticker and broad US news by canonical URL/title."""
    seen = set()
    output = []
    for item in items:
        link = str(item.get("link") or "").split("?")[0].rstrip("/").lower()
        title = " ".join(str(item.get("title") or "").lower().split())
        key = link or title
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _build_us_ranking(instruments: list[dict[str, Any]], shortlist_limit: int = 10) -> dict[str, Any]:
    """Rank US research candidates without conflating research quality and broker eligibility."""
    ranked_rows = []
    unevaluated = []
    for raw in instruments:
        row = dict(raw)
        score = row.get("research_score")
        try:
            score = float(score)
            if not np.isfinite(score):
                raise ValueError("non_finite_score")
        except (TypeError, ValueError):
            unevaluated.append(row)
            continue
        if row.get("data_status") != "latest_available":
            unevaluated.append(row)
            continue
        row["research_rank"] = None
        row["research_only"] = row.get("eligibility_status") != "ZERODHA_VERIFIED"
        ranked_rows.append(row)

    ranked_rows.sort(
        key=lambda row: (
            float(row.get("research_score", 0)),
            str(row.get("symbol", "")),
        ),
        reverse=True,
    )
    for index, row in enumerate(ranked_rows, start=1):
        row["research_rank"] = index

    shortlist = [
        dict(row) for row in ranked_rows
        if row.get("signal") in {"POSITIVE", "NEUTRAL"}
    ][:max(0, int(shortlist_limit))]
    actionable = [
        dict(row) for row in ranked_rows
        if row.get("eligibility_status") == "ZERODHA_VERIFIED"
        and row.get("signal") == "POSITIVE"
    ]
    return {
        "status": "complete" if not unevaluated else "partial",
        "ranked": ranked_rows,
        "shortlist": shortlist,
        # Research-only candidates must never be presented as executable trades.
        "actionable": actionable,
        "held": [],
        "rejected": [],
        "unevaluated": unevaluated,
        "llm_candidates": [],
        "counts": {
            "ranked": len(ranked_rows),
            "shortlist": len(shortlist),
            "actionable": len(actionable),
            "research_only": sum(bool(row.get("research_only")) for row in ranked_rows),
            "unevaluated": len(unevaluated),
        },
    }


def run_us_research(
    universe_path: str,
    history_period: str = "3y",
    limit: int = 30,
    cache_dir: str = "cache",
    candidate_path: str = "data/us_candidate_universe.json",
) -> dict[str, Any]:
    """Research verified and candidate US instruments. Never places trades."""
    verified = load_us_universe(universe_path)
    candidates = load_us_candidates(candidate_path)
    merged = {item["symbol"]: item for item in candidates}
    merged.update({item["symbol"]: item for item in verified})
    # Verified instruments first. Within each eligibility tier, alternate
    # stocks and ETFs so a small limit does not silently exclude ETFs.
    universe = []
    for status in ("ZERODHA_VERIFIED", "CANDIDATE_UNVERIFIED"):
        bucket = [
            item for item in merged.values()
            if item["eligibility_status"] == status
        ]
        by_type = {
            kind: sorted(
                [item for item in bucket if item["type"] == kind],
                key=lambda item: item["symbol"],
            )
            for kind in ("STOCK", "ETF")
        }
        for index in range(max(len(by_type["STOCK"]), len(by_type["ETF"]))):
            for kind in ("STOCK", "ETF"):
                if index < len(by_type[kind]):
                    universe.append(by_type[kind][index])
    configured_count = len(universe)
    if limit > 0:
        universe = universe[:limit]
    if not universe:
        return {
            "status": "universe_not_configured",
            "verified_count": 0,
            "candidate_count": 0,
            "instruments": [],
            "news": [],
        }

    results, news = [], []
    for item in universe:
        symbol = item["symbol"]
        metadata_observed_at = datetime.now(timezone.utc).isoformat()
        row = {
            "symbol": symbol,
            "type": item["type"],
            "name": item.get("name", symbol),
            "category": item.get("category"),
            "primary_exchange": item.get("primary_exchange"),
            "india_inx_exchange_supported": item.get("india_inx_exchange_supported"),
            "india_inx_symbol_verified": item.get("india_inx_symbol_verified", False),
            "eligibility_status": item["eligibility_status"],
            "zerodha_eligible": item["eligibility_status"] == "ZERODHA_VERIFIED",
            "eligibility_notes": item.get("eligibility_notes"),
            "data_status": "unavailable",
            "research_score": None,
            "signal": "INSUFFICIENT_DATA",
            "currency": "USD",
            "market_data_source": "Yahoo Finance",
        }
        try:
            ticker = yf.Ticker(symbol)
            history = ticker.history(period=history_period, auto_adjust=True)
            info = ticker.info or {}
            if not history.empty:
                close = history["Close"].dropna()
                latest = float(close.iloc[-1])
                row["price_usd"] = round(latest, 2)
                if len(close) >= 22:
                    row["change_1m_pct"] = round(
                        (latest / float(close.iloc[-22]) - 1) * 100, 2
                    )
                if len(close) >= 126:
                    row["change_6m_pct"] = round(
                        (latest / float(close.iloc[-126]) - 1) * 100, 2
                    )
                if len(close) >= 253:
                    row["volatility_annual_pct"] = round(
                        float(close.pct_change().dropna().std() * np.sqrt(252) * 100),
                        2,
                    )
                row["data_status"] = "latest_available"
                row["last_market_data_date"] = str(close.index[-1].date())
                row["quote_freshness"] = "NOT_REAL_TIME_VERIFIED"
            currency = str(info.get("currency") or item.get("currency") or "USD").strip().upper()
            yahoo_sector = info.get("sector")
            curated_sector = _sector_from_category(item.get("category"), item.get("type"))
            sector = yahoo_sector or curated_sector
            market_cap = info.get("marketCap")
            row["currency"] = currency
            row["price_currency"] = currency
            if "price_usd" in row:
                row["price"] = row["price_usd"]
                row["price_usd"] = row["price"] if currency == "USD" else None
            row["market_cap_currency"] = currency
            row["market_cap"] = market_cap
            row["market_cap_usd"] = market_cap if currency == "USD" else None
            row["market_cap_metadata_source"] = (
                "YAHOO_FINANCE" if market_cap is not None else "UNAVAILABLE"
            )
            row["sector"] = sector
            row["sector_metadata_source"] = (
                "YAHOO_FINANCE" if yahoo_sector
                else "CURATED_CATEGORY" if curated_sector
                else "UNAVAILABLE"
            )
            row["industry"] = info.get("industry") or item.get("industry")
            row["industry_metadata_source"] = (
                "YAHOO_FINANCE" if info.get("industry")
                else "CURATED_UNIVERSE" if item.get("industry")
                else "UNAVAILABLE"
            )
            expense_ratio = next(
                (
                    info.get(key) for key in (
                        "annualReportExpenseRatio",
                        "netExpenseRatio",
                        "totalExpenseRatio",
                    )
                    if info.get(key) is not None
                ),
                None,
            )
            try:
                expense_ratio = float(expense_ratio) if expense_ratio is not None else None
                if expense_ratio is not None and (not np.isfinite(expense_ratio) or expense_ratio < 0):
                    expense_ratio = None
            except (TypeError, ValueError):
                expense_ratio = None
            row["expense_ratio"] = expense_ratio
            row["expense_ratio_metadata_source"] = (
                "YAHOO_FINANCE" if expense_ratio is not None else "UNAVAILABLE"
            )
            if item["type"] == "ETF":
                try:
                    funds_data = ticker.funds_data
                    if funds_data is not None:
                        top_holdings = funds_data.top_holdings
                        if top_holdings is not None and not top_holdings.empty:
                            row["top_holdings"] = (
                                top_holdings.head(10).reset_index().to_dict(orient="records")
                            )
                            row["top_holdings_metadata_source"] = "YAHOO_FINANCE_FUNDS_DATA"
                        sector_weights = funds_data.sector_weightings
                        if isinstance(sector_weights, dict) and sector_weights:
                            row["sector_weightings"] = sector_weights
                            row["sector_weightings_metadata_source"] = "YAHOO_FINANCE_FUNDS_DATA"
                        asset_classes = funds_data.asset_classes
                        if isinstance(asset_classes, dict) and asset_classes:
                            row["asset_classes"] = asset_classes
                            row["asset_classes_metadata_source"] = "YAHOO_FINANCE_FUNDS_DATA"
                except Exception as funds_exc:
                    row["fund_metadata_error"] = (
                        f"{type(funds_exc).__name__}: {funds_exc}"
                    )[:240]
                    logger.warning(
                        "US ETF fund metadata unavailable for %s: %s",
                        symbol,
                        funds_exc,
                    )
            dividend_history = None
            dividend_history_status = "FETCH_FAILED"
            dividend_history_error = None
            try:
                dividend_series = ticker.dividends
                if dividend_series is None:
                    raise ValueError("Yahoo returned no dividend-history series")
                dividend_history = [
                    (str(event_date)[:10], float(amount))
                    for event_date, amount in dividend_series.items()
                ]
                dividend_history_status = "FETCHED"
            except Exception as dividend_exc:
                dividend_history_error = f"{type(dividend_exc).__name__}: {dividend_exc}"[:240]
                logger.warning("US dividend history unavailable for %s: %s", symbol, dividend_exc)
            row.update(_yield_metadata(
                info.get("dividendYield"),
                dividend_history,
                row.get("price"),
                as_of=history.index[-1] if not history.empty else None,
                history_status=dividend_history_status,
                history_error=dividend_history_error,
                price_date=history.index[-1] if not history.empty else None,
                currency=currency,
            ))
            one, six = row.get("change_1m_pct"), row.get("change_6m_pct")
            if one is not None or six is not None:
                score = (
                    50
                    + (one or 0) * (0.35 if item["type"] == "STOCK" else 0.25)
                    + (six or 0) * 0.15
                )
                if item["type"] == "ETF" and row.get("expense_ratio") is not None:
                    score -= float(row["expense_ratio"]) * 100
                row["research_score"] = round(max(0, min(100, score)), 1)
                row["signal"] = (
                    "POSITIVE" if score >= 60
                    else "NEUTRAL" if score >= 45
                    else "CAUTION"
                )
            for article in (getattr(ticker, "news", None) or [])[:5]:
                content = article.get("content", article)
                title = content.get("title")
                if title:
                    news.append({
                        "symbol": symbol,
                        "title": title,
                        "publisher": (content.get("provider") or {}).get(
                            "displayName", "Yahoo Finance"
                        ),
                        "link": (content.get("canonicalUrl") or {}).get("url", ""),
                    })
        except Exception as exc:
            logger.warning("US data unavailable for %s: %s", symbol, exc)
            row["error"] = str(exc)
        metadata_fields = {
            "sector": ("sector", "sector_metadata_source"),
            "industry": ("industry", "industry_metadata_source"),
            "market_cap": ("market_cap", "market_cap_metadata_source"),
            "expense_ratio": ("expense_ratio", "expense_ratio_metadata_source"),
            "top_holdings": ("top_holdings", "top_holdings_metadata_source"),
            "sector_weightings": ("sector_weightings", "sector_weightings_metadata_source"),
            "asset_classes": ("asset_classes", "asset_classes_metadata_source"),
        }
        row["metadata_observed_at_utc"] = metadata_observed_at
        row["metadata_reporting_date"] = None
        row["metadata_freshness"] = "REPORTING_DATE_UNAVAILABLE"
        row["metadata_provenance"] = {}
        for field, (value_key, source_key) in metadata_fields.items():
            if row.get(value_key) is not None:
                row["metadata_provenance"][field] = {
                    "source": row.get(source_key, "UNAVAILABLE"),
                    "observed_at_utc": metadata_observed_at,
                    "reporting_date": None,
                    "freshness": "REPORTING_DATE_UNAVAILABLE",
                }
        results.append(row)

    # Ticker-specific news can be empty even when broad market events are relevant.
    broad_news, news_failures = fetch_us_market_news(limit=40)
    news = _dedupe_us_news(news + broad_news)
    verified_count = sum(
        x["eligibility_status"] == "ZERODHA_VERIFIED" for x in results
    )
    failed_count = sum(bool(x.get("error")) for x in results)
    expense_ratio_count = sum(
        x.get("expense_ratio") is not None for x in results if x.get("type") == "ETF"
    )
    top_holdings_count = sum(
        bool(x.get("top_holdings")) for x in results if x.get("type") == "ETF"
    )
    sector_weightings_count = sum(
        bool(x.get("sector_weightings")) for x in results if x.get("type") == "ETF"
    )
    asset_classes_count = sum(
        bool(x.get("asset_classes")) for x in results if x.get("type") == "ETF"
    )
    etf_count = sum(x.get("type") == "ETF" for x in results)
    ranking = _build_us_ranking(results)
    return {
        "status": "partial" if verified_count == 0 or failed_count else "ok",
        "ranking": ranking,
        "scope": "RESEARCH_ONLY" if verified_count == 0 else "VERIFIED_AND_RESEARCH",
        "status_reason": (
            "No instruments are explicitly verified as Zerodha-eligible; results are research-only."
            if verified_count == 0
            else "Some instruments failed market-data retrieval." if failed_count
            else "Verified instruments and research candidates were evaluated."
        ),
        "configured_count": configured_count,
        "evaluated_count": len(results),
        "verified_count": verified_count,
        "failed_count": failed_count,
        "candidate_count": sum(
            x["eligibility_status"] == "CANDIDATE_UNVERIFIED" for x in results
        ),
        "universe_sources": {
            "verified": "Manually verified Zerodha Kite Global Stocks list",
            "candidates": (
                "Curated public-market research watchlist; "
                "Zerodha eligibility not confirmed"
            ),
        },
        "price_source": "Yahoo Finance; not Zerodha quotes",
        "dividend_yield_coverage": _dividend_yield_coverage(results),
        "metadata_coverage": {
            "instrument_count": len(results),
            "sector_count": sum(bool(x.get("sector")) for x in results),
            "industry_count": sum(bool(x.get("industry")) for x in results),
            "market_cap_count": sum(x.get("market_cap") is not None for x in results),
            "etf_count": etf_count,
            "etf_expense_ratio_count": expense_ratio_count,
            "etf_expense_ratio_missing_count": max(0, etf_count - expense_ratio_count),
            "etf_top_holdings_count": top_holdings_count,
            "etf_sector_weightings_count": sector_weightings_count,
            "etf_asset_classes_count": asset_classes_count,
            "provenance_reporting_date_available_count": sum(
                bool(row.get("metadata_reporting_date")) for row in results
            ),
            "provenance_observed_count": sum(
                bool(row.get("metadata_observed_at_utc")) for row in results
            ),
            "source_counts": {
                "YAHOO_FINANCE": sum(
                    1 for row in results
                    for key in ("sector_metadata_source", "industry_metadata_source", "expense_ratio_metadata_source")
                    if row.get(key) == "YAHOO_FINANCE"
                ),
                "YAHOO_FINANCE_FUNDS_DATA": sum(
                    1 for row in results
                    for key in ("top_holdings_metadata_source", "sector_weightings_metadata_source", "asset_classes_metadata_source")
                    if row.get(key) == "YAHOO_FINANCE_FUNDS_DATA"
                ),
                "CURATED_CATEGORY": sum(
                    1 for row in results
                    for key in ("sector_metadata_source", "industry_metadata_source")
                    if row.get(key) == "CURATED_CATEGORY"
                ),
            },
        },
        "metadata_provenance_note": (
            "observed_at_utc records retrieval time; Yahoo Finance does not expose "
            "a consistent reporting date for these metadata fields, so they are not "
            "represented as current or independently verified."
        ),
        "news": news,
        "news_count": len(news),
        "news_errors": news_failures[-20:],
        "instruments": results,
    }
