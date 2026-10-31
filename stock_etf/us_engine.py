from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import yfinance as yf

logger = logging.getLogger("stock_etf.us")


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
                row["data_status"] = "live"
            for key in ("marketCap", "sector", "industry", "annualReportExpenseRatio"):
                output_key = {
                    "marketCap": "market_cap_usd",
                    "annualReportExpenseRatio": "expense_ratio",
                }.get(key, key)
                row[output_key] = info.get(key)
            if info.get("dividendYield") is not None:
                row["dividend_yield_pct"] = round(
                    float(info["dividendYield"]) * 100, 2
                )
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
        results.append(row)

    return {
        "status": "ok",
        "configured_count": configured_count,
        "evaluated_count": len(results),
        "verified_count": sum(
            x["eligibility_status"] == "ZERODHA_VERIFIED" for x in results
        ),
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
        "instruments": results,
        "news": news,
    }
