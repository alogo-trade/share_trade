# Stock & ETF Research Dashboard v2

## What changed
- No hardcoded stock/ETF universe in the runtime path.
- On each analysis run, the dashboard refreshes the current NSE equity security list and NSE ETF security list. NSE publishes these as separate current CSV files. citeturn1view0
- `stock_universe.json` is empty by default. Populate it only if you intentionally want a custom universe.
- Latest available market price/history comes from Yahoo Finance. Historical data supports the technical calculations; it should not be described as a guaranteed tick-by-tick NSE feed. Yahoo Finance supports market-data downloads across daily and intraday intervals, subject to its data limitations. citeturn0search3
- News retrieval now queries each shortlisted symbol plus a broader Indian-market query.
- A transparent deterministic decision-tree gate checks data freshness, history depth, confidence, signal consistency and short-term overextension before LLM review. Only PASS candidates are sent to the LLM. HOLD and REJECT decisions include auditable reasons. This is a rule-based verification model, not a trained return predictor.
- Deterministic challenge reviews are always generated, even without an LLM API key.
- Optional OpenRouter LLM review remains available.
- Every run is persisted under `logs/` with a timestamped log and `stock_etf_latest.log`.
- Age and investment capital are available in the sidebar and are persisted with the analysis.
- Capital produces allocation context based only on candidates that pass the decision gate. It does not change the raw market score. AVOID, WATCH, stale-data and low-confidence instruments receive zero suggested allocation.

## Run
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
streamlit run streamlit_app.py
```

## Data-source behavior
The official NSE page lists the current equity and ETF security CSV files and was updated in September 2026. citeturn0search0

If NSE is unavailable, the dashboard uses the last successfully downloaded `cache/nse_universe.json`. If there is no cache and no custom universe, the run fails clearly instead of silently producing zero instruments.


## v5 fixes

- Dynamically discovers the current NSE stock and ETF universe.
- Applies a lightweight liquidity screen before downloading 3 years of detailed history.
- Limits detailed analysis to `ANALYSIS_UNIVERSE_LIMIT` (default 250).
- Handles missing Yahoo Finance fundamentals without failing the whole instrument.
- Uses per-symbol Google News RSS plus Yahoo Finance search as news providers, with a market-news fallback.
- Persists provider errors in `market.news_errors` so an empty news tab is diagnosable.
- Keeps deterministic challenge reviews independent of LLM availability.


### News v5
Uses yfinance news, Yahoo Finance search, Google News RSS, and Bing News RSS with provider diagnostics and live search-link fallback.


### News scope
The news layer intentionally covers direct company/ticker news plus India macro/policy, global macro, geopolitics, trade/sanctions, commodities, technology policy, environment, climate, pollution, extreme weather, and sector-specific developments. Indirect news is retained when it can affect demand, input costs, regulation, supply chains, financing conditions, or business continuity.


## US market research

The US pipeline is independent of NSE research. It evaluates a curated discovery watchlist in `data/us_candidate_universe.json` plus instruments explicitly marked `zerodha_eligible: true` in `data/zerodha_us_universe.json`. Candidate instruments are not confirmed as available in Zerodha. Verify them in Kite before moving them into the verified file. Verified instruments are prioritised when the analysis limit is applied. Yahoo Finance provides research data, not Zerodha quotes. No US trading or holdings sync is implemented. The candidate file is a curated starter watchlist, not a complete US listing or Zerodha eligibility feed.

Configure with `ENABLE_US_RESEARCH=true` (default), `ZERODHA_US_UNIVERSE_PATH`, `US_CANDIDATE_UNIVERSE_PATH`, and `US_ANALYSIS_UNIVERSE_LIMIT` (default 30).
