from __future__ import annotations

from datetime import date, datetime


def n(x, lo, hi, inverse=False):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    z = max(0, min(1, (x - lo) / (hi - lo)))
    return round((1 - z if inverse else z) * 100, 1)


def avg_known(parts):
    vals = [v for v in parts if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def score_short(r):
    return avg_known([n(r.get('return_1w_pct'), -8, 8), n(r.get('return_1m_pct'), -15, 15),
        n(r.get('distance_sma20_pct'), -8, 8), n(r.get('distance_sma50_pct'), -15, 15),
        n(r.get('volume_ratio_20d'), 0.5, 2.5), n(r.get('rsi14'), 35, 70)])


def score_mid(r):
    return avg_known([n(r.get('return_3m_pct'), -20, 30), n(r.get('return_6m_pct'), -30, 50),
        n(r.get('distance_sma50_pct'), -20, 30), n(r.get('distance_sma200_pct'), -30, 50),
        n(r.get('earnings_growth_pct'), -30, 40), n(r.get('roe_pct'), 0, 30),
        n(r.get('pe'), 8, 45, True)])


def score_long(r):
    return avg_known([n(r.get('revenue_growth_pct'), -10, 30), n(r.get('earnings_growth_pct'), -15, 35),
        n(r.get('roe_pct'), 0, 35), n(r.get('profit_margin_pct'), 0, 35),
        n(r.get('debt_to_equity'), 0, 200, True), n(r.get('free_cashflow'), 0, 1_000_000_000),
        n(r.get('pe'), 8, 50, True), n(r.get('cagr_3y_pct'), -10, 30)])


def score_etf(r, horizon):
    if horizon == 'SHORT_TERM':
        return avg_known([n(r.get('return_1w_pct'), -8, 8), n(r.get('return_1m_pct'), -15, 15),
            n(r.get('distance_sma20_pct'), -8, 8), n(r.get('volume_ratio_20d'), 0.4, 2.5),
            n(r.get('rsi14'), 35, 70)])
    if horizon == 'MID_TERM':
        return avg_known([n(r.get('return_3m_pct'), -20, 30), n(r.get('return_6m_pct'), -30, 50),
            n(r.get('distance_sma50_pct'), -20, 30), n(r.get('distance_sma200_pct'), -30, 50),
            n(r.get('volatility_pct'), 8, 45, True)])
    return avg_known([n(r.get('return_1y_pct'), -25, 40), n(r.get('cagr_3y_pct'), -10, 30),
        n(r.get('max_drawdown_pct'), -60, -5), n(r.get('volatility_pct'), 8, 45, True)])


def _as_date(value):
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except (TypeError, ValueError):
        return None


def add_scores(records, horizon, as_of=None, max_data_age_days=7):
    """Calculate scores and suppress actionable signals when data is stale or thin."""
    as_of = as_of or date.today()
    min_observations = {'SHORT_TERM': 50, 'MID_TERM': 100, 'LONG_TERM': 200}.get(horizon, 100)
    for row in records:
        if row.get('error'):
            row.update(horizon_score=None, ranking_score=None, signal='INSUFFICIENT_DATA',
                       data_quality_status='ERROR')
            continue
        score = score_etf(row, horizon) if row.get('instrument_type') == 'ETF' else (
            score_short(row) if horizon == 'SHORT_TERM' else
            score_mid(row) if horizon == 'MID_TERM' else score_long(row))
        row['horizon_score'] = score
        confidence = float(row.get('data_confidence') or 0)
        row['ranking_score'] = round(score * (0.65 + 0.35 * confidence / 100), 1) if score is not None else None
        if score is None:
            row['signal'] = 'INSUFFICIENT_DATA'
        elif horizon == 'SHORT_TERM':
            row['signal'] = 'SETUP' if score >= 75 else ('WATCH' if score >= 60 else 'AVOID')
        elif horizon == 'MID_TERM':
            row['signal'] = 'ACCUMULATE' if score >= 75 else ('WATCH' if score >= 60 else 'AVOID')
        else:
            row['signal'] = 'QUALITY_CANDIDATE' if score >= 75 else ('WATCH' if score >= 60 else 'AVOID')

        last_date = _as_date(row.get('last_date'))
        age_days = (as_of - last_date).days if last_date else None
        row['data_age_days'] = age_days
        observations = int(row.get('data_points') or 0)
        if (str(row.get('cache_status') or '').upper() == 'STALE_CACHE'
                or last_date is None or age_days < 0 or age_days > max_data_age_days):
            row.update(data_quality_status='STALE_OR_UNKNOWN', signal='STALE_DATA', ranking_score=None)
        elif observations < min_observations:
            row.update(data_quality_status='INSUFFICIENT_HISTORY', signal='LOW_CONFIDENCE', ranking_score=None)
        elif confidence < 70:
            row.update(data_quality_status='LOW_CONFIDENCE', signal='LOW_CONFIDENCE', ranking_score=None)
        else:
            row['data_quality_status'] = 'PASS'
    return records
