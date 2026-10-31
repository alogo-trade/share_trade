from __future__ import annotations
import json, logging, math, time
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf
from .universe import yf_symbol
logger=logging.getLogger('stock_etf')

class YahooCircuitBreaker:
    def __init__(self, cooldown_seconds=300):
        self.cooldown_seconds=cooldown_seconds; self.opened_at=0.0; self.reason=''
    @property
    def open(self): return (time.time()-self.opened_at)<self.cooldown_seconds
    def trip(self, reason):
        self.opened_at=time.time(); self.reason=str(reason)
        logger.warning('Yahoo circuit breaker opened for %ss: %s', self.cooldown_seconds, reason)

CB=YahooCircuitBreaker()

def _safe_float(x):
    try:
        if x is None or (isinstance(x,str) and not x.strip()): return None
        x=float(x); return x if math.isfinite(x) else None
    except Exception: return None

def _pct(x):
    v=_safe_float(x); return None if v is None else v*100

def _yield_pct(x):
    """Normalise Yahoo yield values supplied as either ratios or percentages."""
    v=_safe_float(x)
    if v is None: return None
    return v*100 if abs(v)<=1 else v

def _max_drawdown(close):
    s=close.dropna()
    return _safe_float((s/s.cummax()-1).min()*100) if not s.empty else None

def _rsi(close,n=14):
    d=close.diff(); up=d.clip(lower=0); down=-d.clip(upper=0)
    a=up.ewm(alpha=1/n,adjust=False).mean(); b=down.ewm(alpha=1/n,adjust=False).mean()
    rs=a/b.replace(0,np.nan); return _safe_float((100-(100/(1+rs))).iloc[-1])

