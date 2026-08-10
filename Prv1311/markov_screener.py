"""
================================================================================
PROJECT: Prv1311 — Markov Screener (hourly data layer)
FILE: markov_screener.py
================================================================================
ISOLATED data layer for the Markov Signal Engine ONLY. Does not touch the daily
screener.py the other fleets use. Markov needs a deep HOURLY history (~4500 1h
candles per the spec) — Coinbase/ccxt caps ~300 per call, so we paginate.

Returns candles as a list of dicts: {o, h, l, c, ts} oldest->newest.
================================================================================
"""

import time
import ccxt
from config import EXCHANGE_ID, QUOTE

# own exchange handle, same pattern as screener.py
_exchange = getattr(ccxt, EXCHANGE_ID)({'enableRateLimit': True})

MARKOV_TF = '1h'
MARKOV_LIMIT = 4500          # spec: deep hourly history
_PER_CALL = 300             # Coinbase per-request cap


def fetch_hourly(symbol, limit=MARKOV_LIMIT):
    """Paginate 1h candles back `limit` bars. Returns oldest->newest list of
    {o,h,l,c,ts} dicts, or [] on failure."""
    pair = symbol if '/' in symbol else f"{symbol}/{QUOTE}"
    tf_ms = 60 * 60 * 1000                      # 1h in ms
    now_ms = _exchange.milliseconds()
    since = now_ms - limit * tf_ms

    all_rows = []
    cursor = since
    guard = 0
    try:
        while cursor < now_ms and guard < 40:   # 40*300 = 12000 max, safety
            guard += 1
            batch = _exchange.fetch_ohlcv(pair, timeframe=MARKOV_TF,
                                          since=cursor, limit=_PER_CALL)
            if not batch:
                break
            all_rows.extend(batch)
            last_ts = batch[-1][0]
            if last_ts <= cursor:               # no progress -> stop
                break
            cursor = last_ts + tf_ms
            if len(batch) < _PER_CALL:          # reached the present
                break
            time.sleep(_exchange.rateLimit / 1000.0)
    except Exception as e:
        print(f"[Markov fetch error] {pair}: {e}")
        return []

    # dedupe by timestamp, keep oldest->newest, trim to limit
    seen = {}
    for r in all_rows:
        seen[r[0]] = r
    rows = [seen[k] for k in sorted(seen.keys())]
    rows = rows[-limit:]

    return [{'ts': r[0], 'o': float(r[1]), 'h': float(r[2]),
             'l': float(r[3]), 'c': float(r[4])} for r in rows]


if __name__ == "__main__":
    c = fetch_hourly('BTC/USD', limit=4500)
    print(f"BTC/USD: {len(c)} hourly candles")
    if c:
        print(f"  oldest {c[0]['ts']}  newest {c[-1]['ts']}")