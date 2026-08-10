"""
================================================================================
PROJECT: Prv1311 — Quantitative Asset Accumulation Engine
FILE: vwap_bands.py
================================================================================
DESCRIPTION:
Rolling Volume-Weighted Average Price (VWAP) with 2-sigma volume-weighted bands.
Replaces SMA-based Bollinger Bands. VWAP ignores low-volume noise -- a violent
low-volume wick drags an SMA down (false "touch") but barely moves VWAP, so a
VWAP-band touch is a statistically real overextension on genuine capital.

Coinbase: /USD pairs, 6h candles. Returns a dict for the scanner.
CAVEAT: only as good as the volume data -- meant for liquid ($2M+) assets.
================================================================================
"""

import ccxt
from config import EXCHANGE_ID, QUOTE

exchange = getattr(ccxt, EXCHANGE_ID)({'enableRateLimit': True})

VWAP_WINDOW = 20
STDDEV_MULT = 2.0


def calculate_vwap_bands(symbol, timeframe='6h', limit=100, window=VWAP_WINDOW):
    """
    symbol: 'XLM' or 'XLM/USD'. Returns a dict:
      { 'vwap', 'upper', 'lower', 'std', 'price', 'touching', 'signal' }
    signal: 'OVEREXT_LOWER' | 'OVEREXT_UPPER' | 'NEUTRAL'.
    'touching' True if price is outside either band. Returns None on failure.
    """
    base = symbol.split('/')[0]
    pair = f"{base}/{QUOTE}"
    try:
        ohlcv = exchange.fetch_ohlcv(pair, timeframe=timeframe, limit=limit)
        if not ohlcv or len(ohlcv) < window + 2:
            return None
        # drop the forming candle
        candles = ohlcv[:-1]

        # take the last `window` candles for the rolling VWAP
        w = candles[-window:]
        # rows: [ts, open, high, low, close, volume]
        typical = [(c[2] + c[3] + c[4]) / 3 for c in w]
        vols = [c[5] for c in w]

        vol_sum = sum(vols)
        if vol_sum <= 0:
            return None

        pv_sum = sum(tp * v for tp, v in zip(typical, vols))
        vwap = pv_sum / vol_sum

        # volume-weighted variance around the VWAP
        weighted_sq = sum(((tp - vwap) ** 2) * v for tp, v in zip(typical, vols))
        variance = weighted_sq / vol_sum
        std = variance ** 0.5

        upper = vwap + STDDEV_MULT * std
        lower = vwap - STDDEV_MULT * std

        price = candles[-1][4]   # last closed price
        touching = price <= lower or price >= upper

        if price <= lower:
            signal = 'OVEREXT_LOWER'     # below the volume-weighted floor (buy side)
        elif price >= upper:
            signal = 'OVEREXT_UPPER'
        else:
            signal = 'NEUTRAL'

        return {
            'symbol': base,
            'vwap': round(vwap, 6),
            'upper': round(upper, 6),
            'lower': round(lower, 6),
            'std': round(std, 6),
            'price': price,
            'touching': touching,
            'signal': signal,
        }
    except Exception as e:
        print(f"[vwap error] {pair}: {e}")
        return None


if __name__ == "__main__":
    for test in ['XLM', 'XRP', 'ADA']:
        r = calculate_vwap_bands(test)
        if r:
            print(f"{r['symbol']:<5} price ${r['price']:.4f}  "
                  f"vwap ${r['vwap']:.4f}  [{r['lower']:.4f}-{r['upper']:.4f}]  "
                  f"touching {r['touching']}  {r['signal']}")
        else:
            print(f"{test}: no data")