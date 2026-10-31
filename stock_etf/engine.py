from __future__ import annotations

import json
import logging
from copy import deepcopy
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
from .decision_model import DecisionRulesConfig, evaluate_candidates, rules_config_snapshot
from .jev_verifier import DEFAULT_MODEL as DEFAULT_JEV_MODEL, verify_candidates
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
        batch_size=settings.yahoo_liquidity_batch_size,
        request_timeout_seconds=settings.yahoo_request_timeout_seconds,
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
        history_batch_size=settings.yahoo_history_batch_size,
        request_timeout_seconds=settings.yahoo_request_timeout_seconds,
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

    # Deterministic rules are always the hard safety gate. Jev can only
    # evaluate candidates that have already passed those checks.
    rules_config = DecisionRulesConfig(
        max_data_age_days=settings.decision_max_data_age_days,
        min_data_confidence=settings.decision_min_data_confidence,
        min_observations_short=settings.decision_min_observations_short,
        min_observations_mid=settings.decision_min_observations_mid,
        min_observations_long=settings.decision_min_observations_long,
        max_rsi_short=settings.decision_max_rsi_short,
        max_distance_sma20_short_pct=settings.decision_max_distance_sma20_short_pct,
    )
    rules_summary = evaluate_candidates(records, horizon, config=rules_config)
    provider = settings.decision_provider.upper()
    if provider not in {"RULES", "JEV", "HYBRID"}:
        raise ValueError("DECISION_PROVIDER must be RULES, JEV, or HYBRID.")

    jev_summary = {"status": "not_selected", "model": settings.jev_model,
                   "evaluated_count": 0, "pass_count": 0, "hold_count": 0,
                   "reject_count": 0, "decisions": []}
    shadow_mode = bool(settings.decision_shadow_mode and provider != "RULES")
    active_jev = provider in {"JEV", "HYBRID"} and not shadow_mode
    run_jev = provider in {"JEV", "HYBRID"}

    if run_jev:
        rule_pass_rows = [row for row in records if row.get("decision_verdict") == "PASS"]
        # Shadow mode operates on copies and cannot change production eligibility.
        jev_rows = deepcopy(rule_pass_rows) if shadow_mode else rule_pass_rows
        pass_threshold = (
            settings.hybrid_pass_probability_threshold
            if provider == "HYBRID"
            else settings.jev_pass_probability_threshold
        )
        confidence_threshold = (
            settings.hybrid_confidence_threshold
            if provider == "HYBRID"
            else settings.jev_confidence_threshold
        )
        jev_summary = verify_candidates(
            jev_rows,
            horizon,
            model=settings.jev_model or DEFAULT_JEV_MODEL,
            timeout_seconds=settings.jev_timeout_seconds,
            pass_threshold=pass_threshold,
            reject_threshold=settings.jev_reject_probability_threshold,
            confidence_threshold=confidence_threshold,
        )
        jev_summary["mode"] = provider
        jev_summary["shadow_mode"] = shadow_mode
        if active_jev:
            # Only the Jev-evaluated deterministic PASS set can be changed.
            by_symbol = {str(row.get("symbol", "")).upper(): row for row in jev_rows}
            for row in rule_pass_rows:
                evaluated = by_symbol.get(str(row.get("symbol", "")).upper())
                if evaluated:
                    for key in ("decision_verdict", "eligible_for_llm", "jev_verdict",
                                "jev_probability", "jev_confidence", "jev_reason", "jev_model", "deterministic_decision_verdict"):
                        if key in evaluated:
                            row[key] = evaluated[key]
                    detail = row.get("decision_model", {})
                    detail["deterministic_verdict"] = row.get("deterministic_decision_verdict", "PASS")
                    detail["verdict"] = row.get("decision_verdict", "HOLD")
                    detail["eligible_for_llm"] = row.get("eligible_for_llm", False)
                    detail["jev_verdict"] = row.get("jev_verdict")
                    detail["jev_reason"] = row.get("jev_reason")
                    detail["jev_probability"] = row.get("jev_probability")
                    detail["jev_confidence"] = row.get("jev_confidence")
                    detail["jev_model"] = row.get("jev_model")
                    row["decision_model"] = detail

    decision_summary = {
        "provider": provider,
        "active_provider": "RULES" if shadow_mode else provider,
        "shadow_mode": shadow_mode,
        "rules": rules_summary,
        "jev": jev_summary,
        "rules_config": rules_config_snapshot(rules_config),
        "config_snapshot": {
            "provider": provider,
            "shadow_mode": shadow_mode,
            "jev_model": settings.jev_model,
            "jev_timeout_seconds": settings.jev_timeout_seconds,
            "jev_pass_probability_threshold": settings.jev_pass_probability_threshold,
            "jev_reject_probability_threshold": settings.jev_reject_probability_threshold,
            "jev_confidence_threshold": settings.jev_confidence_threshold,
            "hybrid_pass_probability_threshold": settings.hybrid_pass_probability_threshold,
            "hybrid_confidence_threshold": settings.hybrid_confidence_threshold,
            "rules": rules_config_snapshot(rules_config),
        },
        "evaluated_count": rules_summary["evaluated_count"],
        "pass_count": sum(row.get("decision_verdict") == "PASS" for row in records),
        "hold_count": sum(row.get("decision_verdict") == "HOLD" for row in records),
        "reject_count": sum(row.get("decision_verdict") == "REJECT" for row in records),
        "decisions": [row.get("decision_model", {}) for row in records],
        "description": (
            "Deterministic rules are always the hard safety gate. Jev classifies only "
            "rule-approved candidates for escalation to generative research review. "
            "This is not a return forecast or trade recommendation."
        ),
    }

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