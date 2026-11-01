from __future__ import annotations
import logging, urllib.parse, requests, feedparser, re
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

logger = logging.getLogger('stock_etf')
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36',
    'Accept': 'application/rss+xml, application/xml, text/xml, */*',
}


def _clean_text(value):
    import html
    import re
    if value is None:
        return ''
    text = html.unescape(str(value))
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.I|re.S)
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.I|re.S)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _google(query):
    q = urllib.parse.quote(query)
    return f'https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en'


def _bing(query):
    q = urllib.parse.quote(query)
    return f'https://www.bing.com/news/search?q={q}&format=rss'


def _yahoo_search(symbol):
    q = urllib.parse.quote(symbol)
    return f'https://query2.finance.yahoo.com/v1/finance/search?q={q}&newsCount=10&quotesCount=0'


def _google_search_link(query, label=None):
    q = urllib.parse.quote(query)
    return {
        'label': label or query,
        'query': query,
        'url': f'https://news.google.com/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en'
    }


# These are deliberately broader than finance/ticker searches. They capture events
# that can affect earnings, input costs, regulation, supply chains, demand, risk premia,
# and business continuity even when a company name never appears in the headline.
BASE_THEMES = [
    ('INDIA_MACRO', 'India RBI interest rates inflation rupee monsoon GDP policy economy'),
    ('INDIA_POLICY', 'India government policy regulation taxation GST import export tariffs infrastructure policy'),
    ('INDIA_ENERGY', 'India crude oil natural gas electricity coal fuel energy prices supply'),
    ('GLOBAL_MACRO', 'US Federal Reserve interest rates inflation dollar global economy recession growth'),
    ('GLOBAL_TRADE', 'global trade tariffs sanctions export controls China US Europe supply chain disruption'),
    ('GEOPOLITICS', 'geopolitics war conflict Middle East Russia Ukraine China Taiwan shipping sanctions'),
    ('ENVIRONMENT', 'India global climate environment pollution emissions carbon climate regulation water drought flood cyclone heatwave'),
    ('EXTREME_WEATHER', 'India monsoon cyclone flood drought heatwave extreme weather agriculture infrastructure'),
    ('COMMODITIES', 'crude oil metals copper aluminium steel gold commodity prices global supply'),
    ('TECH_POLICY', 'AI regulation semiconductor cybersecurity technology data privacy global policy'),
]

SECTOR_THEMES = {
    'Financial Services': 'India banking RBI credit growth interest rates financial regulation NBFC',
    'Banks': 'India banks RBI interest rates credit deposit NPA financial regulation',
    'Information Technology': 'India IT services AI cloud cybersecurity technology spending US Europe outsourcing',
    'Technology': 'AI semiconductor cybersecurity cloud technology regulation',
    'Healthcare': 'India pharma healthcare drug regulation US FDA patents pricing healthcare policy',
    'Pharmaceuticals': 'India pharma US FDA drug approvals patents pricing healthcare regulation',
    'Automobile': 'India auto electric vehicles EV batteries tariffs supply chain fuel prices emissions regulation',
    'Automotive': 'India auto electric vehicles EV batteries fuel prices emissions regulation',
    'Energy': 'India energy crude oil natural gas electricity renewable power coal energy policy',
    'Oil & Gas': 'crude oil natural gas OPEC India fuel prices geopolitics energy policy',
    'Metals': 'steel aluminium copper iron ore metals China commodity prices tariffs mining policy',
    'Consumer Cyclical': 'India consumer demand inflation rural demand urban consumption interest rates',
    'Consumer Defensive': 'India food inflation FMCG consumer demand monsoon agriculture commodity prices',
    'Industrials': 'India infrastructure capital expenditure manufacturing logistics supply chain government policy',
    'Construction': 'India infrastructure construction cement housing interest rates government spending',
    'Telecommunication Services': 'India telecom spectrum regulation 5G tariff cybersecurity technology',
    'Utilities': 'India electricity power demand renewable energy coal natural gas regulation',
    'Real Estate': 'India housing property interest rates real estate regulation infrastructure',
    'Basic Materials': 'India metals chemicals commodities energy environment regulation',
}


def _query_rows(query, category, provider, symbol='THEMATIC'):
    return {'query': query, 'category': category, 'provider': provider, 'symbol': symbol}

def _published_at(value):
    """Parse RSS/ISO publication timestamps to an aware UTC datetime."""
    if not value:
        return None
    raw = str(value).strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _canonical_news_key(item):
    link = str(item.get("link") or "").strip()
    if link:
        parsed = urllib.parse.urlsplit(link)
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        query = [
            (key, value) for key, value in query
            if not key.lower().startswith("utm_")
            and key.lower() not in {"fbclid", "gclid", "ocid", "ref", "source"}
        ]
        return urllib.parse.urlunsplit((
            parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"),
            urllib.parse.urlencode(sorted(query)), ""
        ))
    title = re.sub(r"[^a-z0-9]+", " ", str(item.get("title") or "").lower()).strip()
    return title


