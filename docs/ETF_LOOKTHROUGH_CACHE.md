# ETF portfolio look-through cache

The portfolio engine reads ETF look-through data from local cache only. It does not
make network requests during analysis, including in `US_ONLY` mode.

## File location

Create one JSON file per held ETF:

```text
<STOCK_CACHE_DIR>/etf_holdings/<ZERODHA_TRADINGSYMBOL>.json
```

For example, with the default cache directory, the file for `NIFTYETF` is
`cache/etf_holdings/NIFTYETF.json`.

## Equity ETF example

```json
{
  "source": "AMC factsheet URL or exchange disclosure",
  "as_of": "2026-10-31",
  "underlying_asset_class": "EQUITY",
  "holdings": [
    {
      "symbol": "RELIANCE",
      "weight_pct": 8.42,
      "sector": "Energy",
      "asset_class": "EQUITY"
    },
    {
      "symbol": "HDFCBANK",
      "weight_pct": 7.91,
      "sector": "Financial Services",
      "asset_class": "EQUITY"
    }
  ]
}
```

- `weight_pct` is a percentage from 0 to 100, not a decimal fraction.
- The sum of constituent weights must not exceed 100.5%. Invalid files are ignored.
- Missing residual weight is attributed to `underlying_asset_class`, or marked
  `UNCLASSIFIED` if that field is absent.
- A sector is counted only for an `EQUITY` constituent with a non-empty sector.
- Keep the provider's reported sector taxonomy. Do not infer sectors from symbols.

## Commodity ETF example

Gold and silver ETFs should not be forced into equity sectors. For a fund with no
security-level constituents, use:

```json
{
  "source": "AMC factsheet URL or exchange disclosure",
  "as_of": "2026-10-31",
  "underlying_asset_class": "COMMODITY",
  "holdings": []
}
```

Allowed underlying asset classes are `EQUITY`, `COMMODITY`, `FIXED_INCOME`,
`CASH`, and `OTHER`.

## Report fields

- `sector_exposure_pct`: direct stock sectors plus sector-attributed ETF constituents,
  measured against total portfolio value.
- `direct_stock_sector_exposure_pct`: sector exposure from directly held stocks.
- `etf_lookthrough_sector_exposure_pct`: sector exposure contributed by ETF constituents.
- `etf_underlying_asset_class_exposure_pct`: ETF look-through asset classes, measured
  against total portfolio value.
- `etf_lookthrough`: ETF cache coverage, value coverage, constituent-weight coverage,
  and source/as-of provenance.

A missing cache is not treated as zero exposure. Its value remains in
`sector_unclassified_current_value` and is reported in
`etf_value_without_cached_data`. Cache coverage being complete means every held ETF
has a cache entry; it does not imply every constituent has a known sector.
