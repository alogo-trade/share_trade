# US market research

The US research pipeline runs independently of the existing NSE pipeline.

## Universe model

Two JSON files are used:

- `data/us_candidate_universe.json`: curated discovery watchlist of US stocks and ETFs. Every row is unverified for Zerodha availability and is marked `zerodha_eligible: false`.
- `data/zerodha_us_universe.json`: account-verified instruments only. Add a symbol here only after confirming it is visible and eligible in the user's Kite Global Stocks account. Set `zerodha_eligible: true` and record the verification date.

The loader merges both lists, de-duplicates symbols, and gives verified entries precedence. Verified instruments are prioritised when `US_ANALYSIS_UNIVERSE_LIMIT` is reached. The candidate file is a curated starter watchlist, not an exhaustive US exchange universe and not an official Zerodha instrument master.

## Add a verified symbol

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

Move a symbol from the candidate file only after checking it in Kite. Do not infer eligibility from a Yahoo Finance quote or US exchange listing status.

## Data and scope

Yahoo Finance supplies historical prices, fundamentals and available news. These are research data, not Zerodha quotes or execution prices. The feature does not place US orders, sync US holdings, or establish broker availability. The candidate watchlist provides broad coverage across large-cap technology, healthcare, financials, consumer, industrials, energy, broad-market ETFs, sector ETFs and selected thematic ETFs.

## Configuration

- `ENABLE_US_RESEARCH=true`
- `ZERODHA_US_UNIVERSE_PATH=data/zerodha_us_universe.json`
- `US_CANDIDATE_UNIVERSE_PATH=data/us_candidate_universe.json`
- `US_ANALYSIS_UNIVERSE_LIMIT=30`

The default limit is intentionally conservative because Yahoo Finance may throttle large batches.
