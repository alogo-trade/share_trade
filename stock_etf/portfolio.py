from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("stock_etf")


def load_holdings(
    path: str | Path,
) -> dict[str, dict]:
    """Load Zerodha equity holdings from the persisted JSON file."""
    file_path = Path(path)
    if not file_path.exists():
        logger.info("Portfolio holdings file does not exist: %s", file_path)
        return {}
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not load holdings from %s: %s", file_path, exc)
        return {}

    if isinstance(raw, dict):
        items = raw.get("holdings", raw)
    elif isinstance(raw, list):
        items = raw
    else:
        logger.warning("Unexpected holdings format in %s", file_path)
        return {}

    holdings: dict[str, dict] = {}
    if isinstance(items, dict):
        iterable = []
        for key, value in items.items():
            if isinstance(value, dict):
                item = dict(value)
                item.setdefault("symbol", key)
                iterable.append(item)
    elif isinstance(items, list):
        iterable = items
    else:
        logger.warning("Unexpected holdings collection format in %s", file_path)
        return {}

    for item in iterable:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or item.get("tradingsymbol") or "").strip().upper()
        if symbol:
            holdings[symbol] = item
    logger.info("Loaded %d existing equity holdings.", len(holdings))
    return holdings


def _numeric_value(value, default=0.0) -> float:
    try:
        number = float(value or 0)
        return number if number == number and abs(number) != float("inf") else default
    except (TypeError, ValueError):
        return default


def _metadata_by_symbol(records: list[dict] | None = None, universe: list[dict] | None = None) -> dict[str, dict]:
    """Combine detailed screened records with the complete instrument-universe metadata."""
    metadata = {}
    for row in universe or []:
        symbol = str(row.get("symbol") or "").strip().upper()
        if symbol:
            metadata[symbol] = dict(row)
    for row in records or []:
        symbol = str(row.get("symbol") or "").strip().upper()
        if symbol:
            metadata.setdefault(symbol, {}).update(row)
    return metadata


def _asset_class(symbol: str, holding: dict, metadata: dict) -> str:
    """Classify broker equity holdings even when they are absent from a market universe."""
    details = metadata.get(symbol, {})
    explicit = str(
        holding.get("asset_class") or holding.get("instrument_type")
        or holding.get("type") or details.get("instrument_type")
        or details.get("type") or ""
    ).strip().upper()
    isin = str(holding.get("isin") or "").strip().upper()
    if explicit in {"ETF", "STOCK"}:
        return explicit
    if explicit in {"SGB", "SOVEREIGN_GOLD_BOND"} or isin.startswith("IN0020"):
        return "SOVEREIGN_GOLD_BOND"
    # Zerodha equity holdings use INF-prefixed ISINs for listed fund units/ETFs
    # and INE-prefixed ISINs for company equity. Do not depend on NSE screening.
    if isin.startswith("INF"):
        return "ETF"
    if isin.startswith("INE"):
        return "STOCK"
    return "UNCLASSIFIED"


def load_cached_portfolio_metadata(
    holdings: dict[str, dict],
    cache_dir: str | Path,
) -> list[dict]:
    """Load sector/industry metadata for held stocks from the existing Yahoo cache.

    This is deliberately cache-only: US_ONLY portfolio reporting must not trigger
    NSE discovery or additional Yahoo network requests just to classify holdings.
    """
    fundamentals_dir = Path(cache_dir) / "fundamentals"
    rows = []
    for symbol, holding in holdings.items():
        exchange = str(holding.get("exchange") or "NSE").strip().upper()
        suffixes = (".BO", ".NS") if exchange == "BSE" else (".NS", ".BO")
        yahoo_symbols = [symbol] if "." in symbol else [f"{symbol}{suffix}" for suffix in suffixes]
        payload = None
        for yahoo_symbol in yahoo_symbols:
            cache_path = fundamentals_dir / f"{yahoo_symbol.replace('/', '_')}.json"
            if not cache_path.exists():
                continue
            try:
                candidate = json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError) as exc:
                logger.warning("Could not read cached portfolio metadata for %s (%s): %s", symbol, yahoo_symbol, exc)
                continue
            if isinstance(candidate, dict) and (candidate.get("sector") or candidate.get("industry")):
                payload = candidate
                break
        if payload is None:
            continue
        if not isinstance(payload, dict):
            continue
        sector = str(payload.get("sector") or "").strip()
        industry = str(payload.get("industry") or "").strip()
        if not sector and not industry:
            continue
        row = {
            "symbol": symbol,
            "type": _asset_class(symbol, holding, {}),
            "sector_metadata_source": "YAHOO_FUNDAMENTALS_CACHE",
            "industry_metadata_source": "YAHOO_FUNDAMENTALS_CACHE",
        }
        if sector and sector.lower() not in {"unknown", "none", "nan"}:
            row["sector"] = sector
        if industry and industry.lower() not in {"unknown", "none", "nan"}:
            row["industry"] = industry
        rows.append(row)
    logger.info(
        "Loaded cached portfolio metadata: %d/%d holdings have sector/industry data.",
        len(rows), len(holdings),
    )
    return rows



