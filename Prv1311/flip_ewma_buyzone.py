"""
================================================================================
PROJECT: Prv1311 — EWMA Buy-Zone (ported from Accumulation-Flip front end)
FILE: flip_ewma_buyzone.py
================================================================================
Identifies a "cheap zone" for an asset from EWMA mean + EWMA volatility:
    buy_zone_low  = ewma_mean * (1 - DIP_Z * vol)
    buy_zone_high = ewma_mean * (1 - 0.25 * DIP_Z * vol)
    in_buy_zone   = buy_zone_low <= current_price <= buy_zone_high

Preserves the SEEDED-VARIANCE FIX: ewmaVar is seeded from the simple variance of
the first min(10, len) returns, NOT 0.

REGIME GATE: now LIVE. Only computes a buy zone for daily-reverting assets — a
trending asset has no meaningful "cheap zone" to mean-revert into. The lab
computes each asset's daily regime and passes it in. Set REQUIRE_REGIME=False to
bypass (compute for all).

TWO WAYS TO USE:
  1. compute_buy_zone(prices, regime) -> pure function other engines import.
  2. run as a script -> standalone LAB scanning the watchlist.
================================================================================
"""

import json
import time
import os
import math
from config import QUOTE, WATCHLIST, EWMA_LAB_LEDGER_FILE
from markov_screener import fetch_hourly
from screener import exchange
from regime import classify_regime
from sync_supabase import push_ewma

# --- tunable constants (mirror the source) ---
EWMA_LAMBDA = 0.94     # decay factor (higher = smoother/slower)
DIP_Z = 1.0            # volatility units below mean that define the zone
REQUIRE_REGIME = True  # LIVE: only compute a buy zone for daily-reverting assets


def compute_buy_zone(prices, regime=None):
    """PURE FUNCTION. prices = list of closes (oldest->newest).
    Returns the buy-zone dict. regime is enforced only if REQUIRE_REGIME."""
    n = len(prices)
    out = {
        'in_buy_zone': False, 'regime': regime, 'ewma_mean': None,
        'ewma_vol': None, 'buy_zone_low': None, 'buy_zone_high': None,
        'current_price': prices[-1] if n else None, 'note': None,
    }

    if REQUIRE_REGIME and regime != 'reverting':
        out['note'] = 'regime not reverting - no buy zone computed'
        return out
    if n < 5:
        out['note'] = 'insufficient data'
        return out

    rets = [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, n)]

    seed_n = min(10, len(rets))
    seed_mean = sum(rets[:seed_n]) / seed_n
    seed_var = sum((rets[s] - seed_mean) ** 2 for s in range(seed_n)) / seed_n
    ewma_var = seed_var

    for r in rets:
        ewma_var = EWMA_LAMBDA * ewma_var + (1 - EWMA_LAMBDA) * r * r
    ewma_vol = math.sqrt(ewma_var)

    ewma_mean = prices[0]
    for m in range(1, n):
        ewma_mean = EWMA_LAMBDA * ewma_mean + (1 - EWMA_LAMBDA) * prices[m]

    buy_zone_low = ewma_mean * (1 - DIP_Z * ewma_vol)
    buy_zone_high = ewma_mean * (1 - 0.25 * DIP_Z * ewma_vol)
    current_price = prices[-1]
    in_buy_zone = buy_zone_low <= current_price <= buy_zone_high

    out.update({
        'in_buy_zone': in_buy_zone,
        'ewma_mean': round(ewma_mean, 6),
        'ewma_vol': round(ewma_vol, 6),
        'buy_zone_low': round(buy_zone_low, 6),
        'buy_zone_high': round(buy_zone_high, 6),
        'current_price': round(current_price, 6),
        'note': 'reverting' if REQUIRE_REGIME else 'ungated',
    })
    return out


# ---------------------------------------------------------------------------
# helper: daily closes for the regime read
# ---------------------------------------------------------------------------
def daily_closes(symbol, limit=120):
    pair = symbol if '/' in symbol else f"{symbol}/{QUOTE}"
    try:
        ohlcv = exchange.fetch_ohlcv(pair, timeframe='1d', limit=limit)
        return [row[4] for row in ohlcv] if ohlcv else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# STANDALONE LAB — scans the watchlist, pushes bands to Supabase for viewing
# ---------------------------------------------------------------------------
LAB_SYMBOLS = [f"{t}/{QUOTE}" for t in WATCHLIST[:20]]


def run_cycle():
    zones = []
    for sym in LAB_SYMBOLS:
        candles = fetch_hourly(sym, limit=500)
        if not candles or len(candles) < 5:
            continue
        closes = [c['c'] for c in candles]

        # daily regime for the gate
        reg = None
        if REQUIRE_REGIME:
            dcloses = daily_closes(sym)
            reg = classify_regime(dcloses)['regime'] if dcloses else 'no_data'

        z = compute_buy_zone(closes, regime=reg)
        z['asset'] = sym.split('/')[0]
        zones.append(z)
        tag = 'IN ZONE' if z['in_buy_zone'] else ('  ---  ' if z['note'] and 'not reverting' in z['note'] else '       ')
        low = z['buy_zone_low'] if z['buy_zone_low'] is not None else '-'
        high = z['buy_zone_high'] if z['buy_zone_high'] is not None else '-'
        print(f"  [{tag}] {sym.split('/')[0]:<6} price {z['current_price']}  zone {low} - {high}  ({reg})")
    state = {'system': 'Prv1311-ewma-buyzone',
             'zones': zones,
             'updated': time.strftime('%Y-%m-%d %H:%M:%S')}
    with open(EWMA_LAB_LEDGER_FILE, 'w') as f:
        json.dump(state, f, indent=4)
    push_ewma(state)
    return state


def run_engine():
    print("=" * 78)
    print("      PRV1311 — EWMA BUY-ZONE LAB")
    print("=" * 78)
    print(f"Scans : {len(LAB_SYMBOLS)} watchlist assets (1h candles)")
    print(f"Logic : EWMA mean/vol bands | seeded-variance fix | regime gate {'ON' if REQUIRE_REGIME else 'OFF (ungated)'}")
    print(f"Const : lambda={EWMA_LAMBDA}, DIP_Z={DIP_Z}")
    print(f"Status: LIVE (Ctrl+C to stop)\n")
    if not os.path.exists('data'):
        os.makedirs('data')
    while True:
        try:
            print(f"\n[{time.strftime('%H:%M:%S')}] EWMA scan...")
            state = run_cycle()
            in_zone = [z['asset'] for z in state['zones'] if z['in_buy_zone']]
            print("-" * 78)
            print(f"  In buy zone ({len(in_zone)}): {', '.join(in_zone) if in_zone else 'none'}")
            print("-" * 78)
            time.sleep(15 * 60)
        except KeyboardInterrupt:
            print("\n[EWMA Lab] stopped safely.")
            break
        except Exception as e:
            print(f"\n[EWMA Lab Error] {e}")
            time.sleep(60)


if __name__ == "__main__":
    run_engine()