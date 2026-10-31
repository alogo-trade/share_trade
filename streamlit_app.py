from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from stock_etf.profile_store import (
    load_profile,
    save_profile,
)
from stock_etf.portfolio import (
    load_holdings,
    portfolio_summary,
)
from stock_etf.zerodha import (
    get_login_url,
    get_redirect_url,
    refresh_holdings,
)


load_dotenv()


ROOT = Path(__file__).resolve().parent

OUT = ROOT / os.getenv(
    "STOCK_ETF_OUTPUT_PATH",
    "stock_etf_analysis.json",
)

LOGDIR = ROOT / "logs"
LOGDIR.mkdir(
    exist_ok=True
)

HORIZONS = {
    "SHORT_TERM": "1–2 Weeks",
    "MID_TERM": "1–12 Months",
    "LONG_TERM": "3–10+ Years",
}

PROFILE_PATH = ROOT / os.getenv(
    "INVESTOR_PROFILE_PATH",
    "backend_state/investor_profile.json",
)

HOLDINGS_PATH = ROOT / os.getenv(
    "ZERODHA_HOLDINGS_PATH",
    "backend_state/zerodha_holdings.json",
)


# ======================================================================
# General helpers
# ======================================================================

def sync_profile():
    profile = save_profile(
        PROFILE_PATH,
        st.session_state.age,
        st.session_state.capital,
    )

    st.session_state.profile_sync_message = (
        f"Backend profile synced · "
        f"{profile['updated_at_utc']}"
    )


def clean_html_text(value):
    """Convert provider HTML fragments to safe plain text."""

    import html as _html
    import re as _re

    if value is None:
        return ""

    text = str(value)

    text = _re.sub(
        r"<script[^>]*>.*?</script>",
        " ",
        text,
        flags=_re.I | _re.S,
    )

    text = _re.sub(
        r"<style[^>]*>.*?</style>",
        " ",
        text,
        flags=_re.I | _re.S,
    )

    text = _re.sub(
        r"<br\s*/?>",
        "\n",
        text,
        flags=_re.I,
    )

    text = _re.sub(
        r"</p>|</div>|</li>|</h[1-6]>",
        "\n",
        text,
        flags=_re.I,
    )

    text = _re.sub(
        r"<[^>]+>",
        " ",
        text,
    )

    text = _html.unescape(
        text
    )

    text = _re.sub(
        r"[ \t]+",
        " ",
        text,
    )

    text = _re.sub(
        r"\n[ \t]+",
        "\n",
        text,
    )

    return text.strip()


