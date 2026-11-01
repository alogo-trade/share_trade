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

The US pipeline is independent of NSE research. By default it discovers current US-listed securities from the Nasdaq Trader symbol directories and dynamically screens them through Yahoo Finance before detailed analysis. Stocks must meet the configured market-cap, average daily volume and share-price thresholds; ETFs must meet fund net assets, average daily volume and price thresholds. The discovery cache is refreshed every 24 hours, with a seven-day stale-cache fallback. The curated `data/us_candidate_universe.json` file is retained only for optional legacy/manual candidate mode when `US_DYNAMIC_DISCOVERY_ENABLED=false`. Instruments explicitly marked `zerodha_eligible: true` in `data/zerodha_us_universe.json` remain a separate user-verified set and are always retained ahead of unverified candidates. Candidate instruments are not confirmed as available in Zerodha. Use the Streamlit sidebar expander **Manually verify a US symbol in Zerodha** after searching the exact ticker in Kite > International / Global Stocks. The form requires only the ticker, company/ETF name, instrument type, exchange, and explicit user confirmation. Index membership is neither required nor checked for manual verification. It atomically upserts the verified universe file with the verification timestamp and method. This is a user attestation, not a broker API lookup. Verified instruments are prioritised when the analysis limit is applied. Yahoo Finance provides research data, not Zerodha quotes. No US trading or holdings sync is implemented. The legacy candidate file is not used when dynamic discovery is enabled and is not a complete US listing or Zerodha eligibility feed.

Configure with `ENABLE_US_RESEARCH=true` (default), `US_DYNAMIC_DISCOVERY_ENABLED=true` (default), `ZERODHA_US_UNIVERSE_PATH`, and `US_ANALYSIS_UNIVERSE_LIMIT` (default 30). Discovery thresholds are configurable through `US_MIN_MARKET_CAP_USD` (default $2B), `US_MIN_AVG_DAILY_VOLUME` (500,000 shares), `US_MIN_SHARE_PRICE_USD` ($5), `US_MIN_ETF_ASSETS_USD` ($500M), `US_MIN_ETF_AVG_DAILY_VOLUME` (250,000 shares), `US_MIN_ETF_PRICE_USD` ($10), and `US_DISCOVERY_MAX_RESULTS_PER_TYPE` (1,000). Set `US_DYNAMIC_DISCOVERY_ENABLED=false` only to use the legacy candidate JSON file.

US research produces a separate score-ranked research list and shortlist. Unverified candidates remain explicitly marked research-only and are never included in the actionable list. The actionable list requires both explicit Zerodha eligibility verification and a positive research signal. This ranking does not invoke the NSE decision model or place orders.

US market news is collected by macro, policy, technology, energy, geopolitics, consumer, healthcare and broad-market themes. Results are deduplicated, restricted to the configured freshness window, and balanced across categories before being copied to the top-level market report.

US metadata records the provider/source and observation timestamp per instrument. Yahoo Finance does not expose a consistent reporting date for all metadata fields, so the report marks the reporting date unavailable rather than implying that metadata is current. Market-price history dates remain separate from metadata observation timestamps.

ETF disclosure ingestion accepts official AMC spreadsheet and PDF portfolio disclosures. PDF table extraction uses `pdfplumber`. A disclosure is accepted only when the scheme can be matched, the portfolio date is present, constituent weights validate, and the reporting date is within `ETF_DISCLOSURE_MAX_AGE_DAYS`. The default freshness window remains 40 days. Classification-only cache entries do not count as constituent look-through coverage.


## Pre-LLM verification

The quantitative NSE pipeline now passes candidates through an auditable decision-tree gate before LLM review. See [Decision model documentation](docs/DECISION_MODEL.md) for PASS/HOLD/REJECT rules, thresholds and calibration requirements.
