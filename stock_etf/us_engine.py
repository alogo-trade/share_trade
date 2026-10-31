from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import yfinance as yf

logger = logging.getLogger("stock_etf.us")


def load_us_universe(path: str) -> list[dict[str, Any]]:
    """Accept only explicitly verified Zerodha Global Stocks symbols."""
    source = Path(path)
    if not source.exists():
        return []
    payload = json.loads(source.read_text(encoding="utf-8"))
    rows = payload.get("instruments", []) if isinstance(payload, dict) else payload
    accepted = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        symbol = str(raw.get("symbol", "")).strip().upper()
        kind = str(raw.get("type", "")).strip().upper()
        if not symbol or kind not in {"STOCK", "ETF"} or raw.get("zerodha_eligible") is not True:
            continue
        if not all(ch.isalnum() or ch in ".-" for ch in symbol):
            continue
        accepted[symbol] = {**raw, "symbol": symbol, "type": kind}
    return list(accepted.values())


def run_us_research(universe_path: str, history_period: str = "3y", limit: int = 30, cache_dir: str = "cache") -> dict[str, Any]:
    """Research verified US instruments with Yahoo Finance. Does not trade."""
    universe = load_us_universe(universe_path)
    if not universe:
        return {"status": "universe_not_configured", "instruments": [], "news": []}
    if limit > 0:
        universe = universe[:limit]
    results, news = [], []
    for item in universe:
        symbol = item["symbol"]
        row = {"symbol": symbol, "type": item["type"], "name": item.get("name", symbol),
               "data_status": "unavailable", "research_score": None,
               "signal": "INSUFFICIENT_DATA", "currency": "USD",
               "market_data_source": "Yahoo Finance"}
        try:
            ticker = yf.Ticker(symbol)
            history = ticker.history(period=history_period, auto_adjust=True)
            info = ticker.info or {}
            if not history.empty:
                close = history["Close"].dropna()
                latest = float(close.iloc[-1])
                row["price_usd"] = round(latest, 2)
                if len(close) >= 22:
                    row["change_1m_pct"] = round((latest / float(close.iloc[-22]) - 1) * 100, 2)
                if len(close) >= 126:
                    row["change_6m_pct"] = round((latest / float(close.iloc[-126]) - 1) * 100, 2)
                if len(close) >= 253:
                    row["volatility_annual_pct"] = round(float(close.pct_change().dropna().std() * np.sqrt(252) * 100), 2)
                row["data_status"] = "live"
            for key in ("marketCap", "sector", "industry", "annualReportExpenseRatio"):
                row[{"marketCap":"market_cap_usd", "annualReportExpenseRatio":"expense_ratio"}.get(key, key)] = info.get(key)
            if info.get("dividendYield") is not None:
                row["dividend_yield_pct"] = round(float(info["dividendYield"]) * 100, 2)
            one, six = row.get("change_1m_pct"), row.get("change_6m_pct")
            if one is not None or six is not None:
                score = 50 + (one or 0) * (0.35 if item["type"] == "STOCK" else 0.25) + (six or 0) * (0.15 if item["type"] == "STOCK" else 0.15)
                if item["type"] == "ETF" and row.get("expense_ratio") is not None:
                    score -= float(row["expense_ratio"]) * 100
                row["research_score"] = round(max(0, min(100, score)), 1)
                row["signal"] = "POSITIVE" if score >= 60 else "NEUTRAL" if score >= 45 else "CAUTION"
            for article in (getattr(ticker, "news", None) or [])[:5]:
                content = article.get("content", article)
                title = content.get("title")
                if title:
                    news.append({"symbol": symbol, "title": title,
                        "publisher": (content.get("provider") or {}).get("displayName", "Yahoo Finance"),
                        "link": (content.get("canonicalUrl") or {}).get("url", "")})
        except Exception as exc:
            logger.warning("US data unavailable for %s: %s", symbol, exc)
            row["error"] = str(exc)
        results.append(row)
    return {"status": "ok", "universe_source": "Manually verified Zerodha Kite Global Stocks list",
            "price_source": "Yahoo Finance; not Zerodha quotes", "instruments": results, "news": news}
