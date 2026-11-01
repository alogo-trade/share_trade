# ETF portfolio look-through cache

The portfolio engine refreshes stale ETF look-through data at runtime from configured
official AMC disclosure pages, then reads the normalized local cache. Network refresh
can be disabled with `ETF_DISCLOSURE_REFRESH_ENABLED=false`. The default cache freshness
window is 40 days and can be changed with `ETF_DISCLOSURE_MAX_AGE_DAYS`.

The ingestion layer deliberately fails closed. It does not use third-party portfolio
aggregators, infer constituents from an ETF's benchmark, or replace a valid cache when
a download or parse fails. Unsupported dynamic pages and unknown workbook layouts are
reported in `portfolio.summary.etf_ingestion`.

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


## Runtime ingestion implementation

The runtime reader is `stock_etf/etf_disclosures.py`. It currently has official
source-page mappings for Nippon India, HDFC, ICICI Prudential, Mirae Asset, SBI and
Motilal Oswal. AMC pages that render their download links only through client-side
JavaScript may return an explicit unsupported/failure diagnostic until a verified
adapter is added. In particular, a source-page mapping does not imply that every
scheme's workbook format is already supported.

Supported workbook formats are XLS, XLSX and CSV, including ZIP bundles containing
those spreadsheet formats. XLSX and XLS parsing use the `openpyxl` and `xlrd`
dependencies. PDF disclosures are not yet parsed. A workbook is accepted only when a scheme-specific section can be identified,
a name/ISIN/weight table is found, and its constituent weights validate.

The report includes attempted, updated, unchanged, failed and unsupported counts,
plus per-symbol source, reporting-date and age details. A source older than the
configured freshness window is still retained when valid, but marked stale and retried
at most once per day. Commodity ETFs are stored as
`COMMODITY` exposure only when a dated official disclosure link is discovered.
