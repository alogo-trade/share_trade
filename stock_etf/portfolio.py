from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("stock_etf")


def load_holdings(
    path: str | Path,
) -> dict[str, dict]:
    """
    Load Zerodha equity holdings from the persisted JSON file.

    Returns:
        {
            "SYMBOL": {
                ...holding data...
            }
        }

    An absent or malformed holdings file is treated as an
    unconnected portfolio rather than failing the research engine.
    """

    file_path = Path(path)

    if not file_path.exists():
        logger.info(
            "Portfolio holdings file does not exist: %s",
            file_path,
        )
        return {}

    try:
        raw = json.loads(
            file_path.read_text(
                encoding="utf-8"
            )
        )

    except Exception as exc:
        logger.warning(
            "Could not load holdings from %s: %s",
            file_path,
            exc,
        )
        return {}

    if isinstance(raw, dict):
        items = raw.get(
            "holdings",
            [],
        )
    elif isinstance(raw, list):
        items = raw
    else:
        logger.warning(
            "Unexpected holdings format in %s",
            file_path,
        )
        return {}

    holdings: dict[str, dict] = {}

    for item in items:
        if not isinstance(
            item,
            dict,
        ):
            continue

        symbol = str(
            item.get("symbol")
            or item.get("tradingsymbol")
            or ""
        ).strip().upper()

        if not symbol:
            continue

        holdings[symbol] = item

    logger.info(
        "Loaded %d existing equity holdings.",
        len(holdings),
    )

    return holdings


def portfolio_summary(
    holdings: dict[str, dict],
) -> dict:
    """
    Calculate portfolio-level summary statistics.
    """

    invested_value = sum(
        float(
            holding.get(
                "invested_value",
                0,
            )
            or 0
        )
        for holding in holdings.values()
    )

    current_value = sum(
        float(
            holding.get(
                "current_value",
                0,
            )
            or 0
        )
        for holding in holdings.values()
    )

    pnl = (
        current_value
        - invested_value
    )

    pnl_pct = (
        pnl / invested_value * 100
        if invested_value
        else 0.0
    )

    return {
        "holdings_count": len(
            holdings
        ),

        "invested_value": round(
            invested_value,
            2,
        ),

        "current_value": round(
            current_value,
            2,
        ),

        "pnl": round(
            pnl,
            2,
        ),

        "pnl_pct": round(
            pnl_pct,
            2,
        ),
    }


def calculate_sector_exposure(
    records: list[dict],
    holdings: dict[str, dict],
) -> dict[str, float]:
    """
    Calculate current portfolio exposure by sector.

    Only holdings that can be matched to the research universe
    contribute to sector exposure.
    """

    sector_values: dict[str, float] = {}

    for record in records:
        symbol = str(
            record.get("symbol")
            or ""
        ).strip().upper()

        holding = holdings.get(
            symbol
        )

        if not holding or str(record.get("instrument_type") or "STOCK").upper() == "ETF":
            continue

        current_value = float(
            holding.get(
                "current_value",
                0,
            )
            or 0
        )

        if current_value <= 0:
            continue

        sector = str(record.get("sector") or "").strip()
        if not sector or sector.lower() in {"unknown", "none", "nan"}:
            continue

        sector_values[sector] = (
            sector_values.get(
                sector,
                0.0,
            )
            + current_value
        )

    total_value = sum(
        sector_values.values()
    )

    if total_value <= 0:
        return {}

    return {
        sector: round(
            value / total_value * 100,
            2,
        )
        for sector, value
        in sorted(
            sector_values.items(),
            key=lambda item: item[1],
            reverse=True,
        )
    }


def _holding_value(
    holding: dict | None,
) -> float:
    if not holding:
        return 0.0

    return float(
        holding.get(
            "current_value",
            0,
        )
        or 0
    )


def _holding_pnl_pct(
    holding: dict | None,
) -> float | None:
    if not holding:
        return None

    value = holding.get(
        "pnl_pct"
    )

    if value is None:
        return None

    try:
        return float(value)
    except (
        TypeError,
        ValueError,
    ):
        return None


