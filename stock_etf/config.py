from __future__ import annotations

import os
from dataclasses import dataclass



@dataclass(frozen=True)
class Settings:
    universe_path: str = os.getenv(
        "STOCK_UNIVERSE_PATH",
        "stock_universe.json",
    )

    output_path: str = os.getenv(
        "STOCK_ETF_OUTPUT_PATH",
        "stock_etf_analysis.json",
    )

    cache_dir: str = os.getenv(
        "STOCK_CACHE_DIR",
        "cache",
    )

    etf_disclosure_refresh_enabled: bool = os.getenv(
        "ETF_DISCLOSURE_REFRESH_ENABLED", "true"
    ).lower() in {"1", "true", "yes"}

    etf_disclosure_max_age_days: int = int(
        os.getenv("ETF_DISCLOSURE_MAX_AGE_DAYS", "40")
    )

    nse_universe_cache: str = os.getenv(
        "NSE_UNIVERSE_CACHE",
        "cache/nse_universe.json",
    )

    vector_store_dir: str = os.getenv(
        "VECTOR_STORE_DIR",
        "vector_store",
    )

    history_period: str = os.getenv(
        "HISTORY_PERIOD",
        "3y",
    )

    ranking_limit: int = int(
        os.getenv(
            "LOCAL_RANKING_LIMIT",
            "50",
        )
    )

    vector_limit: int = int(
        os.getenv(
            "VECTOR_RETRIEVAL_LIMIT",
            "15",
        )
    )

    enable_llm: bool = (
        os.getenv(
            "ENABLE_LLM_REVIEW",
            "false",
        ).lower()
        in {
            "1",
            "true",
            "yes",
        }
    )

    openrouter_model: str = os.getenv(
        "OPENROUTER_MODEL_NAME",
        "openai/gpt-5.6-mini",
    )

    max_workers: int = int(
        os.getenv(
            "MAX_WORKERS",
            "3",
        )
    )

    yahoo_history_batch_size: int = int(
        os.getenv(
            "YAHOO_HISTORY_BATCH_SIZE",
            "40",
        )
    )

    yahoo_request_timeout_seconds: int = int(
        os.getenv("YAHOO_REQUEST_TIMEOUT_SECONDS", "10")
    )

    yahoo_liquidity_batch_size: int = int(
        os.getenv("YAHOO_LIQUIDITY_BATCH_SIZE", "100")
    )

    yahoo_cooldown_seconds: int = int(
        os.getenv(
            "YAHOO_COOLDOWN_SECONDS",
            "300",
        )
    )

    analysis_universe_limit: int = int(
        os.getenv(
            "ANALYSIS_UNIVERSE_LIMIT",
            "250",
        )
    )

    profile_path: str = os.getenv(
        "INVESTOR_PROFILE_PATH",
        "backend_state/investor_profile.json",
    )

    # ------------------------------------------------------------------
    # Zerodha / portfolio integration
    # ------------------------------------------------------------------

    holdings_path: str = os.getenv(
        "ZERODHA_HOLDINGS_PATH",
        "backend_state/zerodha_holdings.json",
    )

    # Market coverage: INDIA_ONLY, US_ONLY, or HYBRID.
    market_scope: str = os.getenv("MARKET_SCOPE", "HYBRID").strip().upper()
    us_research_enabled: bool = os.getenv("ENABLE_US_RESEARCH", "true").lower() in {"1", "true", "yes"}
    us_universe_path: str = os.getenv("ZERODHA_US_UNIVERSE_PATH", "data/zerodha_us_universe.json")
    us_candidate_universe_path: str = os.getenv("US_CANDIDATE_UNIVERSE_PATH", "data/us_candidate_universe.json")
    us_analysis_limit: int = int(os.getenv("US_ANALYSIS_UNIVERSE_LIMIT", "30"))

    # Configurable pre-LLM decision provider. RULES remains the safe default.
    decision_provider: str = os.getenv("DECISION_PROVIDER", "RULES").strip().upper()
    decision_shadow_mode: bool = os.getenv("DECISION_SHADOW_MODE", "true").lower() in {"1", "true", "yes"}
    jev_model: str = os.getenv("JEV_MODEL", "typesafe/jev-1.14")
    jev_timeout_seconds: int = int(os.getenv("JEV_TIMEOUT_SECONDS", "25"))
    jev_pass_probability_threshold: float = float(os.getenv("JEV_PASS_PROBABILITY_THRESHOLD", "0.80"))
    jev_reject_probability_threshold: float = float(os.getenv("JEV_REJECT_PROBABILITY_THRESHOLD", "0.80"))
    jev_confidence_threshold: float = float(os.getenv("JEV_CONFIDENCE_THRESHOLD", "0.80"))
    hybrid_pass_probability_threshold: float = float(os.getenv("HYBRID_PASS_PROBABILITY_THRESHOLD", "0.90"))
    hybrid_confidence_threshold: float = float(os.getenv("HYBRID_CONFIDENCE_THRESHOLD", "0.90"))

    decision_max_data_age_days: int = int(os.getenv("DECISION_MAX_DATA_AGE_DAYS", "7"))
    decision_max_market_sessions_age: int = int(os.getenv("DECISION_MAX_MARKET_SESSIONS_AGE", "2"))
    news_max_age_days: int = int(os.getenv("NEWS_MAX_AGE_DAYS", "30"))
    decision_min_data_confidence: float = float(os.getenv("DECISION_MIN_DATA_CONFIDENCE", "70"))
    decision_min_observations_short: int = int(os.getenv("DECISION_MIN_OBSERVATIONS_SHORT", "50"))
    decision_min_observations_mid: int = int(os.getenv("DECISION_MIN_OBSERVATIONS_MID", "100"))
    decision_min_observations_long: int = int(os.getenv("DECISION_MIN_OBSERVATIONS_LONG", "200"))
    decision_max_rsi_short: float = float(os.getenv("DECISION_MAX_RSI_SHORT", "85"))
    decision_max_distance_sma20_short_pct: float = float(os.getenv("DECISION_MAX_DISTANCE_SMA20_SHORT_PCT", "20"))