def _dedupe(items, limit, max_age_days=30, now=None):
    """Remove duplicate and old articles, then prefer the newest publications."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max(0, int(max_age_days)))
    seen = set()
    accepted = []
    for item in items:
        key = _canonical_news_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        published = _published_at(item.get("published"))
        if published and (published < cutoff or published > now + timedelta(days=1)):
            continue
        row = dict(item)
        row["published_at_utc"] = published.isoformat() if published else None
        row["news_freshness"] = "DATED" if published else "UNKNOWN_DATE"
        row["_sort_timestamp"] = published.timestamp() if published else float("-inf")
        accepted.append(row)
    accepted.sort(key=lambda row: row["_sort_timestamp"], reverse=True)
    for row in accepted:
        row.pop("_sort_timestamp", None)
    return accepted[:limit]


def _feed(url, provider, symbol, failures):
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            failures.append(f'{provider}:{symbol}:HTTP {r.status_code}')
            return []
        parsed = feedparser.parse(r.content)
        if getattr(parsed, 'bozo', False) and not parsed.entries:
            failures.append(f'{provider}:{symbol}:invalid/empty RSS')
            return []
        rows = []
        for e in parsed.entries[:8]:
            source = e.get('source') or {}
            rows.append({
                'title': _clean_text(e.get('title', '')),
                'source': source.get('title', provider) if isinstance(source, dict) else provider,
                'published': e.get('published', e.get('updated', '')),
                'summary': _clean_text(e.get('summary', '')),
                'link': e.get('link', ''),
                'symbol': symbol,
                'provider': provider,
            })
        if not rows:
            failures.append(f'{provider}:{symbol}:0 RSS entries')
        return rows
    except Exception as exc:
        failures.append(f'{provider}:{symbol}:{type(exc).__name__}: {exc}')
        return []


def _yahoo(symbol, failures):
    try:
        r = requests.get(_yahoo_search(symbol), headers=HEADERS, timeout=10)
        if r.status_code != 200:
            failures.append(f'Yahoo:{symbol}:HTTP {r.status_code}')
            return []
        data = r.json()
        rows = []
        for e in data.get('news', [])[:8]:
            ts = e.get('providerPublishTime')
            published = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if isinstance(ts, (int, float)) else str(ts or '')
            rows.append({
                'title': _clean_text(e.get('title', '')),
                'source': e.get('publisher', 'Yahoo Finance'),
                'published': published,
                'summary': _clean_text(e.get('title', '')),
                'link': e.get('link', ''),
                'symbol': symbol,
                'provider': 'yahoo_finance_search',
            })
        if not rows:
            failures.append(f'Yahoo:{symbol}:0 news entries')
        return rows
    except Exception as exc:
        failures.append(f'Yahoo:{symbol}:{type(exc).__name__}: {exc}')
        return []


def _yfinance_news(symbol, failures):
    """Use yfinance's own news adapter as an additional Yahoo-compatible path."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(f'{symbol}.NS')
        raw = ticker.news or []
        rows = []
        for e in raw[:8]:
            content = e.get('content') or {}
            title = content.get('title') or e.get('title') or ''
            provider = (content.get('provider') or {}).get('displayName') if isinstance(content.get('provider'), dict) else None
            url_obj = content.get('canonicalUrl') or content.get('clickThroughUrl') or {}
            link = url_obj.get('url', '') if isinstance(url_obj, dict) else str(url_obj or e.get('link', ''))
            rows.append({
                'title': _clean_text(title),
                'source': provider or 'Yahoo Finance',
                'published': content.get('pubDate') or e.get('pubDate', ''),
                'summary': _clean_text(content.get('summary') or title),
                'link': link,
                'symbol': symbol,
                'provider': 'yfinance_news',
            })
        if not rows:
            failures.append(f'yfinance:{symbol}:0 news entries')
        return rows
    except Exception as exc:
        failures.append(f'yfinance:{symbol}:{type(exc).__name__}: {exc}')
        return []