def apply_portfolio_context(
    records: list[dict],
    holdings: dict[str, dict],
) -> None:
    """
    Add portfolio context to market-analysis records.

    Important design principle:

    The underlying market score is preserved in
    `market_ranking_score`.

    `ranking_score` is then adjusted modestly for existing
    portfolio concentration.

    This allows the application to distinguish:

        market opportunity
        vs.
        portfolio suitability

    without losing the original quantitative signal.

    Portfolio adjustment rules:

    1. Position >= 20% of portfolio:
       apply an incremental concentration penalty.

    2. Sector >= 35% of portfolio:
       apply an incremental sector-concentration penalty.

    The penalties are intentionally capped.
    Portfolio context should influence ranking, not completely
    override market analysis.
    """

    if not records:
        return

    if not holdings:
        for record in records:
            raw_score = record.get(
                "ranking_score"
            )

            if raw_score is None:
                continue

            record[
                "market_ranking_score"
            ] = round(
                float(raw_score),
                1,
            )

            record[
                "portfolio_adjusted_score"
            ] = round(
                float(raw_score),
                1,
            )

            record[
                "portfolio_position_pct"
            ] = 0.0

            record[
                "portfolio_sector_exposure_pct"
            ] = 0.0

            record[
                "portfolio_current_value"
            ] = 0.0

            record[
                "portfolio_pnl_pct"
            ] = None

            record[
                "already_owned"
            ] = False

            record[
                "portfolio_context"
            ] = "NO_PORTFOLIO"

        return

    total_portfolio_value = sum(
        _holding_value(
            holding
        )
        for holding in holdings.values()
    )

    sector_exposure = (
        calculate_sector_exposure(
            records,
            holdings,
        )
    )

    for record in records:
        symbol = str(
            record.get("symbol")
            or ""
        ).strip().upper()

        raw_score = record.get(
            "ranking_score"
        )

        # Ownership is a fact from the broker holdings file, not a
        # consequence of having a market score. Preserve it for every
        # instrument, including rows with missing/stale market data.
        holding = holdings.get(symbol)
        current_value = _holding_value(holding)
        record["already_owned"] = holding is not None
        record["portfolio_current_value"] = round(current_value, 2)
        record["portfolio_pnl_pct"] = _holding_pnl_pct(holding)
        if holding:
            record["portfolio_context"] = "EXISTING_POSITION"
        elif record.get("portfolio_context") is None:
            record["portfolio_context"] = "NEW_EXPOSURE"

        if raw_score is None:
            record.setdefault("portfolio_position_pct", 0.0)
            record.setdefault("portfolio_sector_exposure_pct", 0.0)
            continue

        try:
            raw_score = float(
                raw_score
            )
        except (
            TypeError,
            ValueError,
        ):
            continue

        position_pct = (
            current_value
            / total_portfolio_value
            * 100
            if total_portfolio_value > 0
            else 0.0
        )

        sector = str(record.get("sector") or "").strip()
        sector_applicable = (
            str(record.get("instrument_type") or "STOCK").upper() != "ETF"
            and bool(sector)
            and sector.lower() not in {"unknown", "none", "nan"}
        )
        sector_pct = float(sector_exposure.get(sector, 0.0)) if sector_applicable else 0.0

        penalty = 0.0

        # Individual position concentration.
        if position_pct >= 20.0:
            penalty += min(
                12.0,
                (
                    position_pct
                    - 20.0
                )
                * 0.30,
            )

        # Sector concentration.
        if sector_applicable and sector_pct >= 35.0:
            penalty += min(
                8.0,
                (
                    sector_pct
                    - 35.0
                )
                * 0.20,
            )

        adjusted_score = max(
            0.0,
            raw_score - penalty,
        )

        record[
            "market_ranking_score"
        ] = round(
            raw_score,
            1,
        )

        record[
            "portfolio_adjusted_score"
        ] = round(
            adjusted_score,
            1,
        )

        # ranking.py will continue to use ranking_score.
        record[
            "ranking_score"
        ] = round(
            adjusted_score,
            1,
        )

        record[
            "portfolio_position_pct"
        ] = round(
            position_pct,
            2,
        )

        record[
            "portfolio_sector_exposure_pct"
        ] = round(
            sector_pct,
            2,
        )

        record[
            "portfolio_current_value"
        ] = round(
            current_value,
            2,
        )

        record[
            "portfolio_pnl_pct"
        ] = _holding_pnl_pct(
            holding
        )

        record[
            "already_owned"
        ] = holding is not None

        if holding:
            if position_pct >= 20.0:
                record[
                    "portfolio_context"
                ] = "HIGH_EXISTING_POSITION"

            elif sector_pct >= 35.0:
                record[
                    "portfolio_context"
                ] = "EXISTING_HIGH_SECTOR_EXPOSURE"

            else:
                record[
                    "portfolio_context"
                ] = "EXISTING_POSITION"

        elif sector_applicable and sector_pct >= 35.0:
            record[
                "portfolio_context"
            ] = "HIGH_SECTOR_EXPOSURE"

        else:
            record[
                "portfolio_context"
            ] = "NEW_EXPOSURE"


def build_portfolio_instrument_context(
    records: list[dict],
    holdings: dict[str, dict],
) -> list[dict]:
    """
    Build a compact portfolio-context list suitable for
    diagnostics or API/UI output.
    """

    context = []

    for record in records:
        symbol = str(
            record.get("symbol")
            or ""
        ).strip().upper()

        if not symbol:
            continue

        context.append(
            {
                "symbol": symbol,

                "name": record.get(
                    "name"
                ),

                "sector": record.get(
                    "sector"
                ),

                "already_owned": bool(
                    record.get(
                        "already_owned",
                        symbol in holdings,
                    )
                ),

                "portfolio_position_pct": record.get(
                    "portfolio_position_pct",
                    0.0,
                ),

                "portfolio_sector_exposure_pct": record.get(
                    "portfolio_sector_exposure_pct",
                    0.0,
                ),

                "portfolio_current_value": record.get(
                    "portfolio_current_value",
                    0.0,
                ),

                "portfolio_pnl_pct": record.get(
                    "portfolio_pnl_pct"
                ),

                "portfolio_context": record.get(
                    "portfolio_context",
                    "NO_PORTFOLIO",
                ),

                "market_ranking_score": record.get(
                    "market_ranking_score"
                ),

                "portfolio_adjusted_score": record.get(
                    "portfolio_adjusted_score"
                ),
            }
        )

    return context