def load_cached_etf_constituents(
    holdings: dict[str, dict],
    cache_dir: str | Path,
) -> list[dict]:
    """Load validated ETF holdings from cache without making network requests.

    Cache contract: cache/etf_holdings/<SYMBOL>.json with optional source,
    as_of, underlying_asset_class and a holdings/constituents array. Each
    constituent uses weight_pct (0-100), with optional sector and asset_class.
    """
    cache_path = Path(cache_dir) / "etf_holdings"
    rows = []
    allowed_classes = {"EQUITY", "COMMODITY", "FIXED_INCOME", "CASH", "OTHER"}
    for symbol, holding in holdings.items():
        if _asset_class(symbol, holding, {}) != "ETF":
            continue
        path = cache_path / f"{symbol}.json"
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("Could not read ETF holdings cache for %s: %s", symbol, exc)
            continue
        if not isinstance(payload, dict):
            continue

        underlying_class = str(payload.get("underlying_asset_class") or "").strip().upper()
        if underlying_class not in allowed_classes:
            underlying_class = ""
        raw_constituents = payload.get("holdings", payload.get("constituents", []))
        if not isinstance(raw_constituents, list):
            raw_constituents = []

        constituents = []
        total_weight = 0.0
        for item in raw_constituents:
            if not isinstance(item, dict):
                continue
            constituent_symbol = str(item.get("symbol") or item.get("ticker") or "").strip().upper()
            weight = _numeric_value(item.get("weight_pct"), default=-1.0)
            if not constituent_symbol or not 0 < weight <= 100:
                continue
            sector = str(item.get("sector") or "").strip()
            if sector.lower() in {"unknown", "none", "nan"}:
                sector = ""
            asset_class = str(item.get("asset_class") or "").strip().upper()
            if asset_class not in allowed_classes:
                asset_class = "EQUITY" if sector else "UNCLASSIFIED"
            constituents.append({
                "symbol": constituent_symbol,
                "weight_pct": weight,
                "sector": sector or None,
                "asset_class": asset_class,
            })
            total_weight += weight

        if total_weight > 100.5:
            logger.warning(
                "Ignoring ETF holdings cache for %s: constituent weights total %.2f%%.",
                symbol, total_weight,
            )
            continue
        if not constituents and not underlying_class:
            continue
        rows.append({
            "symbol": symbol,
            "source": str(payload.get("source") or "UNSPECIFIED"),
            "as_of": payload.get("as_of"),
            "underlying_asset_class": underlying_class or None,
            "constituents": constituents,
            "constituent_weight_pct": round(total_weight, 4),
        })
    logger.info(
        "Loaded cached ETF look-through data: %d/%d ETF holdings.",
        len(rows),
        sum(_asset_class(symbol, holding, {}) == "ETF" for symbol, holding in holdings.items()),
    )
    return rows


