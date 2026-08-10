"""
================================================================================
PROJECT: Prv1311 — Ranking Engine (filter + rank, Coinbase live data)
FILE: ranking.py
================================================================================
Takes the RSI scanner's fires, FILTERS through eligibility (kills thin/junk
via real candle volume), RANKS survivors by a composite score from data we
already compute (RSI depth + taker + band + floor proximity).
================================================================================
"""

import ccxt
from config import EXCHANGE_ID, QUOTE, MIN_24H_USD_VOLUME, PRICE_CEILING, PRICE_FLOOR
from screener import calculate_90_day_floor

exchange = getattr(ccxt, EXCHANGE_ID)({'enableRateLimit': True})

DENYLIST = {'DOGE'}
STABLECOINS = {'USDC', 'USDT', 'DAI', 'BUSD', 'TUSD', 'FRAX', 'USDP', 'USDS'}


def _candle_24h_usd_volume(pair):
    """24h USD volume from the daily candle (Coinbase ticker volume is None)."""
    try:
        o = exchange.fetch_ohlcv(pair, timeframe='1d', limit=1)
        if not o:
            return 0
        row = o[-1]
        return row[5] * row[4]     # base volume * close price = ~USD volume
    except Exception:
        return 0


def passes_eligibility(fire):
    sym = fire['symbol']
    price = fire['price']
    if sym in DENYLIST:
        return False, "denylisted (meme/hype)"
    if sym in STABLECOINS:
        return False, "stablecoin"
    if price is None or price > PRICE_CEILING:
        return False, f"price ${price} > ${PRICE_CEILING} ceiling"
    if price < PRICE_FLOOR:
        return False, "dust"
    vol = _candle_24h_usd_volume(f"{sym}/{QUOTE}")
    if vol < MIN_24H_USD_VOLUME:
        return False, f"thin liquidity (24h vol ${vol:,.0f})"
    return True, "eligible"


def score_fire(fire):
    if not fire['signal'] or 'OVERSOLD' not in fire['signal']:
        return None
    rsi_med = fire['rsi_4h']    # holds the 6h value now
    taker = fire['taker']
    price = fire['price']

    rsi_component = max(0.0, min(25.0, (35 - rsi_med) * 1.5))
    t = min(taker, 5.0)
    taker_component = max(0.0, min(25.0, (t - 1.0) * 6.0))
    band_component = 15.0 if fire.get('band') else 0.0

    floor = calculate_90_day_floor(f"{fire['symbol']}/{QUOTE}")
    if floor and floor > 0:
        pct_above = (price - floor) / floor
        floor_component = max(0.0, min(25.0, 25.0 * (1 - pct_above / 0.20)))
    else:
        floor_component = 0.0

    return round(rsi_component + taker_component + band_component + floor_component, 1)


def filter_and_rank(fires):
    ranked, rejected = [], []
    for f in fires:
        eligible, reason = passes_eligibility(f)
        if not eligible:
            rejected.append((f['symbol'], reason))
            continue
        score = score_fire(f)
        if score is None:
            rejected.append((f['symbol'], "overbought (not a buy candidate)"))
            continue
        f2 = dict(f)
        f2['score'] = score
        ranked.append(f2)
    ranked.sort(key=lambda x: x['score'], reverse=True)
    return ranked, rejected


if __name__ == "__main__":
    from rsi_scanner import run_market_scan
    print("Running market discovery scan first...\n")
    fires = run_market_scan(verbose_progress=False)
    print("\nFiltering + ranking...\n")
    ranked, rejected = filter_and_rank(fires)
    print("=" * 64)
    print("RANKED BUY CANDIDATES (best first)")
    print("=" * 64)
    if ranked:
        for r in ranked:
            print(f"  {r['symbol']:<7} score {r['score']:>5}  "
                  f"${r['price']:.4f}  RSI6h {r['rsi_4h']}  taker {r['taker']}")
    else:
        print("  (none passed eligibility + were buy-side)")
    print("\nREJECTED:")
    for sym, reason in rejected:
        print(f"  {sym:<7} -- {reason}")