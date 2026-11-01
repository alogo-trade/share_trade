from __future__ import annotations

import json
import logging
from io import BytesIO
import re
import tempfile
from datetime import date, datetime
import calendar
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests

logger = logging.getLogger("stock_etf.etf_disclosures")

USER_AGENT = "share-trade-research/1.0 (ETF portfolio disclosure reader)"
TIMEOUT_SECONDS = 20
MAX_DOWNLOAD_BYTES = 40 * 1024 * 1024

# Symbols are Zerodha trading symbols. Scheme names are used only to match
# official AMC disclosure links and workbook sections, never to infer weights.
ETF_SOURCES = {
    "GOLDBEES": ("nippon", ("Nippon India ETF Gold BeES", "Gold BeES")),
    "HNGSNGBEES": ("nippon", ("Nippon India ETF Hang Seng BeES", "Hang Seng BeES")),
    "NIFTYBEES": ("nippon", ("Nippon India ETF Nifty 50 BeES", "Nifty BeES")),
    "SILVERBEES": ("nippon", ("Nippon India Silver ETF", "Silver BeES")),
    "HDFCGOLD": ("hdfc", ("HDFC Gold ETF",)),
    "HDFCSILVER": ("hdfc", ("HDFC Silver ETF",)),
    "LOWVOLIETF": ("icici", ("ICICI Prudential Nifty 100 Low Volatility 30 ETF",)),
    "NEXT50IETF": ("icici", ("ICICI Prudential Nifty Next 50 ETF",)),
    "NIFTYIETF": ("icici", ("ICICI Prudential Nifty 50 ETF",)),
    "NV20IETF": ("icici", ("ICICI Prudential Nifty 50 Value 20 ETF",)),
    "SILVERIETF": ("icici", ("ICICI Prudential Silver ETF",)),
    "MAHKTECH": ("mirae", ("Mirae Asset Hang Seng TECH ETF",)),
    "MON100": ("motilal", ("Motilal Oswal Nasdaq 100 ETF",)),
    "SBIGOLD": ("sbi", ("SBI Gold ETF",)),
}

SOURCE_PAGES = {
    "nippon": "https://mf.nipponindiaim.com/investor-service/downloads/factsheet-portfolio-and-other-disclosures",
    "hdfc": "https://www.hdfcfund.com/statutory-disclosure/portfolio/monthly-portfolio",
    "mirae": "https://www.miraeassetmf.co.in/downloads/portfolio",
    "motilal": "https://www.motilaloswalmf.com/download/month-end-portfolio",
    "sbi": "https://www.sbimf.com/portfolios",
    "icici": "https://www.icicipruamc.com/media-center/downloads?currentTabFilter=otherSchemeDisclosures&&subCatTabFilter=MonthlyPortfolioDisclosures",
    # ICICI's current disclosure UI is JS-driven. Do not scrape a third-party
    # mirror or guess an undocumented API endpoint.
}