def _etf_sector_values(
    holdings: dict[str, dict],
    etf_constituents: list[dict] | None,
) -> tuple[dict[str, float], dict[str, float], dict]:
    """Return ETF sector values, underlying asset-class values and coverage diagnostics."""
    cache_by_symbol = {
        str(row.get("symbol") or "").strip().upper(): row
        for row in etf_constituents or []
        if row.get("symbol")
    }
    sector_values: dict[str, float] = {}
    underlying_values: dict[str, float] = {}
    etf_symbols = [
        symbol for symbol, holding in holdings.items()
        if _asset_class(symbol, holding, {}) == "ETF"
    ]
    etf_total_value = sum(_holding_value(holdings[symbol]) for symbol in etf_symbols)
    covered_value = 0.0
    sector_attributed_value = 0.0
    constituent_value = 0.0
    sector_attributed_symbols = 0
    for symbol in etf_symbols:
        etf_value = _holding_value(holdings[symbol])
        cached = cache_by_symbol.get(symbol)
        if not cached:
            continue
        covered_value += etf_value
        constituents = cached.get("constituents") or []
        total_weight = 0.0
        symbol_has_sector = False
        for item in constituents:
            weight = _numeric_value(item.get("weight_pct"))
            if weight <= 0:
                continue
            value = etf_value * weight / 100.0
            total_weight += weight
            constituent_value += value
            asset_class = str(item.get("asset_class") or "UNCLASSIFIED").upper()
            underlying_values[asset_class] = underlying_values.get(asset_class, 0.0) + value
            sector = str(item.get("sector") or "").strip()
            if asset_class == "EQUITY" and sector and sector.lower() not in {"unknown", "none", "nan"}:
                sector_values[sector] = sector_values.get(sector, 0.0) + value
                sector_attributed_value += value
                symbol_has_sector = True
        if constituents:
            residual_weight = max(0.0, 100.0 - total_weight)
            if residual_weight > 0:
                residual_class = str(cached.get("underlying_asset_class") or "UNCLASSIFIED").upper()
                residual_value = etf_value * residual_weight / 100.0
                underlying_values[residual_class] = underlying_values.get(residual_class, 0.0) + residual_value
        else:
            asset_class = str(cached.get("underlying_asset_class") or "UNCLASSIFIED").upper()
            underlying_values[asset_class] = underlying_values.get(asset_class, 0.0) + etf_value
        if symbol_has_sector:
            sector_attributed_symbols += 1

    missing_count = len(etf_symbols) - sum(symbol in cache_by_symbol for symbol in etf_symbols)
    diagnostics = {
        "status": (
            "not_applicable" if not etf_symbols
            else "complete" if missing_count == 0
            else "partial"
        ),
        "etf_holdings_count": len(etf_symbols),
        "etf_lookthrough_available_count": len(etf_symbols) - missing_count,
        "etf_lookthrough_missing_count": missing_count,
        "etf_total_current_value": round(etf_total_value, 2),
        "etf_value_with_cached_data": round(covered_value, 2),
        "etf_value_without_cached_data": round(max(0.0, etf_total_value - covered_value), 2),
        "etf_constituent_value_attributed": round(constituent_value, 2),
        "etf_sector_attributed_value": round(sector_attributed_value, 2),
        "etf_sector_attributed_holdings_count": sector_attributed_symbols,
        "cache_sources": [
            {"symbol": symbol, "source": row.get("source"), "as_of": row.get("as_of")}
            for symbol, row in sorted(cache_by_symbol.items())
        ],
        "constituent_weight_coverage_pct": (
            round(constituent_value / etf_total_value * 100, 2)
            if etf_total_value > 0 else 0.0
        ),
    }
    return sector_values, underlying_values, diagnostics

