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
            "80",
        )
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

    us_research_enabled: bool = os.getenv("ENABLE_US_RESEARCH", "true").lower() in {"1", "true", "yes"}
    us_universe_path: str = os.getenv("ZERODHA_US_UNIVERSE_PATH", "data/zerodha_us_universe.json")
    us_candidate_universe_path: str = os.getenv("US_CANDIDATE_UNIVERSE_PATH", "data/us_candidate_universe.json")
    us_analysis_limit: int = int(os.getenv("US_ANALYSIS_UNIVERSE_LIMIT", "30"))
