"""
================================================================================
PROJECT: Prv1311 — Quantitative Asset Accumulation Engine
FILE: dynamic_rsi.py
================================================================================
DESCRIPTION:
14-period RSI on 6h candles, then rolling mean + stddev of that RSI over ~30
days (120 six-hour candles) to produce DYNAMIC overbought/oversold thresholds
that adapt to regime. Fixes the hardcoded-30/70 problem: RSI 40 in a bull is a
real dip; RSI 30 in a bear is just Tuesday. Thresholds move with volatility.

Coinbase: /USD pairs, 6h candles (no 4h). Returns a dict for the scanner.
================================================================================
"""

import ccxt
from config import EXCHANGE_ID, QUOTE

exchange = getattr(ccxt, EXCHANGE_ID)({'enableRateLimit': True})

RSI_PERIOD = 14
LOOKBACK = 120        # 30 days of 6h candles
STDDEV_MULT = 2.0


def _wilder_rsi_series(closes, period=RSI_PERIOD):
    """Return a list of RSI values (one per close after warmup), Wilder's smoothing."""
    if len(closes) < period + 1:
        return []
    gains, losses = [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0.0)); losses.append(max(-ch, 0.0))
    rsis = []
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    # first RSI value at index `period`
    def rsi_from(ag, al):
        if al == 0:
            return 100.0
        rs = ag / al
        return 100.0 - (100.0 / (1.0 + rs))
    rsis.append(rsi_from(avg_gain, avg_loss))
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rsis.append(rsi_from(avg_gain, avg_loss))
    return rsis


def _mean_std(vals):
    n = len(vals)
    if n == 0:
        return None, None
    m = sum(vals) / n
    var = sum((v - m) ** 2 for v in vals) / n
    return m, var ** 0.5


def get_dynamic_rsi(symbol, timeframe='6h', limit=200):
    """
    symbol: 'XLM' or 'XLM/USD'. Returns a dict:
      { 'rsi', 'mean', 'std', 'lower', 'upper', 'signal', 'price' }
    signal: 'OVERSOLD' | 'OVERBOUGHT' | 'NEUTRAL' (dynamic thresholds).
    Returns None on failure.
    """
    base = symbol.split('/')[0]
    pair = f"{base}/{QUOTE}"
    try:
        ohlcv = exchange.fetch_ohlcv(pair, timeframe=timeframe, limit=limit)
        if not ohlcv or len(ohlcv) < RSI_PERIOD + 5:
            return None
        # drop forming candle
        closes = [row[4] for row in ohlcv[:-1]]
        price = closes[-1]

        rsi_series = _wilder_rsi_series(closes)
        if not rsi_series:
            return None
        current_rsi = rsi_series[-1]

        # dynamic thresholds from the trailing lookback of RSI values
        window = rsi_series[-LOOKBACK:] if len(rsi_series) >= LOOKBACK else rsi_series
        mean, std = _mean_std(window)
        if mean is None or std is None:
            return None

        lower = mean - STDDEV_MULT * std
        upper = mean + STDDEV_MULT * std

        if current_rsi <= lower:
            signal = 'OVERSOLD'
        elif current_rsi >= upper:
            signal = 'OVERBOUGHT'
        else:
            signal = 'NEUTRAL'

        return {
            'symbol': base,
            'rsi': round(current_rsi, 1),
            'mean': round(mean, 1),
            'std': round(std, 1),
            'lower': round(lower, 1),
            'upper': round(upper, 1),
            'signal': signal,
            'price': price,
        }
    except Exception as e:
        print(f"[dynamic_rsi error] {pair}: {e}")
        return None


if __name__ == "__main__":
    for test in ['XLM', 'XRP', 'ADA']:
        r = get_dynamic_rsi(test)
        if r:
            print(f"{r['symbol']:<5} RSI {r['rsi']:<5} "
                  f"[dyn {r['lower']}-{r['upper']}] -> {r['signal']}  "
                  f"(mean {r['mean']}, std {r['std']})")
        else:
            print(f"{test}: no data")