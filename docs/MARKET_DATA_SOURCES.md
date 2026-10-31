# Market data sources and freshness

## Current implementation

The NSE research pipeline currently obtains daily OHLCV history and most fundamentals through Yahoo Finance (yfinance). Yahoo is a convenience source, not an exchange-authorised feed. The application must display the source and last market-data date and must never equate a successful HTTP response or a high confidence score with freshness.

The market-history cache is refreshed when its file modification age exceeds 12 hours. If refresh fails, the previous file is retained as a stale fallback. The decision gate separately checks the date of the last candle, so an old trading date remains ineligible even if the cache file was recently touched. Fundamentals for the first 60 analysed instruments are refreshed after seven days; later instruments may continue to use cached fundamentals until they enter that refresh window.

## Better sources to consider

1. **Zerodha Kite Connect** is the most direct integration for this user's own account and NSE instrument master. Historical candles are available through the historical API and quote snapshots through the quote APIs. The free Personal plan does not include historical or live market data; the paid Connect plan is currently listed at ₹500/month per API key. The access token expires daily. See:
   - https://www.kite.trade/docs/connect/v3/historical/
   - https://www.kite.trade/docs/connect/v3/market-quotes/
   - https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/kite-connect-api-faqs

   **Important licensing constraint:** Zerodha's support guidance says Kite market data cannot be displayed on other platforms because of exchange data-vending policies. Confirm that the intended private/local use is permitted before using it as the dashboard's displayed data source. Do not redistribute it.

2. **NSE official EOD reports** provide a strong daily validation source. Use the Full Bhavcopy and Security Deliverable data and the CM-UDiFF Common Bhavcopy Final files to reconcile close, volume, delivery and security identifiers:
   - https://www.nseindia.com/all-reports
   - https://www.nseindia.in/static/resources/forms-formats-members

3. **NSE Data & Analytics** sells licensed EOD, historical, corporate and real-time data products. This is the appropriate route for a production or redistributable application:
   - https://www.nseindia.com/static/nse-data-and-analytics/data-information-vending
   - https://www.nseindia.com/static/market-data/eod-historical-data-subscription
   - https://www.nseindia.com/static/market-data/real-time-data-subscription

4. **TrueData** advertises authorised NSE/BSE/MCX market-data feeds and APIs, including real-time/snapshot and corporate/fundamental data. Verify exchange permissions, exact product coverage, retention rights and display rights for the selected plan:
   - https://www.truedata.in/

## Recommended provider architecture

- `NSE_EOD`: primary daily close/volume and deliverable reconciliation, from official bhavcopy or a licensed vendor.
- `KITE`: optional personal-account market-data adapter only after confirming the user's Kite Connect plan and intended use rights.
- `YAHOO`: secondary research fallback, never silently promoted over a fresher official source.
- `FUNDAMENTALS`: separate provider from price history. Persist provider, fetched-at time, reporting period, currency and units for every fundamental field.

Every instrument record should carry `provider`, `fetched_at_utc`, `market_data_date`, `cache_age_hours`, `cache_status`, and `quality_status`. Provider failures should produce explicit stale/unknown states, not a fresh-looking record. Never blend prices from different providers in one candle series without normalising corporate actions, timezone, adjustment policy and units.

## Known follow-up work

- Add a licensed/official NSE EOD adapter and validate its UDiFF schema against the current report.
- Add a Kite adapter only after confirming the user's Kite Connect plan and intended use rights.
- Use an exchange + tradingsymbol key for instrument identity; persist the Kite instrument master daily.
- Add provider-level tests for stale cache fallback, refresh success, partial batch failures and missing instruments.
- Make the Streamlit UI show data freshness and source coverage before presenting rankings.