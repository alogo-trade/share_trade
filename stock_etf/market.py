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

def _history_batch(symbols, period='3y', batch_size=80, retries=2, cache_dir='cache/market', cache_ttl_hours=12):
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
            frame.attrs['cache_age_hours'] = round(
                max(0.0, (now - (cache / (symbol.replace('/', '_') + '.parquet')).stat().st_mtime) / 3600.0), 2
            )
            frame.attrs['data_source'] = 'Yahoo Finance (stale cache fallback)'
            out[symbol] = frame

    if not missing:
        return out

    if CB.open:
        logger.warning('Yahoo circuit breaker active; stale cache fallback for %d symbols.', len(missing))
        for symbol in missing:
            retain_stale(symbol)
        return out

    for start in range(0, len(missing), batch_size):
        batch = missing[start:start + batch_size]
        batch_loaded = set()
        for attempt in range(retries + 1):
            try:
                data = yf.download(
                    batch, period=period, auto_adjust=True, actions=False,
                    progress=False, threads=False, group_by='ticker'
                )
                if data is None or data.empty:
                    raise RuntimeError('empty Yahoo batch response')
                for symbol in batch:
                    try:
                        frame = data if len(batch) == 1 else data[symbol]
                        if frame is None or frame.empty:
                            continue
                        frame = frame.dropna(how='all')
                        if frame.empty:
                            continue
                        frame.attrs['cache_status'] = 'REFRESHED'
                        frame.attrs['cache_age_hours'] = 0.0
                        frame.attrs['data_source'] = 'Yahoo Finance'
                        out[symbol] = frame
                        batch_loaded.add(symbol)
                        try:
                            frame.to_parquet(cache / (symbol.replace('/', '_') + '.parquet'))
                        except Exception as exc:
                            logger.debug('Could not cache %s history: %s', symbol, exc)
                    except Exception as exc:
                        logger.debug('Could not extract %s from batch: %s', symbol, exc)
                logger.info(
                    'Yahoo batch history: %d/%d symbols refreshed.',
                    len(batch_loaded), len(batch)
                )
                break
            except Exception as exc:
                message = str(exc)
                if 'Too Many Requests' in message or 'RateLimit' in message or '429' in message:
                    CB.trip(message)
                    break
                if attempt < retries:
                    delay = 2 ** attempt
                    logger.warning('Yahoo batch failed, retrying in %ss: %s', delay, message)
                    time.sleep(delay)
                else:
                    logger.warning('Yahoo batch failed after retries: %s', message)
        for symbol in batch:
            if symbol not in batch_loaded:
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

def liquidity_screen(universe,max_instruments=250,workers=3):
    if len(universe)<=max_instruments:return universe
    symbols=[yf_symbol(x) for x in universe]; scores={}
    try:
        for start in range(0,len(symbols),200):
            batch=symbols[start:start+200]
            if CB.open: break
            try:data=yf.download(batch,period='1mo',interval='1d',auto_adjust=True,progress=False,threads=False,group_by='ticker')
            except Exception as exc:
                if 'Too Many Requests' in str(exc) or '429' in str(exc):CB.trip(exc)
                continue
            if data is None or data.empty:continue
            for row in universe[start:start+200]:
                ys=yf_symbol(row)
                try:
                    d=data if len(batch)==1 else data[ys]; c=pd.to_numeric(d.get('Close'),errors='coerce').dropna(); v=pd.to_numeric(d.get('Volume'),errors='coerce').dropna()
                    if not c.empty and not v.empty:scores[row['symbol']]=_safe_float(c.iloc[-1])*_safe_float(v.tail(20).mean())
                except Exception:continue
        stocks=sorted([x for x in universe if str(x.get('type','STOCK')).upper()=='STOCK'],key=lambda x:scores.get(x['symbol'],0),reverse=True); etfs=sorted([x for x in universe if str(x.get('type','STOCK')).upper()=='ETF'],key=lambda x:scores.get(x['symbol'],0),reverse=True)
        stock_limit=max(1,int(max_instruments*0.8)); etf_limit=max(1,max_instruments-stock_limit); chosen=stocks[:stock_limit]+etfs[:etf_limit]
        logger.info('Liquidity screen: selected %d stocks and %d ETFs from %d instruments.',sum(x.get('type')=='STOCK' for x in chosen),sum(x.get('type')=='ETF' for x in chosen),len(universe)); return chosen
    except Exception as exc: logger.warning('Liquidity screen failed, using fallback: %s',exc); return universe[:max_instruments]

def build_market_data(universe,history_period='3y',workers=3,cache_dir='cache'):
    symbols=[yf_symbol(x) for x in universe]; histories=_history_batch(symbols,history_period,batch_size=80,cache_dir=str(Path(cache_dir)/'market'))
    records=[]
    # Fundamentals are expensive and rate-limited. Use cached info first; otherwise fetch only the first 60 liquid candidates.
    for i,row in enumerate(universe):
        ysym=yf_symbol(row); info={}
        p=Path(cache_dir)/'fundamentals'/(ysym.replace('/','_')+'.json')
        if p.exists():
            try: info=json.loads(p.read_text(encoding='utf-8'))
            except Exception: info={}
        elif i<60: info=_info(ysym,str(Path(cache_dir)/'fundamentals'))
        rec=_merge_record(row,histories.get(ysym),info)
        records.append(rec)
        if (i+1)%25==0 or i+1==len(universe):logger.info('Market data progress: %d/%d',i+1,len(universe))
    return records