def portfolio_summary(
    holdings: dict[str, dict],
    records: list[dict] | None = None,
    universe: list[dict] | None = None,
    etf_constituents: list[dict] | None = None,
) -> dict:
    """Summarise direct holdings and cached ETF constituent exposure."""
    invested_value = sum(_numeric_value(h.get("invested_value")) for h in holdings.values())
    current_value = sum(_numeric_value(h.get("current_value")) for h in holdings.values())
    pnl = current_value - invested_value
    pnl_pct = pnl / invested_value * 100 if invested_value else 0.0
    metadata = _metadata_by_symbol(records, universe)
    asset_values = {}
    screened_symbols = {
        str(row.get("symbol") or "").strip().upper() for row in records or []
    }
    known_universe_symbols = set(metadata)
    direct_sector_values: dict[str, float] = {}
    direct_sector_classified_value = 0.0
    direct_sector_holdings_count = 0
    for symbol, holding in holdings.items():
        value = _holding_value(holding)
        asset = _asset_class(symbol, holding, metadata)
        asset_values[asset] = asset_values.get(asset, 0.0) + value
        details = metadata.get(symbol, {})
        if asset == "STOCK":
            sector = str(holding.get("sector") or details.get("sector") or "").strip()
            if sector and sector.lower() not in {"unknown", "none", "nan"}:
                direct_sector_values[sector] = direct_sector_values.get(sector, 0.0) + value
                direct_sector_classified_value += value
                direct_sector_holdings_count += 1

    etf_sector_values, etf_underlying_values, etf_coverage = _etf_sector_values(
        holdings, etf_constituents
    )
    sector_values = dict(direct_sector_values)
    for sector, value in etf_sector_values.items():
        sector_values[sector] = sector_values.get(sector, 0.0) + value
    sector_classified_value = direct_sector_classified_value + sum(etf_sector_values.values())
    sector_classified_holdings_count = (
        direct_sector_holdings_count + etf_coverage["etf_sector_attributed_holdings_count"]
    )

    denominator = current_value or 0.0
    def _percentages(values: dict[str, float]) -> dict[str, float]:
        return {
            key: round(value / denominator * 100, 2) if denominator else 0.0
            for key, value in sorted(values.items(), key=lambda item: item[1], reverse=True)
        }

    in_universe = sum(symbol in known_universe_symbols for symbol in holdings)
    in_screen = sum(symbol in screened_symbols for symbol in holdings)
    return {
        "holdings_count": len(holdings),
        "invested_value": round(invested_value, 2),
        "current_value": round(current_value, 2),
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
        "coverage": {
            "holdings_in_full_universe_count": in_universe,
            "holdings_outside_full_universe_count": len(holdings) - in_universe,
            "holdings_in_screened_universe_count": in_screen,
            "holdings_outside_screened_universe_count": len(holdings) - in_screen,
            "asset_classified_holdings_count": sum(
                _asset_class(symbol, holding, metadata) != "UNCLASSIFIED"
                for symbol, holding in holdings.items()
            ),
            "unclassified_holdings_count": sum(
                _asset_class(symbol, holding, metadata) == "UNCLASSIFIED"
                for symbol, holding in holdings.items()
            ),
        },
        "asset_class_exposure_pct": _percentages(asset_values),
        "sector_exposure_pct": _percentages(sector_values),
        "direct_stock_sector_exposure_pct": _percentages(direct_sector_values),
        "etf_lookthrough_sector_exposure_pct": _percentages(etf_sector_values),
        "etf_underlying_asset_class_exposure_pct": _percentages(etf_underlying_values),
        "sector_exposure_method": "DIRECT_STOCKS_PLUS_CACHED_ETF_CONSTITUENTS",
        "sector_classified_current_value": round(sector_classified_value, 2),
        "sector_unclassified_current_value": round(max(0.0, current_value - sector_classified_value), 2),
        "sector_exposure_coverage_pct": (
            round(sector_classified_value / current_value * 100, 2)
            if current_value > 0 else 0.0
        ),
        "sector_classified_holdings_count": sector_classified_holdings_count,
        "sector_unclassified_holdings_count": len(holdings) - sector_classified_holdings_count,
        "etf_lookthrough": etf_coverage,
    }

def calculate_sector_exposure(
    records: list[dict],
    holdings: dict[str, dict],
    universe: list[dict] | None = None,
    etf_constituents: list[dict] | None = None,
) -> dict[str, float]:
    """Calculate direct-stock plus cached ETF sector exposure over total portfolio value."""
    metadata = _metadata_by_symbol(records, universe)
    sector_values: dict[str, float] = {}
    total_portfolio_value = sum(_holding_value(item) for item in holdings.values())
    if total_portfolio_value <= 0:
        return {}
    for symbol, holding in holdings.items():
        details = metadata.get(symbol, {})
        if _asset_class(symbol, holding, metadata) != "STOCK":
            continue
        sector = str(holding.get("sector") or details.get("sector") or "").strip()
        if sector and sector.lower() not in {"unknown", "none", "nan"}:
            value = _holding_value(holding)
            if value > 0:
                sector_values[sector] = sector_values.get(sector, 0.0) + value
    etf_sector_values, _, _ = _etf_sector_values(holdings, etf_constituents)
    for sector, value in etf_sector_values.items():
        sector_values[sector] = sector_values.get(sector, 0.0) + value
    return {
        sector: round(value / total_portfolio_value * 100, 2)
        for sector, value in sorted(sector_values.items(), key=lambda item: item[1], reverse=True)
    }