COMMODITY_ETFS = {
    "GOLDBEES", "HDFCGOLD", "SBIGOLD", "SILVERBEES", "HDFCSILVER", "SILVERIETF"
}
DATE_PATTERNS = (
    re.compile(r"(\d{1,2})[ -](Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)[ -](20\d{2})", re.I),
    re.compile(r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(20\d{2})", re.I),
)


class _AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            self.links.append((" ".join(" ".join(self._text).split()), self._href))
            self._href = None
            self._text = []


def _normalise(value) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _parse_as_of(value: str) -> str | None:
    text = re.sub(r"(\\d)(st|nd|rd|th)\\b", r"\\1", str(value or ""), flags=re.I)
    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            if len(match.groups()) == 3:
                day, month, year = match.groups()
                return datetime.strptime(f"{day} {month} {year}", "%d %B %Y").date().isoformat()
            month, year = match.groups()
            return datetime.strptime(f"1 {month} {year}", "%d %B %Y").date().isoformat()
        except ValueError:
            try:
                if len(match.groups()) == 3:
                    day, month, year = match.groups()
                    return datetime.strptime(f"{day} {month[:3]} {year}", "%d %b %Y").date().isoformat()
                month, year = match.groups()
                month_date = datetime.strptime(f"1 {month[:3]} {year}", "%d %b %Y").date()
                return date(month_date.year, month_date.month, calendar.monthrange(month_date.year, month_date.month)[1]).isoformat()
            except ValueError:
                continue
    return None


def _download_candidates(session: requests.Session, source: str, aliases: tuple[str, ...]):
    page_url = SOURCE_PAGES.get(source)
    if not page_url:
        return [], "official_source_not_configured"
    try:
        response = session.get(page_url, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as exc:
        return [], f"source_page_fetch_failed:{type(exc).__name__}"

    parser = _AnchorParser()
    parser.feed(response.text)
    aliases_norm = [_normalise(alias) for alias in aliases]
    candidates = []
    for label, href in parser.links:
        absolute = urljoin(page_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"}:
            continue
        label_norm = _normalise(label)
        href_norm = _normalise(absolute)
        text = f"{label_norm} {href_norm}"
        is_file = bool(re.search(r"\\.(xlsx?|csv|zip)(?:$|\\?)", parsed.path, re.I))
        if not is_file:
            continue
        alias_match = any(alias and (alias in text or _normalise(alias) in label_norm) for alias in aliases_norm)
        generic_monthly = "monthly portfolio" in text or "month end portfolio" in text
        if alias_match or (source == "nippon" and generic_monthly):
            candidates.append((absolute, label or absolute))
    # Prefer links whose label directly identifies the scheme. Avoid selecting
    # several stale months or unrelated AMC documents.
    candidates.sort(key=lambda item: (not any(_normalise(a) in _normalise(item[1]) for a in aliases), item[1]), reverse=False)
    return candidates[:3], None


def _read_workbook(payload: bytes, url: str) -> list[pd.DataFrame]:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix == ".csv":
        return [pd.read_csv(BytesIO(payload), header=None, dtype=str)]
    if suffix == ".xls":
        sheets = pd.read_excel(BytesIO(payload), sheet_name=None, header=None, dtype=str, engine="xlrd")
    elif suffix in {".xlsx", ".xlsm"}:
        sheets = pd.read_excel(BytesIO(payload), sheet_name=None, header=None, dtype=str, engine="openpyxl")
    elif suffix == ".zip":
        raise ValueError("zip_disclosure_not_supported_yet")
    else:
        raise ValueError(f"unsupported_disclosure_format:{suffix or 'unknown'}")
    return list(sheets.values())


def _column_index(headers: list[str], patterns: tuple[str, ...]) -> int | None:
    normalised = [_normalise(value) for value in headers]
    for idx, header in enumerate(normalised):
        if any(_normalise(pattern) in header for pattern in patterns):
            return idx
    return None


def _extract_rows(frames: list[pd.DataFrame], aliases: tuple[str, ...], scheme_context_verified: bool = False) -> tuple[list[dict], str | None]:
    alias_tokens = [_normalise(alias) for alias in aliases]
    for frame in frames:
        values = frame.fillna("").astype(str)
        matching_rows = []
        for idx in range(len(values)):
            row_text = _normalise(" ".join(values.iloc[idx].tolist()))
            if any(token and token in row_text for token in alias_tokens):
                matching_rows.append(idx)
        if not matching_rows and scheme_context_verified:
            matching_rows = [0]
        for match_idx in matching_rows:
            for row_idx in range(match_idx, min(match_idx + 40, len(values))):
                headers = values.iloc[row_idx].tolist()
                isin_col = _column_index(headers, ("isin",))
                weight_col = _column_index(headers, ("percent to net assets", "% to net assets", "net asset percentage", "% nav", "percent to nav", "% to nav"))
                name_col = _column_index(headers, ("name of instrument", "name of security", "issuer name", "instrument name", "security name"))
                sector_col = _column_index(headers, ("industry", "sector", "industry classification"))
                if isin_col is None or weight_col is None or name_col is None:
                    continue
                rows = []
                for _, data in values.iloc[row_idx + 1:].iterrows():
                    cells = [str(value).strip() for value in data.tolist()]
                    if not any(cells):
                        break
                    isin = str(data.iloc[isin_col]).strip().upper()
                    name = str(data.iloc[name_col]).strip()
                    raw_weight = str(data.iloc[weight_col]).replace(",", "").replace("%", "").strip()
                    if not isin or isin.lower() in {"nan", "none"} or not name or name.lower() in {"nan", "none"}:
                        continue
                    try:
                        weight = float(raw_weight)
                    except (TypeError, ValueError):
                        continue
                    if not 0 < weight <= 100:
                        continue
                    sector = str(data.iloc[sector_col]).strip() if sector_col is not None else ""
                    if sector.lower() in {"nan", "none", "not applicable", "n.a."}:
                        sector = ""
                    rows.append({
                        "symbol": name,
                        "isin": isin,
                        "weight_pct": round(weight, 6),
                        "sector": sector or None,
                        "asset_class": "EQUITY" if sector else "UNCLASSIFIED",
                    })
                total = sum(row["weight_pct"] for row in rows)
                if rows and total <= 100.5:
                    return rows, None
                if rows:
                    return [], f"invalid_total_weight:{total:.3f}"
    return [], "matching_scheme_table_not_found"


def _write_cache(cache_file: Path, payload: dict) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=cache_file.parent, delete=False) as handle:
        json.dump(payload, handle, indent=2)
        temp_path = Path(handle.name)
    temp_path.replace(cache_file)


def _cache_is_fresh(path: Path, max_age_days: int) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        as_of = date.fromisoformat(str(payload.get("as_of", "")))
        return (date.today() - as_of).days <= max_age_days
    except (OSError, ValueError, TypeError):
        return False


def refresh_etf_disclosure_cache(
    holdings: dict[str, dict],
    cache_dir: str | Path,
    max_age_days: int = 40,
    force: bool = False,
) -> dict:
    """Refresh stale held-ETF caches from official AMC disclosure pages.

    Unsupported dynamic pages and unrecognised workbook layouts fail closed.
    Existing cache files are never overwritten unless a new disclosure validates.
    """
    cache_root = Path(cache_dir) / "etf_holdings"
    diagnostics = {
        "status": "not_applicable",
        "attempted": 0,
        "updated": 0,
        "unchanged": 0,
        "failed": 0,
        "unsupported": 0,
        "results": [],
    }
    held_etfs = [symbol for symbol, item in holdings.items()
                 if str(item.get("isin", "")).upper().startswith("INF")]
    if not held_etfs:
        return diagnostics

    diagnostics["status"] = "partial"
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
    for symbol in sorted(held_etfs):
        cache_file = cache_root / f"{symbol}.json"
        if not force and cache_file.exists() and _cache_is_fresh(cache_file, max_age_days):
            diagnostics["unchanged"] += 1
            diagnostics["results"].append({"symbol": symbol, "status": "fresh_cache"})
            continue

        source_info = ETF_SOURCES.get(symbol)
        if not source_info:
            diagnostics["unsupported"] += 1
            diagnostics["results"].append({"symbol": symbol, "status": "unsupported_symbol"})
            continue
        source, aliases = source_info

        # Precious-metal ETFs have no equity-sector look-through. Persist their
        # verified asset class, but do not invent security constituents.
        diagnostics["attempted"] += 1
        candidates, error = _download_candidates(session, source, aliases)
        if error:
            diagnostics["failed"] += 1
            diagnostics["results"].append({"symbol": symbol, "status": "failed", "reason": error})
            continue
        if not candidates:
            diagnostics["unsupported"] += 1
            diagnostics["results"].append({"symbol": symbol, "status": "unsupported", "reason": "no_matching_official_download_link"})
            continue

        updated = False
        last_error = "no_valid_candidate"
        for url, label in candidates:
            try:
                response = session.get(url, timeout=TIMEOUT_SECONDS)
                response.raise_for_status()
                if len(response.content) > MAX_DOWNLOAD_BYTES:
                    raise ValueError("disclosure_exceeds_download_limit")
                as_of = _parse_as_of(f"{label} {url}")
                if symbol in COMMODITY_ETFS:
                    if not as_of:
                        last_error = "commodity_disclosure_as_of_date_not_found"
                        continue
                    payload = {
                        "source": url,
                        "as_of": as_of,
                        "underlying_asset_class": "COMMODITY",
                        "holdings": [],
                    }
                    _write_cache(cache_file, payload)
                    diagnostics["updated"] += 1
                    diagnostics["results"].append({"symbol": symbol, "status": "updated_asset_class_only", "as_of": as_of, "source": url})
                    updated = True
                    break
                frames = _read_workbook(response.content, url)
                link_confirms_scheme = any(_normalise(alias) in _normalise(f"{label} {url}") for alias in aliases)
                rows, parse_error = _extract_rows(frames, aliases, scheme_context_verified=link_confirms_scheme)
                if parse_error:
                    last_error = parse_error
                    continue
                if not as_of:
                    # Use a date printed in the workbook, not the download date.
                    for frame in frames:
                        text = " ".join(frame.fillna("").astype(str).values.flatten().tolist()[:500])
                        as_of = _parse_as_of(text)
                        if as_of:
                            break
                if not as_of:
                    last_error = "disclosure_as_of_date_not_found"
                    continue
                payload = {
                    "source": url,
                    "as_of": as_of,
                    "underlying_asset_class": "EQUITY",
                    "holdings": rows,
                }
                _write_cache(cache_file, payload)
                diagnostics["updated"] += 1
                diagnostics["results"].append({
                    "symbol": symbol, "status": "updated", "as_of": as_of,
                    "constituents": len(rows), "weight_total_pct": round(sum(r["weight_pct"] for r in rows), 3),
                    "source": url,
                })
                updated = True
                break
            except Exception as exc:
                last_error = f"{type(exc).__name__}:{exc}"
                logger.warning("ETF disclosure refresh failed for %s from %s: %s", symbol, url, exc)
        if not updated:
            diagnostics["failed"] += 1
            diagnostics["results"].append({"symbol": symbol, "status": "failed", "reason": last_error})

    diagnostics["status"] = (
        "complete" if diagnostics["failed"] == 0 and diagnostics["unsupported"] == 0
        else "partial"
    )
    logger.info(
        "ETF disclosure ingestion: attempted=%d updated=%d unchanged=%d failed=%d unsupported=%d",
        diagnostics["attempted"], diagnostics["updated"], diagnostics["unchanged"],
        diagnostics["failed"], diagnostics["unsupported"],
    )
    return diagnostics
