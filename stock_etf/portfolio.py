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
    details = metadata.get(symbol, {})
    explicit = str(
        holding.get("asset_class") or holding.get("instrument_type")
        or holding.get("type") or details.get("instrument_type")
        or details.get("type") or ""
    ).strip().upper()
    isin = str(holding.get("isin") or "").upper()
    if explicit in {"ETF", "STOCK"}:
        return explicit
    if explicit in {"SGB", "SOVEREIGN_GOLD_BOND"} or isin.startswith("IN0020"):
        return "SOVEREIGN_GOLD_BOND"
    return "UNCLASSIFIED"


def portfolio_summary(
    holdings: dict[str, dict],
    records: list[dict] | None = None,
    universe: list[dict] | None = None,
) -> dict:
    """Summarise every broker holding, including holdings outside the screened universe."""
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
    sector_values = {}
    sector_classified_value = 0.0
    for symbol, holding in holdings.items():
        value = _holding_value(holding)
        asset = _asset_class(symbol, holding, metadata)
        asset_values[asset] = asset_values.get(asset, 0.0) + value
        details = metadata.get(symbol, {})
        if asset == "STOCK":
            sector = str(holding.get("sector") or details.get("sector") or "").strip()
            if sector and sector.lower() not in {"unknown", "none", "nan"}:
                sector_values[sector] = sector_values.get(sector, 0.0) + value
                sector_classified_value += value

    denominator = current_value or 0.0
    asset_pct = {
        key: round(value / denominator * 100, 2) if denominator else 0.0
        for key, value in sorted(asset_values.items(), key=lambda item: item[1], reverse=True)
    }
    sector_pct = {
        key: round(value / denominator * 100, 2) if denominator else 0.0
        for key, value in sorted(sector_values.items(), key=lambda item: item[1], reverse=True)
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
        "asset_class_exposure_pct": asset_pct,
        "sector_exposure_pct": sector_pct,
        "sector_classified_current_value": round(sector_classified_value, 2),
        "sector_unclassified_current_value": round(max(0.0, current_value - sector_classified_value), 2),
    }


def calculate_sector_exposure(
    records: list[dict],
    holdings: dict[str, dict],
    universe: list[dict] | None = None,
) -> dict[str, float]:
    """Calculate stock-sector exposure against total portfolio value, not only classified stocks."""
    metadata = _metadata_by_symbol(records, universe)
    sector_values: dict[str, float] = {}
    total_portfolio_value = sum(_holding_value(item) for item in holdings.values())
    if total_portfolio_value <= 0:
        return {}
    for symbol, holding in holdings.items():
        details = metadata.get(symbol, {})
        asset = _asset_class(symbol, holding, metadata)
        if asset != "STOCK":
            continue
        sector = str(holding.get("sector") or details.get("sector") or "").strip()
        if not sector or sector.lower() in {"unknown", "none", "nan"}:
            continue
        value = _holding_value(holding)
        if value > 0:
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
) -> None:
    """
    Attach ownership and exposure context independently of market-score availability.

    Position percentages use total current value across the complete broker holdings
    payload as denominator. Sector percentages use classified stock holdings only.
    """
    if not records:
        return

    total_portfolio_value = sum(_holding_value(holding) for holding in holdings.values())
    sector_exposure = calculate_sector_exposure(records, holdings, universe)

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