def _holding_value(holding: dict | None) -> float:
    return _numeric_value(holding.get("current_value")) if holding else 0.0


def _holding_pnl_pct(holding: dict | None) -> float | None:
    if not holding or holding.get("pnl_pct") is None:
        return None
    try:
        value = float(holding["pnl_pct"])
        return value if value == value and abs(value) != float("inf") else None
    except (TypeError, ValueError):
        return None


def apply_portfolio_context(
    records: list[dict],
    holdings: dict[str, dict],
    universe: list[dict] | None = None,
    etf_constituents: list[dict] | None = None,
) -> None:
    """
    Attach ownership and exposure context independently of market-score availability.

    Position percentages use total current value across the complete broker holdings
    payload as denominator. Sector percentages combine direct stocks and cached ETF constituents.
    """
    if not records:
        return

    total_portfolio_value = sum(_holding_value(holding) for holding in holdings.values())
    sector_exposure = calculate_sector_exposure(records, holdings, universe, etf_constituents)

    for record in records:
        symbol = str(record.get("symbol") or "").strip().upper()
        holding = holdings.get(symbol)
        current_value = _holding_value(holding)
        position_pct = (
            current_value / total_portfolio_value * 100
            if total_portfolio_value > 0 else 0.0
        )

        record["already_owned"] = holding is not None
        record["portfolio_current_value"] = round(current_value, 2)
        record["portfolio_pnl_pct"] = _holding_pnl_pct(holding)
        record["portfolio_position_pct"] = round(position_pct, 2)
        record["portfolio_context"] = "EXISTING_POSITION" if holding else (
            "NO_PORTFOLIO" if not holdings else "NEW_EXPOSURE"
        )

        sector = str(record.get("sector") or "").strip()
        sector_applicable = (
            str(record.get("instrument_type") or "STOCK").upper() != "ETF"
            and bool(sector)
            and sector.lower() not in {"unknown", "none", "nan"}
        )
        sector_pct = float(sector_exposure.get(sector, 0.0)) if sector_applicable else 0.0
        record["portfolio_sector_exposure_pct"] = round(sector_pct, 2)

        raw_score = record.get("ranking_score")
        if raw_score is None:
            continue
        try:
            raw_score = float(raw_score)
        except (TypeError, ValueError):
            continue

        # Concentration influences ranking only when a quantitative score exists.
        penalty = 0.0
        if position_pct >= 20.0:
            penalty += min(12.0, (position_pct - 20.0) * 0.30)
        if sector_applicable and sector_pct >= 35.0:
            penalty += min(8.0, (sector_pct - 35.0) * 0.20)
        adjusted_score = max(0.0, raw_score - penalty)

        record["market_ranking_score"] = round(raw_score, 1)
        record["portfolio_adjusted_score"] = round(adjusted_score, 1)
        record["ranking_score"] = round(adjusted_score, 1)

        if holding:
            if position_pct >= 20.0:
                record["portfolio_context"] = "HIGH_EXISTING_POSITION"
            elif sector_pct >= 35.0:
                record["portfolio_context"] = "EXISTING_HIGH_SECTOR_EXPOSURE"
        elif sector_applicable and sector_pct >= 35.0:
            record["portfolio_context"] = "HIGH_SECTOR_EXPOSURE"


def build_portfolio_instrument_context(records: list[dict], holdings: dict[str, dict]) -> list[dict]:
    """Build compact portfolio context suitable for diagnostics or API/UI output."""
    context = []
    for record in records:
        symbol = str(record.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        context.append({
            "symbol": symbol,
            "name": record.get("name"),
            "sector": record.get("sector"),
            "already_owned": bool(record.get("already_owned", symbol in holdings)),
            "portfolio_position_pct": record.get("portfolio_position_pct", 0.0),
            "portfolio_sector_exposure_pct": record.get("portfolio_sector_exposure_pct", 0.0),
            "portfolio_current_value": record.get("portfolio_current_value", 0.0),
            "portfolio_pnl_pct": record.get("portfolio_pnl_pct"),
            "portfolio_context": record.get("portfolio_context", "NO_PORTFOLIO"),
            "market_ranking_score": record.get("market_ranking_score"),
            "portfolio_adjusted_score": record.get("portfolio_adjusted_score"),
        })
    return context