def load():
    if not OUT.exists():
        return None

    try:
        return json.loads(
            OUT.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return None


def load_live_portfolio():
    """
    Load the latest persisted Zerodha portfolio.

    This is intentionally independent of the research
    output file so the Portfolio tab always reflects the
    latest successful Zerodha refresh.
    """

    return load_holdings(
        HOLDINGS_PATH
    )


def fmt(
    value,
    suffix="",
):
    if value is None:
        return "—"

    if isinstance(
        value,
        (int, float),
    ):
        return f"{value:.1f}{suffix}"

    return str(value)


def format_inr(value):
    if value is None:
        return "₹0"

    try:
        return f"₹{float(value):,.0f}"
    except Exception:
        return str(value)


# ======================================================================
# Research engine
# ======================================================================

def run_engine(
    horizon,
    age,
    capital,
    enable_llm,
):
    """
    Run the research engine explicitly.

    This function is ONLY called by the Run Analysis
    button or Load/Run workflow.

    Zerodha authentication never calls this function.
    """

    env = os.environ.copy()

    env["PYTHONPATH"] = (
        str(ROOT)
        + os.pathsep
        + env.get(
            "PYTHONPATH",
            "",
        )
    )

    env["STOCK_HORIZON"] = horizon

    env["INVESTOR_AGE"] = str(
        age
    )

    env["INVESTMENT_CAPITAL_INR"] = str(
        capital
    )

    env["ENABLE_LLM_REVIEW"] = (
        "true"
        if enable_llm
        else "false"
    )

    env["INVESTOR_PROFILE_PATH"] = str(
        PROFILE_PATH
    )

    env["ZERODHA_HOLDINGS_PATH"] = str(
        HOLDINGS_PATH
    )

    env["PYTHONUNBUFFERED"] = "1"

    log = []

    with st.status(
        f"Running {HORIZONS[horizon]} research…",
        expanded=True,
    ) as status:

        process = subprocess.Popen(
            [
                sys.executable,
                "-u",
                str(
                    ROOT
                    / "run_stock_etf_analysis.py"
                ),
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        for line in process.stdout or []:

            line = line.rstrip()

            if line:
                log.append(line)

                st.code(
                    "\n".join(
                        log[-100:]
                    )
                )

        return_code = process.wait()

        stamp = pd.Timestamp.now().strftime(
            "%Y%m%d_%H%M%S"
        )

        (
            LOGDIR
            / f"ui_run_{stamp}.log"
        ).write_text(
            "\n".join(log)
            + "\n",
            encoding="utf-8",
        )

        (
            LOGDIR
            / "stock_etf_latest.log"
        ).write_text(
            "\n".join(log)
            + "\n",
            encoding="utf-8",
        )

        if return_code:

            status.update(
                label="Analysis failed",
                state="error",
            )

            st.error(
                "Engine failed. See logs/."
            )

            return None

        status.update(
            label="Analysis complete",
            state="complete",
        )

    return load()


# ======================================================================
# Zerodha callback
# ======================================================================

def process_zerodha_callback():
    """
    Process a Zerodha OAuth callback.

    IMPORTANT:
    This function ONLY refreshes the portfolio.

    It deliberately does NOT run the research engine.

    Zerodha callback example:

        http://localhost:8501/
        ?status=success
        &request_token=XXXX
        &action=login
    """

    params = st.query_params

    request_token = params.get(
        "request_token"
    )

    status = params.get(
        "status"
    )

    if not request_token:
        return

    request_token = str(
        request_token
    ).strip()

    if not request_token:
        return

    # Prevent duplicate processing of the same
    # single-use request token.
    processed_token = (
        st.session_state.get(
            "zerodha_processed_request_token"
        )
    )

    if (
        processed_token
        == request_token
    ):
        return

    if status and str(
        status
    ).lower() != "success":

        st.session_state[
            "zerodha_error"
        ] = (
            "Zerodha login did not complete successfully."
        )

        st.query_params.clear()

        return

    try:

        st.session_state[
            "zerodha_processed_request_token"
        ] = request_token

        with st.spinner(
            "Completing Zerodha authentication..."
        ):

            result = refresh_holdings(
                request_token,
                str(HOLDINGS_PATH),
            )

        st.session_state[
            "zerodha_success"
        ] = (
            "Zerodha portfolio refreshed successfully. "
            f"{result['holdings_count']} holdings loaded."
        )

        # Important:
        # Do NOT run the research engine here.
        #
        # The user explicitly controls research execution
        # through the Run Analysis button.

        st.query_params.clear()

    except Exception as exc:

        st.session_state.pop(
            "zerodha_processed_request_token",
            None,
        )

        st.session_state[
            "zerodha_error"
        ] = (
            f"Zerodha authentication failed: {exc}"
        )

        st.query_params.clear()


# ======================================================================
# Main application
# ======================================================================

def main():

    st.set_page_config(
        page_title="Stock & ETF Research Dashboard",
        page_icon="📈",
        layout="wide",
    )

    st.title(
        "📈 Stock & ETF Research Dashboard"
    )

    st.caption(
        "Dynamic NSE universe → liquidity screen → "
        "market data → deterministic ranking → "
        "portfolio-aware ranking → multi-source news → "
        "challenge review."
    )

    # --------------------------------------------------------------
    # Load investor profile
    # --------------------------------------------------------------

    backend_profile = load_profile(
        PROFILE_PATH,
        38,
        1_000_000.0,
    )

    if "horizon" not in st.session_state:
        st.session_state.horizon = (
            "SHORT_TERM"
        )

    if "age" not in st.session_state:
        st.session_state.age = (
            backend_profile["age"]
        )

    if "capital" not in st.session_state:
        st.session_state.capital = (
            backend_profile["capital_inr"]
        )

    if "llm" not in st.session_state:
        st.session_state.llm = False

    if (
        "profile_sync_message"
        not in st.session_state
    ):
        st.session_state.profile_sync_message = (
            "Backend profile loaded · "
            f"{backend_profile.get('updated_at_utc', 'unknown')}"
        )

    # --------------------------------------------------------------
    # Handle Zerodha callback
    #
    # This refreshes holdings only.
    # It does NOT run analysis.
    # --------------------------------------------------------------

    process_zerodha_callback()

    # --------------------------------------------------------------
    # Show callback status
    # --------------------------------------------------------------

    zerodha_success = st.session_state.pop(
        "zerodha_success",
        None,
    )

    zerodha_error = st.session_state.pop(
        "zerodha_error",
        None,
    )

    if zerodha_success:
        st.success(
            zerodha_success
        )

    if zerodha_error:
        st.error(
            zerodha_error
        )

    # --------------------------------------------------------------
    # Sidebar
    # --------------------------------------------------------------

    with st.sidebar:

        st.header(
            "Investor profile"
        )

        st.number_input(
            "Age",
            min_value=18,
            max_value=100,
            step=1,
            key="age",
            on_change=sync_profile,
        )

        st.number_input(
            "Investment capital (₹)",
            min_value=1000.0,
            step=10000.0,
            format="%.0f",
            key="capital",
            on_change=sync_profile,
        )

        st.caption(
            "Changes are persisted to the backend immediately. "
            "Capital is used for allocation context and does "
            "not change the underlying market score."
        )

        st.success(
            st.session_state.profile_sync_message
        )

        st.divider()

        st.header(
            "Research horizon"
        )

        st.radio(
            "Horizon",
            list(HORIZONS),
            format_func=lambda x: HORIZONS[x],
            key="horizon",
        )

        st.toggle(
            "Enable LLM challenge",
            value=st.session_state.llm,
            key="llm",
        )

        # ----------------------------------------------------------
        # Explicit analysis button
        # ----------------------------------------------------------

        if st.button(
            "▶ Run analysis",
            type="primary",
            use_container_width=True,
        ):

            result = run_engine(
                st.session_state.horizon,
                st.session_state.age,
                st.session_state.capital,
                st.session_state.llm,
            )

            if result is not None:
                st.session_state.result = result

            st.rerun()

        if st.button(
            "↻ Load latest",
            use_container_width=True,
        ):

            st.session_state.result = load()

            st.rerun()

        st.divider()

        # ----------------------------------------------------------
        # Zerodha
        # ----------------------------------------------------------

        st.header(
            "Zerodha portfolio"
        )

        try:

            configured_redirect_url = (
                get_redirect_url()
            )

            st.caption(
                "Configured redirect URL"
            )

            st.code(
                configured_redirect_url,
                language="text",
            )

            login_url = get_login_url()

            st.link_button(
                "🔐 Login to Zerodha",
                login_url,
                use_container_width=True,
            )

            st.caption(
                "After successful authentication, Zerodha "
                "automatically returns to this application. "
                "The portfolio will be refreshed, but research "
                "will NOT run automatically."
            )

            live_holdings = (
                load_live_portfolio()
            )

            if live_holdings:

                summary = portfolio_summary(
                    live_holdings
                )

                st.success(
                    f"{summary.get('holdings_count', 0)} "
                    f"holdings loaded · "
                    f"{format_inr(summary.get('current_value'))}"
                )

            else:

                st.info(
                    "No Zerodha holdings loaded yet."
                )

        except Exception as exc:

            st.warning(
                f"Zerodha login unavailable: {exc}"
            )

        st.divider()

        st.caption(
            "Universe is refreshed from NSE current "
            "equity + ETF security lists. A populated "
            "stock_universe.json overrides this only "
            "when you explicitly provide one."
        )

    # --------------------------------------------------------------
    # Load current research result
    # --------------------------------------------------------------

    result = (
        st.session_state.get("result")
        or load()
    )

    if not result:

        # Even without research results, show the portfolio.
        st.info(
            "No research result is loaded. "
            "You can still connect Zerodha and view your portfolio."
        )

        live_holdings = (
            load_live_portfolio()
        )

        if live_holdings:

            st.subheader(
                "Current Zerodha Portfolio"
            )

            summary = portfolio_summary(
                live_holdings
            )

            c1, c2, c3, c4 = st.columns(
                4
            )

            c1.metric(
                "Holdings",
                summary.get(
                    "holdings_count",
                    0,
                ),
            )

            c2.metric(
                "Invested",
                format_inr(
                    summary.get(
                        "invested_value"
                    )
                ),
            )

            c3.metric(
                "Current value",
                format_inr(
                    summary.get(
                        "current_value"
                    )
                ),
            )

            c4.metric(
                "P&L",
                format_inr(
                    summary.get(
                        "pnl"
                    )
                ),
            )

            portfolio_table = []

            for symbol, holding in (
                live_holdings.items()
            ):

                portfolio_table.append(
                    {
                        "Symbol": symbol,
                        "Exchange": holding.get(
                            "exchange"
                        ),
                        "Quantity": holding.get(
                            "quantity"
                        ),
                        "Average price": holding.get(
                            "average_price"
                        ),
                        "Last price": holding.get(
                            "last_price"
                        ),
                        "Invested value": holding.get(
                            "invested_value"
                        ),
                        "Current value": holding.get(
                            "current_value"
                        ),
                        "P&L": holding.get(
                            "pnl"
                        ),
                        "P&L %": holding.get(
                            "pnl_pct"
                        ),
                    }
                )

            st.dataframe(
                pd.DataFrame(
                    portfolio_table
                ),
                use_container_width=True,
                hide_index=True,
            )

        st.stop()

    # --------------------------------------------------------------
    # Research data
    # --------------------------------------------------------------

    rows = result.get(
        "evaluated_instruments",
        [],
    )

    ranked = result.get(
        "local_ranking",
        {},
    ).get(
        "ranked",
        [],
    )

    shortlist = result.get(
        "local_ranking",
        {},
    ).get(
        "llm_candidates",
        [],
    )

    news = result.get(
        "market",
        {},
    ).get(
        "relevant_news",
        [],
    )

    profile = result.get(
        "investor_profile",
        {},
    )

    reviews = result.get(
        "reviews",
        {},
    )

    portfolio = result.get(
        "portfolio",
        {},
    )

    # --------------------------------------------------------------
    # Dashboard metrics
    # --------------------------------------------------------------

    c1, c2, c3, c4, c5 = st.columns(
        5
    )

    c1.metric(
        "Universe",
        len(rows),
    )

    c2.metric(
        "Ranked",
        len(ranked),
    )

    c3.metric(
        "Candidates",
        len(shortlist),
    )

    c4.metric(
        "News",
        len(news),
    )

    c5.metric(
        "Reviews",
        len(
            reviews.get(
                "deterministic",
                [],
            )
        )
        + len(
            reviews.get(
                "llm",
                [],
            )
        ),
    )

    st.info(
        f"Profile: age {profile.get('age')} "
        f"· capital ₹{profile.get('capital_inr', 0):,.0f} "
        f"· horizon capital context "
        f"₹{profile.get('horizon_capital_inr', 0):,.0f}"
    )

    # --------------------------------------------------------------
    # Tabs
    # --------------------------------------------------------------

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
        [
            "🏆 Opportunities",
            "📊 Stock / ETF detail",
            "📰 News & review",
            "💼 Current Portfolio",
            "⚙ Diagnostics",
            "🇺🇸 US Research",
        ]
    )

    # ==============================================================
    # Opportunities
    # ==============================================================

    with tab1:

        st.subheader(
            f"Top candidates · "
            f"{HORIZONS[result['metadata']['horizon']]}"
        )

        filt = st.multiselect(
            "Instrument type",
            ["STOCK", "ETF"],
            default=["STOCK", "ETF"],
        )

        data = [
            x
            for x in ranked
            if x.get(
                "instrument_type"
            )
            in filt
        ]

        table = pd.DataFrame(
            [
                {
                    "Rank": x.get(
                        "local_rank"
                    ),
                    "Symbol": x.get(
                        "symbol"
                    ),
                    "Type": x.get(
                        "instrument_type"
                    ),
                    "Market score": x.get(
                        "market_ranking_score"
                    ),
                    "Portfolio score": x.get(
                        "portfolio_adjusted_score"
                    ),
                    "Portfolio context": x.get(
                        "portfolio_context"
                    ),
                    "Owned": x.get(
                        "already_owned"
                    ),
                    "Position %": x.get(
                        "portfolio_position_pct"
                    ),
                    "Sector exposure %": x.get(
                        "portfolio_sector_exposure_pct"
                    ),
                    "P&L %": x.get(
                        "portfolio_pnl_pct"
                    ),
                    "Confidence": x.get(
                        "data_confidence"
                    ),
                    "Signal": x.get(
                        "signal"
                    ),
                    "Suggested weight %": x.get(
                        "suggested_weight_pct"
                    ),
                    "Capital context ₹": x.get(
                        "capital_context_inr"
                    ),
                    "1W %": x.get(
                        "return_1w_pct"
                    ),
                    "1M %": x.get(
                        "return_1m_pct"
                    ),
                    "3M %": x.get(
                        "return_3m_pct"
                    ),
                    "1Y %": x.get(
                        "return_1y_pct"
                    ),
                    "RSI": x.get(
                        "rsi14"
                    ),
                }
                for x in data[:30]
            ]
        )

        st.dataframe(
            table,
            use_container_width=True,
            hide_index=True,
        )

        st.caption(
            "Market score is the original market-based score. "
            "Portfolio score incorporates existing holdings "
            "and concentration context."
        )

    # ==============================================================
    # Stock / ETF detail
    # ==============================================================

    with tab2:

        symbols = [
            x.get("symbol")
            for x in rows
            if x.get("price") is not None
        ]

        if not symbols:

            st.warning(
                "No instruments have usable market data. "
                "Check Diagnostics and logs/."
            )

        else:

            sym = st.selectbox(
                "Instrument",
                symbols,
                index=0,
            )

            row = next(
                (
                    x
                    for x in rows
                    if x.get(
                        "symbol"
                    )
                    == sym
                ),
                None,
            )

            if row:

                st.subheader(
                    f"{row.get('name', sym)} · {sym}"
                )

                a, b, c, d, e = st.columns(
                    5
                )

                a.metric(
                    "Latest price",
                    fmt(
                        row.get(
                            "price"
                        )
                    ),
                )

                b.metric(
                    "Market score",
                    fmt(
                        row.get(
                            "market_ranking_score"
                        )
                    ),
                )

                c.metric(
                    "Portfolio score",
                    fmt(
                        row.get(
                            "portfolio_adjusted_score"
                        )
                    ),
                )

                d.metric(
                    "1M",
                    fmt(
                        row.get(
                            "return_1m_pct"
                        ),
                        "%",
                    ),
                )

                e.metric(
                    "1Y",
                    fmt(
                        row.get(
                            "return_1y_pct"
                        ),
                        "%",
                    ),
                )

                st.write(
                    "**Signal:**",
                    row.get("signal"),
                    " · **Data confidence:**",
                    fmt(
                        row.get(
                            "data_confidence"
                        ),
                        "%",
                    ),
                )

                st.markdown(
                    "### Portfolio context"
                )

                p1, p2, p3, p4 = st.columns(
                    4
                )

                p1.metric(
                    "Already owned",
                    "Yes"
                    if row.get(
                        "already_owned"
                    )
                    else "No",
                )

                p2.metric(
                    "Position",
                    fmt(
                        row.get(
                            "portfolio_position_pct"
                        ),
                        "%",
                    ),
                )

                p3.metric(
                    "Sector exposure",
                    fmt(
                        row.get(
                            "portfolio_sector_exposure_pct"
                        ),
                        "%",
                    ),
                )

                p4.metric(
                    "P&L",
                    fmt(
                        row.get(
                            "portfolio_pnl_pct"
                        ),
                        "%",
                    ),
                )

                context = row.get(
                    "portfolio_context"
                )

                if context:

                    st.info(
                        f"Portfolio context: {context}"
                    )

                left, right = st.columns(
                    2
                )

                with left:

                    st.markdown(
                        "### Technical"
                    )

                    st.json(
                        {
                            key: row.get(key)
                            for key in [
                                "sma20",
                                "sma50",
                                "sma200",
                                "distance_sma20_pct",
                                "distance_sma50_pct",
                                "distance_sma200_pct",
                                "rsi14",
                                "atr_pct",
                                "volume_ratio_20d",
                                "volatility_pct",
                                "max_drawdown_pct",
                            ]
                        }
                    )

                with right:

                    st.markdown(
                        "### Fundamentals / ETF data"
                    )

                    st.json(
                        {
                            key: row.get(key)
                            for key in [
                                "sector",
                                "industry",
                                "market_cap_inr",
                                "pe",
                                "forward_pe",
                                "pb",
                                "ev_ebitda",
                                "roe_pct",
                                "revenue_growth_pct",
                                "earnings_growth_pct",
                                "debt_to_equity",
                                "free_cashflow",
                                "expense_ratio",
                                "tracking_error",
                                "category",
                            ]
                        }
                    )

                if row.get(
                    "review"
                ):

                    st.markdown(
                        "### Challenge review"
                    )

                    st.json(
                        row["review"]
                    )

    # ==============================================================
    # News & review
    # ==============================================================

    with tab3:

        if not news:

            errors = result.get(
                "market",
                {},
            ).get(
                "news_errors",
                [],
            )

            st.warning(
                "No live articles were returned by the "
                "configured providers for this run."
            )

            if errors:

                st.error(
                    "News provider diagnostics:"
                )

                st.code(
                    "\n".join(
                        errors[-10:]
                    )
                )

            links = result.get(
                "market",
                {},
            ).get(
                "news_search_links",
                [],
            )

            if links:

                st.markdown(
                    "### Live search fallback"
                )

                for item in links:

                    url = str(
                        item.get(
                            "url",
                            "",
                        )
                    )

                    if url.startswith(
                        (
                            "http://",
                            "https://",
                        )
                    ):

                        st.link_button(
                            f"{item['symbol']} news",
                            url,
                        )

        for article in news:

            title = (
                clean_html_text(
                    article.get(
                        "title",
                        "",
                    )
                )
                or "Untitled article"
            )

            source = (
                clean_html_text(
                    article.get(
                        "source",
                        "",
                    )
                )
                or "Unknown source"
            )

            published = clean_html_text(
                article.get(
                    "published",
                    "",
                )
            )

            summary = clean_html_text(
                article.get(
                    "summary",
                    "",
                )
            )

            similarity = article.get(
                "vector_similarity",
                "—",
            )

            with st.container(
                border=True
            ):

                st.markdown(
                    f"### {title}"
                )

                meta = source

                if published:
                    meta += (
                        f" · {published}"
                    )

                if similarity != "—":
                    meta += (
                        f" · similarity {similarity}"
                    )

                st.caption(
                    meta
                )

                if summary:
                    st.write(
                        summary[:700]
                    )

                link = str(
                    article.get(
                        "link",
                        "",
                    )
                ).strip()

                if link.startswith(
                    (
                        "http://",
                        "https://",
                    )
                ):

                    st.link_button(
                        "Open article",
                        link,
                    )

        st.divider()

        st.subheader(
            "Review status"
        )

        st.write(
            reviews.get(
                "llm_status",
                "unknown",
            )
        )

        st.json(
            {
                "deterministic_reviews": reviews.get(
                    "deterministic",
                    [],
                ),
                "llm_reviews": reviews.get(
                    "llm",
                    [],
                ),
            }
        )

    # ==============================================================
    # Current Portfolio
    # ==============================================================

    with tab4:

        st.subheader(
            "Current Zerodha Portfolio"
        )

        live_holdings = (
            load_live_portfolio()
        )

        if not live_holdings:

            st.info(
                "No Zerodha holdings are currently loaded."
            )

        else:

            summary = portfolio_summary(
                live_holdings
            )

            p1, p2, p3, p4, p5 = st.columns(
                5
            )

            p1.metric(
                "Holdings",
                summary.get(
                    "holdings_count",
                    0,
                ),
            )

            p2.metric(
                "Invested",
                format_inr(
                    summary.get(
                        "invested_value"
                    )
                ),
            )

            p3.metric(
                "Current value",
                format_inr(
                    summary.get(
                        "current_value"
                    )
                ),
            )

            p4.metric(
                "P&L",
                format_inr(
                    summary.get(
                        "pnl"
                    )
                ),
            )

            p5.metric(
                "P&L %",
                fmt(
                    summary.get(
                        "pnl_pct"
                    ),
                    "%",
                ),
            )

            portfolio_table = []

            for symbol, holding in (
                live_holdings.items()
            ):

                portfolio_table.append(
                    {
                        "Symbol": symbol,
                        "Exchange": holding.get(
                            "exchange"
                        ),
                        "Quantity": holding.get(
                            "quantity"
                        ),
                        "Average price": holding.get(
                            "average_price"
                        ),
                        "Last price": holding.get(
                            "last_price"
                        ),
                        "Invested value": holding.get(
                            "invested_value"
                        ),
                        "Current value": holding.get(
                            "current_value"
                        ),
                        "P&L": holding.get(
                            "pnl"
                        ),
                        "P&L %": holding.get(
                            "pnl_pct"
                        ),
                    }
                )

            st.dataframe(
                pd.DataFrame(
                    portfolio_table
                ),
                use_container_width=True,
                hide_index=True,
            )

            st.divider()

            st.subheader(
                "Portfolio vs Research"
            )

            research_rows = []

            for symbol, holding in (
                live_holdings.items()
            ):

                match = next(
                    (
                        row
                        for row in rows
                        if str(
                            row.get(
                                "symbol"
                            )
                        ).upper()
                        == str(
                            symbol
                        ).upper()
                    ),
                    None,
                )

                research_rows.append(
                    {
                        "Symbol": symbol,
                        "Current value": holding.get(
                            "current_value"
                        ),
                        "P&L %": holding.get(
                            "pnl_pct"
                        ),
                        "Market score": (
                            match.get(
                                "market_ranking_score"
                            )
                            if match
                            else None
                        ),
                        "Portfolio score": (
                            match.get(
                                "portfolio_adjusted_score"
                            )
                            if match
                            else None
                        ),
                        "Signal": (
                            match.get(
                                "signal"
                            )
                            if match
                            else "Not evaluated"
                        ),
                        "Portfolio context": (
                            match.get(
                                "portfolio_context"
                            )
                            if match
                            else "Outside analysis universe"
                        ),
                    }
                )

            st.dataframe(
                pd.DataFrame(
                    research_rows
                ),
                use_container_width=True,
                hide_index=True,
            )

    # ==============================================================
    # Diagnostics
    # ==============================================================

    with tab6:
        us = result.get("us_research", {})
        st.subheader("US stocks and ETFs")
        st.caption("Only manually verified Zerodha Kite Global Stocks symbols are evaluated. Yahoo Finance supplies research data, not Zerodha quotes. US trading and US holdings sync are not implemented.")
        status = us.get("status", "not_configured")
        if status == "universe_not_configured":
            st.warning("No verified US instruments are configured. Populate data/zerodha_us_universe.json after checking eligibility in Kite.")
            st.code('{"instruments":[{"symbol":"AAPL","type":"STOCK","zerodha_eligible":true,"verified_on":"YYYY-MM-DD"}]}')
        elif status == "error":
            st.error(f"US research failed: {us.get('message', 'Unknown error')}")
        elif status == "disabled":
            st.info("US research is disabled. Set ENABLE_US_RESEARCH=true.")
        else:
            us_rows = us.get("instruments", [])
            st.metric("Evaluated US instruments", len(us_rows))
            if us_rows:
                frame = pd.DataFrame(us_rows)
                columns = [c for c in ["symbol","type","name","price_usd","change_1m_pct","change_6m_pct","volatility_annual_pct","market_cap_usd","expense_ratio","dividend_yield_pct","research_score","signal","data_status"] if c in frame.columns]
                st.dataframe(frame[columns].sort_values("research_score", ascending=False, na_position="last"), use_container_width=True, hide_index=True)
            for article in us.get("news", [])[:20]:
                st.markdown(f"**{article.get('title', 'Untitled')}**")
                st.caption(article.get("publisher", "Yahoo Finance"))
                link = article.get("link", "")
                if str(link).startswith(("https://", "http://")):
                    st.link_button("Open article", link)

    with tab5:

        st.subheader(
            "Diagnostics"
        )

        st.json(
            result.get(
                "metadata",
                {},
            )
        )

        st.write(
            "Investor profile:",
            profile,
        )

        st.write(
            "Portfolio summary:",
            portfolio.get(
                "summary",
                {},
            ),
        )

        st.write(
            "Portfolio holdings file:",
            str(HOLDINGS_PATH),
        )

        st.write(
            "Portfolio source:",
            portfolio.get(
                "source",
                "No portfolio",
            ),
        )

        st.write(
            "Zerodha redirect URL:",
            os.getenv(
                "ZERODHA_REDIRECT_URL",
                "not configured",
            ),
        )

        st.write(
            "Reviews:",
            reviews,
        )

        st.write(
            "Errors:",
            sum(
                bool(
                    x.get(
                        "error"
                    )
                )
                for x in rows
            ),
        )

        errors = [
            x
            for x in rows
            if x.get(
                "error"
            )
        ]

        if errors:

            st.dataframe(
                pd.DataFrame(
                    errors
                )[
                    [
                        "symbol",
                        "yf_symbol",
                        "error",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )

        st.write(
            "Log directory:",
            str(LOGDIR),
        )

        st.write(
            "Data confidence distribution:",
            pd.Series(
                [
                    x.get(
                        "data_confidence",
                        0,
                    )
                    for x in rows
                ]
            ).describe(),
        )


if __name__ == "__main__":
    main()