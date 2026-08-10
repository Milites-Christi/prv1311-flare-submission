"""
================================================================================
PROJECT: Prv1311 — RSI Scanner (full-universe discovery, Coinbase live data)
FILE: rsi_scanner.py
================================================================================
DISCOVERY ENGINE. Scans the entire Coinbase /USD tradeable universe (minus
stablecoins). Triple-confirmation before a signal fires. Silence is normal.

Triple-confirmation (ALL THREE):
  (1) RSI extreme      -- 6h<=35/>=65, 1h<=30/>=70   (6h replaces 4h on Coinbase)
  (2) Taker ratio      -- from Coinbase recent trades (buy-side vs sell-side vol)
  (3) Bollinger touch  -- 20-period, 2sigma, fresh candles

Coinbase notes: quotes in USD; candle granularities are 1h/6h/1d (no 4h);
taker data derived from fetch_trades 'side'. Real live volume (unlike Binance.US).
================================================================================
"""

import time
import ccxt
from config import EXCHANGE_ID, QUOTE, TF_SHORT, TF_MED

exchange = getattr(ccxt, EXCHANGE_ID)({'enableRateLimit': True})

RSI_PERIOD = 14
BB_PERIOD = 20
BB_STDDEV_MULT = 2.0
RSI_MED_OVERSOLD, RSI_MED_OVERBOUGHT = 35, 65     # was 4h, now 6h
RSI_1H_OVERSOLD, RSI_1H_OVERBOUGHT = 30, 70
STABLECOINS = {'USDC', 'USDT', 'DAI', 'BUSD', 'TUSD', 'FRAX', 'USDP',
               'USDS', 'PYUSD', 'GUSD', 'USDD', 'PAX', 'HUSD', 'PYUSD'}


def compute_rsi(closes, period=RSI_PERIOD):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0.0)); losses.append(max(-ch, 0.0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0
    rs = ag / al
    return 100.0 - (100.0 / (1.0 + rs))


def compute_bollinger(closes, period=BB_PERIOD, mult=BB_STDDEV_MULT):
    valid = [c for c in closes if c is not None]
    if len(valid) < period:
        return False
    w = valid[-period:]
    sma = sum(w) / period
    var = sum((v - sma) ** 2 for v in w) / period
    sd = var ** 0.5
    cur = valid[-1]
    return cur > sma + mult * sd or cur < sma - mult * sd


def taker_ratio(pair):
    """
    Coinbase taker ratio: pull recent trades, sum buy-side vs sell-side volume.
    ratio > 1 = buyers dominant. Replaces Binance's kline taker column.
    """
    try:
        trades = exchange.fetch_trades(pair, limit=100)
        buy_vol = sum(t['amount'] for t in trades if t.get('side') == 'buy')
        sell_vol = sum(t['amount'] for t in trades if t.get('side') == 'sell')
        if sell_vol <= 0:
            return 999.0 if buy_vol > 0 else 1.0
        return buy_vol / sell_vol
    except Exception:
        return None


def closed_closes(pair, timeframe, limit=60):
    try:
        ohlcv = exchange.fetch_ohlcv(pair, timeframe=timeframe, limit=limit)
        if len(ohlcv) < 2:
            return None
        return [row[4] for row in ohlcv[:-1]]   # drop forming candle
    except Exception:
        return None


def candle_24h_volume(pair):
    """24h USD volume from daily candle (Coinbase ticker quoteVolume is None)."""
    try:
        o = exchange.fetch_ohlcv(pair, timeframe='1d', limit=1)
        if not o:
            return 0
        row = o[-1]                      # [ts,open,high,low,close,volume]
        return row[5] * row[4]           # base volume * close = ~USD volume
    except Exception:
        return 0


def tradeable_universe():
    """Every SYM/USD pair on Coinbase, minus stablecoins."""
    try:
        markets = exchange.load_markets()
        pairs = []
        for m in markets:
            if m.endswith(f'/{QUOTE}'):
                base = m.split('/')[0]
                if base not in STABLECOINS:
                    pairs.append(m)
        return sorted(pairs)
    except Exception as e:
        print(f"[universe error] {e}")
        return []


def scan_pair(pair):
    c1h = closed_closes(pair, TF_SHORT, 60)
    cmed = closed_closes(pair, TF_MED, 60)
    if not c1h or not cmed:
        return None
    rsi_1h = compute_rsi(c1h)
    rsi_med = compute_rsi(cmed)
    if rsi_1h is None or rsi_med is None:
        return None
    touching = compute_bollinger(cmed)
    ratio = taker_ratio(pair)
    if ratio is None:
        ratio = 1.0

    signal = None
    if rsi_med <= RSI_MED_OVERSOLD and ratio >= 1 and touching:
        signal = 'OVERSOLD 6h'
    elif rsi_med >= RSI_MED_OVERBOUGHT and ratio <= 1 and touching:
        signal = 'OVERBOUGHT 6h'
    elif rsi_1h <= RSI_1H_OVERSOLD and ratio >= 1 and touching:
        signal = 'OVERSOLD 1h'
    elif rsi_1h >= RSI_1H_OVERBOUGHT and ratio <= 1 and touching:
        signal = 'OVERBOUGHT 1h'

    return {'symbol': pair.split('/')[0], 'pair': pair, 'price': cmed[-1],
            'rsi_1h': round(rsi_1h, 1), 'rsi_4h': round(rsi_med, 1),  # keep key name for downstream
            'taker': round(ratio, 3), 'band': touching, 'signal': signal}


def run_market_scan(verbose_progress=True):
    print("Loading full Coinbase tradeable universe ...")
    universe = tradeable_universe()
    print(f"  {len(universe)} tradeable /{QUOTE} pairs (stablecoins excluded)\n")
    print(f"Scanning all {len(universe)} with triple-confirmation ...")
    print("(this takes a few minutes -- candles + trades per asset)\n")

    fired = []
    scanned = 0
    for i, pair in enumerate(universe, 1):
        r = scan_pair(pair)
        if verbose_progress and i % 25 == 0:
            print(f"  ...{i}/{len(universe)} scanned")
        if r is None:
            continue
        scanned += 1
        if r['signal']:
            fired.append(r)
            print(f"  🚨 {r['symbol']:<7} {r['signal']:<14} "
                  f"${r['price']:.4f}  RSI1h {r['rsi_1h']} RSI6h {r['rsi_4h']} "
                  f"taker {r['taker']}")
        time.sleep(0.1)

    print(f"\n{'='*60}")
    print(f"Scanned {scanned} of {len(universe)} pairs.  "
          f"TRIPLE-CONFIRMED SIGNALS: {len(fired)}")
    print(f"{'='*60}")
    if not fired:
        print("No signals today. (Normal -- narrow pathway by design.)")
    return fired


if __name__ == "__main__":
    run_market_scan()