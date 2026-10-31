# US market research

The US research pipeline runs independently of the existing NSE pipeline.

## Universe model

Two JSON files are used:

- `data/us_candidate_universe.json`: curated discovery watchlist. Every row remains `zerodha_eligible: false` until account-level verification.
- `data/zerodha_us_universe.json`: account-verified instruments only. Add a symbol here only after confirming it is visible and eligible in the user's Kite Global Stocks account.

The candidate list records:
- `primary_exchange`: the instrument's primary US listing venue
- `india_inx_exchange_supported`: whether the exchange is listed in the India INX Global Access exchange directory
- `india_inx_symbol_verified`: individual symbol lookup has been completed in the trading terminal
- `zerodha_eligible`: account-level eligibility confirmed in Kite

## India INX interpretation

The candidate file was mapped against the India INX Global Access exchange directory on 2026-10-31. The directory lists NASDAQ, NYSE and NYSE Arca among supported US venues. This is exchange-level compatibility only. It does not establish that every listed security is available, enabled, or permitted for every account.

Individual security verification requires searching the exact symbol in the India INX Global Access or Zerodha Kite international trading terminal. Do not set `india_inx_symbol_verified` or `zerodha_eligible` to true based solely on exchange support, a public quote, or a Yahoo Finance ticker.

The candidate file is a curated starter watchlist, not an exhaustive US exchange universe or official broker instrument master.

## Add a verified symbol

After confirming the instrument in the user's Kite Global Stocks account, add it to `data/zerodha_us_universe.json`:

```json
{
  "instruments": [
    {
      "symbol": "AAPL",
      "name": "Apple",
      "type": "STOCK",
      "zerodha_eligible": true,
      "verified_on": "YYYY-MM-DD"
    }
  ]
}
```

The loader merges both files, de-duplicates symbols, and gives verified entries precedence. Verified instruments are prioritised when the analysis limit is applied. Within each eligibility tier, stocks and ETFs are interleaved so a small limit can retain both asset types.

## Data and scope

Yahoo Finance supplies historical prices, fundamentals and available news. These are research data, not Zerodha quotes or execution prices. The feature does not place US orders or sync US holdings.

## Configuration

- `ENABLE_US_RESEARCH=true`
- `ZERODHA_US_UNIVERSE_PATH=data/zerodha_us_universe.json`
- `US_CANDIDATE_UNIVERSE_PATH=data/us_candidate_universe.json`
- `US_ANALYSIS_UNIVERSE_LIMIT=30`

The default limit is conservative because Yahoo Finance may throttle large batches.