def _atr(df,n=14):
    h,l,c=df['High'],df['Low'],df['Close']; pc=c.shift(1)
    tr=pd.concat([h-l,(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1)
    return _safe_float(tr.rolling(n).mean().iloc[-1])

def _annualized_vol(close,days=252):
    r=close.pct_change().dropna(); return _safe_float(r.tail(days).std()*np.sqrt(252)*100)

def _cagr(close,years):
    if len(close)<years*200:return None
    start=_safe_float(close.iloc[-min(len(close)-1,years*252)]); end=_safe_float(close.iloc[-1])
    if start is None or end is None or start<=0:return None
    return _safe_float(((end/start)**(1/years)-1)*100)

def _technicals(df):
    c=pd.to_numeric(df['Close'],errors='coerce').dropna(); v=pd.to_numeric(df.get('Volume',0),errors='coerce').fillna(0)
    if c.empty:return {}
    last=_safe_float(c.iloc[-1]); sma20=_safe_float(c.rolling(20).mean().iloc[-1]); sma50=_safe_float(c.rolling(50).mean().iloc[-1]); sma200=_safe_float(c.rolling(200).mean().iloc[-1]); avg20=_safe_float(v.rolling(20).mean().iloc[-1])
    def ret(n):
        if len(c)<=n:return None
        prev=_safe_float(c.iloc[-n-1]); return _safe_float((last/prev-1)*100) if last is not None and prev not in (None,0) else None
    atr=_atr(df)
    return {'price':last,'return_1w_pct':ret(5),'return_1m_pct':ret(21),'return_3m_pct':ret(63),'return_6m_pct':ret(126),'return_1y_pct':ret(252),'cagr_3y_pct':_cagr(c,3),'sma20':sma20,'sma50':sma50,'sma200':sma200,
            'distance_sma20_pct':_safe_float((last/sma20-1)*100) if last is not None and sma20 else None,'distance_sma50_pct':_safe_float((last/sma50-1)*100) if last is not None and sma50 else None,'distance_sma200_pct':_safe_float((last/sma200-1)*100) if last is not None and sma200 else None,'rsi14':_rsi(c),'atr14':atr,'atr_pct':_safe_float(atr/last*100) if atr is not None and last not in (None,0) else None,'volatility_pct':_annualized_vol(c),'max_drawdown_pct':_max_drawdown(c),'volume_ratio_20d':_safe_float(v.iloc[-1]/avg20) if avg20 not in (None,0) else None,'avg_volume_20d':avg20,'high_52w':_safe_float(c.tail(252).max()),'low_52w':_safe_float(c.tail(252).min()),'data_points':int(len(c)),'last_date':str(c.index[-1].date())}

def _history_batch(symbols, period='3y', batch_size=40, retries=1, cache_dir='cache/market', cache_ttl_hours=12, request_timeout_seconds=15):
    """
    Load historical prices with a bounded cache lifetime.

    Expired cache entries are refreshed from Yahoo. If refresh fails,
    the old cache is retained for diagnostics but marked STALE_CACHE so
    the decision gate cannot treat it as current market data.
    """
    out = {}
    stale = {}
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    missing = []
    now = time.time()

    for symbol in symbols:
        path = cache / (symbol.replace('/', '_') + '.parquet')
        if not path.exists():
            missing.append(symbol)
            continue
        try:
            age_hours = max(0.0, (now - path.stat().st_mtime) / 3600.0)
            frame = pd.read_parquet(path)
            if frame is None or frame.empty:
                missing.append(symbol)
                continue
            if age_hours <= cache_ttl_hours:
                frame.attrs['cache_status'] = 'FRESH_CACHE'
                frame.attrs['cache_age_hours'] = round(age_hours, 2)
                frame.attrs['data_source'] = 'Yahoo Finance'
                out[symbol] = frame
            else:
                stale[symbol] = frame
                missing.append(symbol)
        except Exception as exc:
            logger.warning('Could not read cached history for %s: %s', symbol, exc)
            missing.append(symbol)

    def retain_stale(symbol):
        frame = stale.get(symbol)
        if frame is not None and symbol not in out:
            frame.attrs['cache_status'] = 'STALE_CACHE'
            cache_path = cache / (symbol.replace('/', '_') + '.parquet')
            try:
                age_hours = max(0.0, (now - cache_path.stat().st_mtime) / 3600.0)
            except OSError:
                # Cache may be evicted between initial read and fallback.
                logger.warning('Stale cache file disappeared for %s; skipping fallback.', symbol)
                return
            frame.attrs['cache_age_hours'] = round(age_hours, 2)
            frame.attrs['data_source'] = 'Yahoo Finance (stale cache fallback)'
            out[symbol] = frame

    if not missing:
        return out

    if CB.open:
        logger.warning('Yahoo circuit breaker active; stale cache fallback for %d symbols.', len(missing))
        for symbol in missing:
            retain_stale(symbol)
        return out

    def download_isolated(group):
        """Download a group, recursively splitting failed batches to isolate bad tickers."""
        try:
            data = yf.download(
                group, period=period, auto_adjust=True, actions=False,
                progress=False, threads=False, group_by='ticker',
                timeout=request_timeout_seconds
            )
            if data is None or data.empty:
                raise RuntimeError('empty Yahoo batch response')
            frames = {}
            for symbol in group:
                try:
                    frame = data if len(group) == 1 else data[symbol]
                    if frame is not None and not frame.empty:
                        frame = frame.dropna(how='all')
                        if not frame.empty:
                            frames[symbol] = frame
                except Exception as exc:
                    logger.warning('Yahoo returned no usable history for %s: %s', symbol, exc)
            missing_symbols = [symbol for symbol in group if symbol not in frames]
            if missing_symbols and len(group) > 1:
                logger.warning(
                    'Yahoo returned incomplete batch data; isolating %d missing symbols.',
                    len(missing_symbols)
                )
                frames.update(download_isolated(missing_symbols))
            return frames
        except Exception as exc:
            message = str(exc)
            if 'Too Many Requests' in message or 'RateLimit' in message or '429' in message:
                CB.trip(message)
                return {}
            if len(group) == 1:
                logger.warning('Yahoo history failed for %s after bounded request: %s', group[0], message)
                return {}
            midpoint = len(group) // 2
            logger.warning(
                'Yahoo batch of %d failed; splitting into %d and %d symbols: %s',
                len(group), midpoint, len(group) - midpoint, message
            )
            left = download_isolated(group[:midpoint])
            if CB.open:
                return left
            right = download_isolated(group[midpoint:])
            left.update(right)
            return left

    for start in range(0, len(missing), batch_size):
        batch = missing[start:start + batch_size]
        refreshed = download_isolated(batch)
        for symbol, frame in refreshed.items():
            frame.attrs['cache_status'] = 'REFRESHED'
            frame.attrs['cache_age_hours'] = 0.0
            frame.attrs['data_source'] = 'Yahoo Finance'
            out[symbol] = frame
            try:
                frame.to_parquet(cache / (symbol.replace('/', '_') + '.parquet'))
            except Exception as exc:
                logger.debug('Could not cache %s history: %s', symbol, exc)
        logger.info('Yahoo batch history: %d/%d symbols refreshed.', len(refreshed), len(batch))
        for symbol in batch:
            if symbol not in refreshed:
                retain_stale(symbol)
        if CB.open:
            for symbol in missing[start + len(batch):]:
                retain_stale(symbol)
            break
    return out

def _info(ticker, cache_dir='cache/fundamentals', cache_ttl_hours=168):
    """Load Yahoo fundamentals with a seven-day cache and stale fallback."""
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / (ticker.replace('/', '_') + '.json')
    cached = None
    if path.exists():
        try:
            cached = json.loads(path.read_text(encoding='utf-8'))
            age_hours = max(0.0, (time.time() - path.stat().st_mtime) / 3600.0)
            if age_hours <= cache_ttl_hours:
                return cached
        except Exception:
            cached = None
    if CB.open:
        return cached or {}
    for attempt in range(2):
        try:
            info = yf.Ticker(ticker).info or {}
            if info:
                path.write_text(json.dumps(info, default=str), encoding='utf-8')
                return info
            break
        except Exception as exc:
            message = str(exc)
            if 'Too Many Requests' in message or 'RateLimit' in message or '429' in message:
                CB.trip(message)
                break
            if attempt == 0:
                time.sleep(2)
    return cached or {}

def _merge_record(row, df, info):
    symbol=str(row.get('symbol','')).upper(); ysym=yf_symbol(row); typ=str(row.get('type','STOCK')).upper()
    if df is None or df.empty:return {'symbol':symbol,'yf_symbol':ysym,'instrument_type':typ,'data_confidence':0,'error':'no historical data'}
    df=df.dropna(subset=['Close'])
    if df.empty:return {'symbol':symbol,'yf_symbol':ysym,'instrument_type':typ,'data_confidence':0,'error':'no valid close data'}
    tech=_technicals(df)
    rec={'symbol':symbol,'yf_symbol':ysym,'instrument_type':typ,'name':info.get('longName') or info.get('shortName') or row.get('name') or symbol,'sector':info.get('sector') or row.get('sector'),'industry':info.get('industry') or row.get('industry'),'currency':info.get('currency'),'data_source':df.attrs.get('data_source', 'Yahoo Finance'),'cache_status':df.attrs.get('cache_status', 'UNKNOWN'),'cache_age_hours':df.attrs.get('cache_age_hours'),
         'market_cap_inr':_safe_float(info.get('marketCap')),'beta':_safe_float(info.get('beta')),'pe':_safe_float(info.get('trailingPE')),'forward_pe':_safe_float(info.get('forwardPE')),'pb':_safe_float(info.get('priceToBook')),'ev_ebitda':_safe_float(info.get('enterpriseToEbitda')),'dividend_yield_pct':_yield_pct(info.get('dividendYield')),'roe_pct':_pct(info.get('returnOnEquity')),'roa_pct':_pct(info.get('returnOnAssets')),'profit_margin_pct':_pct(info.get('profitMargins')),'operating_margin_pct':_pct(info.get('operatingMargins')),'revenue_growth_pct':_pct(info.get('revenueGrowth')),'earnings_growth_pct':_pct(info.get('earningsGrowth')),'debt_to_equity':_safe_float(info.get('debtToEquity')),'free_cashflow':_safe_float(info.get('freeCashflow')),'total_cash':_safe_float(info.get('totalCash')),'total_debt':_safe_float(info.get('totalDebt')),'fund_family':info.get('fundFamily'),'expense_ratio':_safe_float(info.get('annualReportExpenseRatio')),'tracking_error':_safe_float(info.get('trackingError')),'category':info.get('category'),'fund_holdings':_safe_float(info.get('holdingsCount')),**tech}
    required=['price','return_1w_pct','return_1m_pct','volatility_pct','max_drawdown_pct']
    completeness=sum(rec.get(k) is not None for k in required)/len(required)
    sample_coverage=min(int(rec.get('data_points') or 0)/126,1.0)
    rec['data_confidence']=round((0.6*completeness+0.4*sample_coverage)*100)
    return rec

def market_data_quality_summary(records):
    """Summarise source and cache freshness without conflating it with candle age."""
    from collections import Counter
    providers = Counter()
    cache_states = Counter()
    last_dates = []
    cache_ages = []
    for row in records:
        providers[str(row.get('data_source') or 'UNKNOWN')] += 1
        cache_states[str(row.get('cache_status') or 'UNKNOWN')] += 1
        if row.get('last_date'):
            last_dates.append(str(row['last_date']))
        age = _safe_float(row.get('cache_age_hours'))
        if age is not None:
            cache_ages.append(age)
    return {
        'instrument_count': len(records),
        'provider_counts': dict(providers),
        'cache_status_counts': dict(cache_states),
        'latest_market_data_date': max(last_dates) if last_dates else None,
        'oldest_market_data_date': min(last_dates) if last_dates else None,
        'max_cache_age_hours': round(max(cache_ages), 2) if cache_ages else None,
        'cache_age_is_not_market_data_age': True,
        'note': 'Market-data freshness is determined by last_date and the decision gate. Cache age measures local cache refresh age only.'
    }
def liquidity_screen(
    universe,
    max_instruments=250,
    workers=3,
    batch_size=100,
    request_timeout_seconds=10,
):
    """Select liquid instruments while logging progress and isolating bad symbols."""
    started = time.monotonic()
    total = len(universe)
    if total <= max_instruments:
        logger.info(
            "Liquidity screen skipped: %d instruments are within the limit %d.",
            total, max_instruments
        )
        return universe

    symbols = [yf_symbol(row) for row in universe]
    scores = {}
    total_batches = (len(symbols) + batch_size - 1) // batch_size
    logger.info(
        "Liquidity screen started: instruments=%d, target=%d, batch_size=%d, "
        "request_timeout=%ss, batches=%d.",
        total, max_instruments, batch_size, request_timeout_seconds, total_batches
    )

    def score_group(group):
        """Return {Yahoo symbol: average traded value}; split failed groups."""
        try:
            data = yf.download(
                group,
                period="1mo",
                interval="1d",
                auto_adjust=True,
                progress=False,
                threads=False,
                group_by="ticker",
                timeout=request_timeout_seconds,
            )
            if data is None or data.empty:
                raise RuntimeError("empty Yahoo liquidity response")

            group_scores = {}
            for yahoo_symbol in group:
                try:
                    frame = data if len(group) == 1 else data[yahoo_symbol]
                    close = pd.to_numeric(frame.get("Close"), errors="coerce").dropna()
                    volume = pd.to_numeric(frame.get("Volume"), errors="coerce").dropna()
                    if not close.empty and not volume.empty:
                        traded_value = _safe_float(close.iloc[-1]) * _safe_float(volume.tail(20).mean())
                        if traded_value is not None:
                            group_scores[yahoo_symbol] = traded_value
                except Exception as exc:
                    logger.debug("Liquidity data unavailable for %s: %s", yahoo_symbol, exc)

            missing_symbols = [symbol for symbol in group if symbol not in group_scores]
            if missing_symbols and len(group) > 1 and not CB.open:
                logger.warning(
                    "Liquidity response incomplete: %d/%d symbols missing; retrying missing symbols in smaller groups.",
                    len(missing_symbols), len(group)
                )
                group_scores.update(score_group(missing_symbols))
            return group_scores

        except Exception as exc:
            message = str(exc)
            if "Too Many Requests" in message or "RateLimit" in message or "429" in message:
                CB.trip(message)
                return {}
            if len(group) == 1:
                logger.warning("Liquidity data unavailable for %s: %s", group[0], message)
                return {}
            midpoint = len(group) // 2
            logger.warning(
                "Liquidity batch failed for %d symbols; splitting into %d and %d: %s",
                len(group), midpoint, len(group) - midpoint, message
            )
            left = score_group(group[:midpoint])
            if CB.open:
                return left
            left.update(score_group(group[midpoint:]))
            return left

    for batch_index, offset in enumerate(range(0, len(symbols), batch_size), start=1):
        batch = symbols[offset:offset + batch_size]
        if CB.open:
            logger.warning(
                "Liquidity screen stopped at batch %d/%d because Yahoo circuit breaker is open.",
                batch_index, total_batches
            )
            break

        batch_started = time.monotonic()
        logger.info(
            "Liquidity batch %d/%d started: symbols %d-%d (%d instruments).",
            batch_index, total_batches, offset + 1, offset + len(batch), len(batch)
        )
        batch_scores = score_group(batch)
        scores.update(batch_scores)
        logger.info(
            "Liquidity batch %d/%d finished in %.1fs: scored=%d/%d, cumulative=%d/%d.",
            batch_index, total_batches, time.monotonic() - batch_started,
            len(batch_scores), len(batch), len(scores), total
        )

    coverage = 100.0 * len(scores) / max(total, 1)
    logger.info(
        "Liquidity data collection finished in %.1fs: scored=%d/%d (%.1f%%).",
        time.monotonic() - started, len(scores), total, coverage
    )

    if len(scores) < max_instruments:
        logger.warning(
            "Liquidity coverage is below the requested shortlist size: scored=%d, target=%d. "
            "Unscored instruments will receive zero liquidity score.",
            len(scores), max_instruments
        )

    stocks = sorted(
        [row for row in universe if str(row.get("type", "STOCK")).upper() == "STOCK"],
        key=lambda row: scores.get(yf_symbol(row), 0),
        reverse=True,
    )
    etfs = sorted(
        [row for row in universe if str(row.get("type", "STOCK")).upper() == "ETF"],
        key=lambda row: scores.get(yf_symbol(row), 0),
        reverse=True,
    )
    stock_limit = max(1, int(max_instruments * 0.8))
    etf_limit = max(1, max_instruments - stock_limit)
    chosen = stocks[:stock_limit] + etfs[:etf_limit]
    logger.info(
        "Liquidity screen completed in %.1fs: selected=%d (%d stocks, %d ETFs).",
        time.monotonic() - started, len(chosen),
        sum(row.get("type") == "STOCK" for row in chosen),
        sum(row.get("type") == "ETF" for row in chosen),
    )
    return chosen

def build_market_data(universe,history_period='3y',workers=3,cache_dir='cache',history_cache_ttl_hours=12,fundamentals_cache_ttl_hours=168):
    symbols=[yf_symbol(x) for x in universe]; histories=_history_batch(symbols,history_period,batch_size=80,cache_dir=str(Path(cache_dir)/'market'),cache_ttl_hours=history_cache_ttl_hours)
    records=[]
    # Fundamentals are expensive and rate-limited. Use cached info first; otherwise fetch only the first 60 liquid candidates.
    for i,row in enumerate(universe):
        ysym=yf_symbol(row); info={}
        p=Path(cache_dir)/'fundamentals'/(ysym.replace('/','_')+'.json')
        if i < 60:
            # _info enforces the fundamentals cache TTL and refreshes expired entries.
            info = _info(ysym, str(Path(cache_dir) / 'fundamentals'), cache_ttl_hours=fundamentals_cache_ttl_hours)
        elif p.exists():
            try:
                info = json.loads(p.read_text(encoding='utf-8'))
            except Exception:
                info = {}
        rec=_merge_record(row,histories.get(ysym),info)
        records.append(rec)
        if (i+1)%25==0 or i+1==len(universe):logger.info('Market data progress: %d/%d',i+1,len(universe))
    return records