def fetch_news(instruments, limit=50, thematic_limit=24, max_age_days=30):
    """Collect company, sector, India, global, geopolitical, commodity and environmental news.

    instruments may be shortlisted market-data records or plain symbols. Thematic queries
    are intentionally independent of ticker mentions so indirect material events are captured.
    """
    failures = []
    out = []
    rows = []
    for item in instruments[:20]:
        if isinstance(item, dict):
            rows.append(item)
        else:
            rows.append({'symbol': str(item).upper(), 'name': str(item).upper()})

    symbols = [str(x.get('symbol','')).upper() for x in rows if x.get('symbol')]

    # Direct instrument coverage.
    for row in rows:
        symbol = str(row.get('symbol','')).upper()
        name = str(row.get('name') or symbol).strip()
        for query in [f'"{symbol}" NSE India', f'"{name}" India']:
            out.extend(_feed(_google(query), 'Google News', symbol, failures))
            if len(out) >= limit:
                break
        if len(out) >= limit:
            break

    # Sector-specific coverage derived from the actual shortlisted instruments.
    sectors = []
    for row in rows:
        sector = str(row.get('sector') or row.get('industry') or '').strip()
        if sector and sector not in sectors:
            sectors.append(sector)
    sector_queries = []
    for sector in sectors:
        q = SECTOR_THEMES.get(sector)
        if q:
            sector_queries.append((f'SECTOR_{sector}', q))
    for category, query in sector_queries[:8]:
        out.extend(_feed(_google(query), 'Google News', category, failures))
        if len(out) >= limit:
            break

    # Broad event-driven coverage. These can move shares without mentioning the ticker.
    if len(out) < limit:
        for category, query in BASE_THEMES[:thematic_limit]:
            out.extend(_feed(_google(query), 'Google News', category, failures))
            if len(out) >= limit:
                break

    # Bing is a fallback for the most important thematic groups and may surface different publishers.
    if len(out) < min(20, limit):
        for category, query in BASE_THEMES[:8]:
            out.extend(_feed(_bing(query), 'Bing News', category, failures))
            if len(out) >= limit:
                break

    # Yahoo/yfinance are retained for company-specific finance news.
    if len(out) < min(20, limit):
        for symbol in symbols[:10]:
            out.extend(_yfinance_news(symbol, failures))
            if len(out) < limit:
                out.extend(_yahoo(symbol, failures))
            if len(out) >= limit:
                break

    before_filter = len(out)
    out = _dedupe(out, limit, max_age_days=max_age_days)
    logger.info('News freshness filter: received=%d, retained=%d, max_age_days=%d.', before_filter, len(out), max_age_days)
    logger.info('News feed loaded: %d entries from %d symbols + %d sectors + %d thematic queries.',
                len(out), len(symbols), len(sector_queries[:8]), min(thematic_limit, len(BASE_THEMES)))
    if not out:
        logger.warning('News providers returned no usable articles. Failures: %s', '; '.join(failures[-20:]))
    return out, failures


def search_links(instruments, limit=20):
    links = []
    seen = set()
    for item in instruments:
        if isinstance(item, dict):
            symbol = str(item.get('symbol','')).upper()
            name = str(item.get('name') or symbol)
        else:
            symbol = str(item).upper(); name = symbol
        for query, label in [
            (f'"{symbol}" NSE India', f'{symbol} company'),
            (f'"{name}" India', f'{symbol} company/name'),
        ]:
            key = query.lower()
            if key not in seen:
                links.append(_google_search_link(query, label))
                seen.add(key)
    for category, query in BASE_THEMES[:8]:
        links.append(_google_search_link(query, category.replace('_', ' ').title()))
    return links[:limit]



US_MARKET_THEMES = [
    ("US_MACRO", "United States Federal Reserve interest rates inflation employment GDP economy"),
    ("US_POLICY", "United States federal government fiscal policy regulation tariffs trade policy"),
    ("US_TECH", "United States technology semiconductor AI cloud cybersecurity regulation"),
    ("US_ENERGY", "United States crude oil natural gas electricity energy markets"),
    ("US_GEOPOLITICS", "United States global geopolitics sanctions trade conflict supply chains"),
    ("US_CONSUMER", "United States consumer spending retail housing credit employment"),
    ("US_HEALTHCARE", "United States healthcare pharmaceuticals FDA drug pricing policy"),
    ("US_MARKET", "United States stock market earnings corporate guidance market volatility"),
]


def fetch_us_market_news(limit=40, max_age_days=14):
    """Collect fresh US news with category balancing to prevent one theme dominating."""
    failures = []
    items = []
    category_order = [category for category, _ in US_MARKET_THEMES]
    for category, query in US_MARKET_THEMES:
        rows = _feed(_google(query), "Google News", category, failures)
        for row in rows:
            row["category"] = category
        items.extend(rows)

    # Use Bing only for themes with no Google results, rather than duplicating
    # already-covered categories and exhausting the result limit early.
    present = {row.get("category") for row in items}
    for category, query in US_MARKET_THEMES:
        if category in present:
            continue
        rows = _feed(_bing(query), "Bing News", category, failures)
        for row in rows:
            row["category"] = category
        items.extend(rows)

    deduped = _dedupe(
        items,
        max(limit * max(1, len(category_order)), limit),
        max_age_days=max_age_days,
    )
    buckets = {
        category: [row for row in deduped if row.get("category") == category]
        for category in category_order
    }
    balanced = []
    while len(balanced) < max(0, limit) and any(buckets.values()):
        for category in category_order:
            if buckets[category] and len(balanced) < limit:
                balanced.append(buckets[category].pop(0))
    logger.info(
        "US news freshness/category filter: received=%d retained=%d categories=%d max_age_days=%d",
        len(items), len(balanced), len({row.get("category") for row in balanced}), max_age_days,
    )
    return balanced, failures
