"""
================================================================================
PROJECT: Prv1311 — Quantitative Asset Accumulation Engine
FILE: taker_absorption.py
================================================================================
DESCRIPTION:
Microstructure order-flow analysis. Compares aggressive selling pressure against
the actual price delta to detect institutional limit-order absorption -- heavy
selling that DOESN'T move price = a big buyer quietly absorbing panic. The
institutional entry moment.

Coinbase note: Coinbase has no taker-volume kline column (Binance did), so we
reconstruct taker buy/sell from recent trade 'side' data via fetch_trades, and
measure price delta from the same window's candle.
================================================================================
"""

import ccxt
from config import EXCHANGE_ID, QUOTE

exchange = getattr(ccxt, EXCHANGE_ID)({'enableRateLimit': True})

# --- tunables ---
MIN_VOLUME_USD = 50_000        # ignore low-volume noise (in USD)
HEAVY_SELL_RATIO = 60.0        # % taker-sell to call "heavy selling"
HEAVY_BUY_RATIO = 60.0         # % taker-buy to call "heavy buying"
ABSORPTION_MOVE_PCT = 0.5      # price moved less than this % = absorption


def check_absorption(symbol, timeframe='1h'):
    """
    symbol: 'XLM' or 'XLM/USD' (either accepted).
    Returns a dict:
      { 'signal': 'ABSORPTION'|'DISTRIBUTION'|'NORMAL'|'NEUTRAL',
        'taker_sell_pct', 'price_delta_pct', 'volume_usd', ... }
    'ABSORPTION' = heavy selling absorbed (bullish, the buy moment).
    Returns None on failure.
    """
    base = symbol.split('/')[0]
    pair = f"{base}/{QUOTE}"

    try:
        # taker buy/sell from recent trades (Coinbase gives 'side' per trade)
        trades = exchange.fetch_trades(pair, limit=200)
        if not trades:
            return None
        buy_vol = sum(t['amount'] for t in trades if t.get('side') == 'buy')
        sell_vol = sum(t['amount'] for t in trades if t.get('side') == 'sell')
        total_vol = buy_vol + sell_vol
        if total_vol <= 0:
            return None

        # price delta over the recent completed candle
        ohlcv = exchange.fetch_ohlcv(pair, timeframe=timeframe, limit=2)
        if len(ohlcv) < 1:
            return None
        candle = ohlcv[-2] if len(ohlcv) >= 2 else ohlcv[-1]
        c_open, c_close = candle[1], candle[4]
        price_delta_pct = ((c_close - c_open) / c_open) * 100 if c_open else 0.0

        # volume in USD (approx: trade base volume * current price)
        volume_usd = total_vol * c_close
        taker_sell_pct = (sell_vol / total_vol) * 100
        taker_buy_pct = (buy_vol / total_vol) * 100

        # --- absorption logic ---
        if volume_usd < MIN_VOLUME_USD:
            signal = 'NEUTRAL'   # low-volume noise, no institutional read
        elif taker_sell_pct > HEAVY_SELL_RATIO:
            # heavy selling -- did it move price?
            if price_delta_pct > -ABSORPTION_MOVE_PCT:
                signal = 'ABSORPTION'          # sell wall absorbed -> bullish
            else:
                signal = 'NORMAL'              # selling moved price down (real)
        elif taker_buy_pct > HEAVY_BUY_RATIO:
            # heavy buying -- did it move price up?
            if price_delta_pct < ABSORPTION_MOVE_PCT:
                signal = 'DISTRIBUTION'        # buy pressure absorbed -> bearish
            else:
                signal = 'NORMAL'
        else:
            signal = 'NEUTRAL'                 # balanced flow

        return {
            'symbol': base,
            'signal': signal,
            'taker_sell_pct': round(taker_sell_pct, 1),
            'taker_buy_pct': round(taker_buy_pct, 1),
            'price_delta_pct': round(price_delta_pct, 2),
            'volume_usd': round(volume_usd, 0),
        }
    except Exception as e:
        print(f"[absorption error] {pair}: {e}")
        return None


if __name__ == "__main__":
    for test in ['XLM', 'XRP', 'ADA']:
        r = check_absorption(test)
        if r:
            print(f"{r['symbol']:<5} {r['signal']:<13} "
                  f"sell {r['taker_sell_pct']}%  Δprice {r['price_delta_pct']}%  "
                  f"vol ${r['volume_usd']:,.0f}")
        else:
            print(f"{test}: no data")