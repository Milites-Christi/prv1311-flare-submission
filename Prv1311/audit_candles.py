"""
audit_candles.py — standalone candle-history audit for the live Rider universe.

Does not import screener.calculate_90_day_floor's return value (it collapses
maturity-fail/math-fail/API-error into one None) -- fetches OHLCV directly,
the same way screener.py does, so candle_count is visible on its own.

Read-only. Wires nothing, changes no gate, imports get_universe_markets from
rider_team rather than reimplementing universe selection.
"""

import supabase_client  # noqa: F401  -- import first: injects truststore before any ccxt call

from screener import exchange, MIN_HISTORY_CANDLES
from rider_team import get_universe_markets

FALLBACK_STUB = {'BTC/USD', 'ETH/USD', 'SOL/USD', 'XRP/USD', 'ADA/USD', 'XLM/USD'}


def candle_count(symbol):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1d', limit=400)
        return len(ohlcv) if ohlcv else 0
    except Exception as e:
        print(f"[!] fetch failed for {symbol}: {e}")
        return None


def main():
    universe = get_universe_markets(limit=50)

    if set(universe) == FALLBACK_STUB:
        print("=" * 78)
        print("UNIVERSE FETCH FELL BACK TO THE 6-SYMBOL STUB.")
        print("This report would be worthless -- stopping without printing one.")
        print("=" * 78)
        return

    print(f"Universe: {len(universe)} symbols\n")

    rows = []
    for sym in universe:
        cnt = candle_count(sym)
        rows.append((sym, cnt))

    rows.sort(key=lambda r: (r[1] is None, r[1] if r[1] is not None else -1))

    print(f"{'symbol':<12} {'candle_count':>12} {'passes_maturity_gate':>22}")
    passes = 0
    fails = 0
    for sym, cnt in rows:
        if cnt is None:
            print(f"{sym:<12} {'FETCH_ERR':>12} {'--':>22}")
            fails += 1
            continue
        ok = cnt >= MIN_HISTORY_CANDLES
        passes += ok
        fails += (not ok)
        print(f"{sym:<12} {cnt:>12} {str(ok):>22}")

    print(f"\nSummary: {passes} pass / {fails} fail  (threshold: {MIN_HISTORY_CANDLES} candles)")

    print("\nDeterminism check (5 symbols, fetched twice):")
    sample = [s for s, c in rows if c is not None][:5]
    for sym in sample:
        c1 = candle_count(sym)
        c2 = candle_count(sym)
        flag = "" if c1 == c2 else "  <-- MISMATCH"
        print(f"  {sym:<12} run1={c1}  run2={c2}{flag}")


if __name__ == "__main__":
    main()
