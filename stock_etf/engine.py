from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .investor import add_position_context, build_profile
from .llm import review
from .market import build_market_data, liquidity_screen
from .news import fetch_news, search_links
from .portfolio import (
    apply_portfolio_context,
    build_portfolio_instrument_context,
    load_holdings,
    portfolio_summary,
)
from .profile_store import load_profile
from .ranking import diversify, rank
from .retrieval import retrieve_news
from .review import local_challenge
from .scoring import add_scores
from .decision_model import evaluate_candidates
from .universe import load_universe
from .us_engine import run_us_research

logger = logging.getLogger("stock_etf")


def run(
    horizon: str = "LONG_TERM",
    settings: Settings | None = None,
    age: int = 38,
    capital_inr: float = 1_000_000,
) -> dict:
    """
    Run the complete stock / ETF research pipeline.

    Portfolio awareness is deliberately applied after the underlying
    market scoring has been calculated.

    This preserves two separate concepts:

        market_ranking_score
            The original market/fundamental ranking.

        portfolio_adjusted_score
            The ranking after considering existing portfolio exposure.

    The raw market score remains available for auditability.
    """

    settings = settings or Settings()

    # ------------------------------------------------------------------
    # Investor profile
    # ------------------------------------------------------------------

    stored_profile = load_profile(
        settings.profile_path,
        age,
        capital_inr,
    )

    age = stored_profile["age"]
    capital_inr = stored_profile["capital_inr"]

    profile = build_profile(
        age,
        capital_inr,
        horizon,
    )

    # ------------------------------------------------------------------
    # Existing Zerodha portfolio
    # ------------------------------------------------------------------

    holdings = load_holdings(
        settings.holdings_path
    )

    portfolio_info = portfolio_summary(
        holdings
    )

    logger.info(
        "Portfolio loaded: %d holdings, current value ₹%.2f",
        portfolio_info["holdings_count"],
        portfolio_info["current_value"],
    )

    # ------------------------------------------------------------------
    # NSE universe
    # ------------------------------------------------------------------

    universe = load_universe(
        settings.universe_path,
        settings.nse_universe_cache,
        refresh=True,
    )

    discovered = len(
        universe
    )

    if (
        settings.analysis_universe_limit > 0
        and len(universe)
        > settings.analysis_universe_limit
    ):
        universe = liquidity_screen(
            universe,
            settings.analysis_universe_limit,
            settings.max_workers,
        )

    logger.info(
        "Universe: %d instruments (%d stocks, %d ETFs).",
        len(universe),
        sum(
            x.get("type") == "STOCK"
            for x in universe
        ),
        sum(
            x.get("type") == "ETF"
            for x in universe
        ),
    )

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    records = build_market_data(
        universe,
        settings.history_period,
        settings.max_workers,
        settings.cache_dir,
    )

    # ------------------------------------------------------------------
    # Quantitative scoring
    # ------------------------------------------------------------------

    add_scores(
        records,
        horizon,
    )

    # Keep the original market ranking before portfolio adjustments.
    for record in records:
        score = record.get(
            "ranking_score"
        )

        if score is not None:
            record[
                "market_ranking_score"
            ] = round(
                float(score),
                1,
            )

    # ------------------------------------------------------------------
    # Portfolio-aware scoring
    # ------------------------------------------------------------------
    #
    # This is intentionally performed after market scoring.
    #
    # Example:
    #
    # Market score              86
    # Existing portfolio weight 28%
    # Sector exposure            41%
    #                         ↓
    # Portfolio-adjusted score   lower
    #
    # A strong stock therefore remains visible, but the engine can
    # distinguish "good market opportunity" from "good incremental
    # addition to this particular portfolio".
    # ------------------------------------------------------------------

    apply_portfolio_context(
        records,
        holdings,
    )

    # Decision-tree verification runs before any candidate reaches the LLM.
    decision_summary = evaluate_candidates(records, horizon)

    # ------------------------------------------------------------------
    # Ranking
    # ------------------------------------------------------------------

    ranked = rank(
        records,
        settings.ranking_limit,
    )

    shortlist = diversify(
        ranked,
        min(
            25,
            settings.vector_limit * 2,
        ),
    )

    # Position sizing uses the portfolio-adjusted ranking.
    add_position_context(
        ranked,
        profile,
    )

    # shortlist contains references to ranked records, so it already
    # carries the portfolio weights calculated across the full ranked set.

    # ------------------------------------------------------------------
    # News
    # ------------------------------------------------------------------

    symbols = [
        x["symbol"]
        for x in shortlist
        if x.get("symbol")
    ]

    news, news_errors = fetch_news(
        shortlist,
        max(
            50,
            settings.vector_limit * 3,
        ),
    )

    news_query = " ".join(
        [
            str(
                x.get("symbol", "")
            )
            + " "
            + str(
                x.get("name", "")
            )
            + " "
            + str(
                x.get("sector", "")
            )
            + " "
            + str(
                x.get("industry", "")
            )
            for x in shortlist
        ]
    )

    relevant = (
        retrieve_news(
            news,
            news_query,
            settings.vector_limit,
            settings.vector_store_dir,
        )
        if news
        else []
    )

    # ------------------------------------------------------------------
    # Research candidates
    # ------------------------------------------------------------------

    candidates = [
        row for row in shortlist
        if row.get("decision_verdict") == "PASS"
    ][: settings.vector_limit]

    deterministic_reviews = local_challenge(shortlist, horizon)

    if settings.enable_llm and candidates:
        (
            llm_reviews,
            tokens,
            llm_status,
        ) = review(
            candidates,
            horizon,
            relevant,
        )
    elif settings.enable_llm:
        llm_reviews = []
        tokens = 0
        llm_status = "skipped_by_decision_gate"
    else:
        llm_reviews = []
        tokens = 0
        llm_status = "disabled"

    # ------------------------------------------------------------------
    # Merge review results
    # ------------------------------------------------------------------

    review_map = {
        str(
            x.get("symbol", "")
        ).upper(): x
        for x in deterministic_reviews
    }

    for review_item in llm_reviews:
        symbol = str(
            review_item.get(
                "symbol",
                "",
            )
        ).upper()

        if symbol:
            review_map.setdefault(
                symbol,
                {},
            )["llm"] = review_item

    for record in records:
        symbol = str(
            record.get(
                "symbol",
                "",
            )
        ).upper()

        if symbol in review_map:
            record[
                "review"
            ] = review_map[symbol]

    # ------------------------------------------------------------------
    # Portfolio context for output
    # ------------------------------------------------------------------

    portfolio_instrument_context = (
        build_portfolio_instrument_context(
            records,
            holdings,
        )
    )

    # ------------------------------------------------------------------
    # Result
    # ------------------------------------------------------------------

    try:
        us_research = (run_us_research(settings.us_universe_path, settings.history_period, settings.us_analysis_limit, settings.cache_dir, settings.us_candidate_universe_path)
                       if settings.us_research_enabled else {"status": "disabled", "instruments": [], "news": []})
    except Exception as exc:
        logger.exception("US research failed independently of NSE analysis")
        us_research = {"status": "error", "message": str(exc), "instruments": [], "news": []}

    result = {
        "metadata": {
            "engine_version": "stock-etf-v10-decision-gated",

            "run_at_utc": datetime.now(
                timezone.utc
            ).isoformat(),

            "horizon": horizon,

            "discovered_universe_count": discovered,

            "universe_count": len(
                universe
            ),

            "universe_source": (
                "NSE current security lists unless "
                "stock_universe.json is explicitly populated"
            ),

            "price_source": (
                "Yahoo Finance latest available "
                "quote/history; not licensed exchange tick data"
            ),

            "analysis_universe_limit": (
                settings.analysis_universe_limit
            ),

            "selection_method": (
                "liquidity-screened dynamic NSE universe"
            ),

            "profile_backend_path": (
                settings.profile_path
            ),

            "portfolio_backend_path": (
                settings.holdings_path
            ),

            "portfolio_source": (
                "Zerodha Kite Connect"
                if holdings
                else "Not connected"
            ),
        },

        # --------------------------------------------------------------
        # Investor
        # --------------------------------------------------------------

        "investor_profile": profile,
        "us_research": us_research,
        "decision_model": decision_summary,

        # --------------------------------------------------------------
        # Portfolio
        # --------------------------------------------------------------

        "portfolio": {
            "source": (
                "zerodha_kite_connect"
                if holdings
                else "not_connected"
            ),

            "holdings": holdings,

            "summary": portfolio_info,

            "instrument_context": (
                portfolio_instrument_context
            ),
        },

        # --------------------------------------------------------------
        # Market / news
        # --------------------------------------------------------------

        "market": {
            "relevant_news": relevant,

            "news_found": len(
                news
            ),

            "news_errors": (
                news_errors[-20:]
            ),

            "news_search_links": (
                search_links(
                    shortlist
                )
            ),
        },

        # --------------------------------------------------------------
        # Research universe
        # --------------------------------------------------------------

        "evaluated_instruments": records,

        # --------------------------------------------------------------
        # Ranking
        # --------------------------------------------------------------

        "local_ranking": {
            "ranked": ranked,

            "shortlist": shortlist,

            "llm_candidates": candidates,
        },

        # --------------------------------------------------------------
        # Reviews
        # --------------------------------------------------------------

        "reviews": {
            "deterministic": (
                deterministic_reviews
            ),

            "llm": llm_reviews,

            "llm_status": llm_status,

            "prompt_tokens": tokens,
        },
    }

    # ------------------------------------------------------------------
    # Persist result
    # ------------------------------------------------------------------

    output_path = Path(
        settings.output_path
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path.write_text(
        json.dumps(
            result,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    logger.info(
        "Analysis written to %s",
        settings.output_path,
    )

    return result