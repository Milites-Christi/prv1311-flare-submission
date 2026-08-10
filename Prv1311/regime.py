"""
================================================================================
PROJECT: Prv1311 — Regime Classifier (AR(1)/OU, the shared regime brain)
FILE: regime.py
================================================================================
THIS IS A PIECE, NOT A STANDALONE PRODUCT. It is the ONE shared regime function
every engine imports. Consolidates three War-Room files into one:
    flip-regime-classifier.js  +  grid-ou-regime.js  +  AR(1) mean-reversion
Do NOT re-implement regime logic anywhere else — call classify_regime().

WHAT IT DOES: fits an AR(1) model to a price series and measures how strongly
price pulls back toward its mean.
    beta  = lag-1 autocorrelation of mean-centered prices
    reverting if 0 < beta < BETA_REVERTING_MAX (0.98)   -> ranging, snaps back
    theta = -ln(beta)  = reversion SPEED (higher = snaps back faster)

  NON-REVERTING NOW SPLIT BY DIRECTION (the fix):
    Before, everything that wasn't 'reverting' was dumped into 'non_reverting'
    — which lumped a healthy UPTREND (a pullback in it is a prime flip setup)
    together with a CRASH (a falling knife). Engines gating on 'reverting' threw
    BOTH away, so on green flip-heavy days the Rider sat empty.
    Now non-reverting series are labeled by slope:
        trending_up    -> price rising across the window (flip-friendly)
        trending_down  -> price falling across the window (falling knife)
    'reverting' is unchanged. Callers that only check == 'reverting' still work.

WHO USES IT (plug points):
  - EWMA buy-zone: flip REQUIRE_REGIME=True, feed it this regime
  - Rider / Dogs / future Ranger: gate entries on reverting OR trending_up
  - any engine deciding "is this asset ranging / rising / falling right now?"

TIMEFRAME-AGNOSTIC: pass daily closes for the big-picture filter, hourly closes
for the tactical read.

CONSTANTS: MIN_SAMPLES=20, BETA_REVERTING_MAX=0.98, TREND_FLAT_PCT=1.0.
================================================================================
"""

import json
import time
import os
import math
from config import QUOTE, WATCHLIST, REGIME_LAB_LEDGER_FILE
from screener import exchange
from markov_screener import fetch_hourly
from sync_supabase import push_regime

# --- tunable constants (mirror the source) ---
MIN_SAMPLES = 20            # min price points to classify; below = insufficient_data
BETA_REVERTING_MAX = 0.98   # beta must be < this to count as mean-reverting
TREND_FLAT_PCT = 1.0        # |net move| below this % across the window = 'trending_flat'


def _trend_direction(prices):
    """Direction of a non-reverting series from net move first->last close.
    Returns 'trending_up' / 'trending_down' / 'trending_flat'. Uses the plain
    net move (robust, no fit) so a rising market and a falling market — which
    both land in non-reverting — are told apart."""
    first, last = prices[0], prices[-1]
    if first <= 0:
        return 'trending_flat'
    net_pct = (last / first - 1.0) * 100.0
    if net_pct >= TREND_FLAT_PCT:
        return 'trending_up'
    if net_pct <= -TREND_FLAT_PCT:
        return 'trending_down'
    return 'trending_flat'


def classify_regime(prices):
    """PURE FUNCTION. prices = list of closes (oldest->newest).
    Returns {regime, theta, beta, mean, sample_size}. Import and call this
    from any engine — do not duplicate this logic elsewhere.

    regime is one of:
        'reverting'        — ranging, mean-snapping (0 < beta < BETA_REVERTING_MAX)
        'trending_up'      — non-reverting, price rising across the window
        'trending_down'    — non-reverting, price falling across the window
        'trending_flat'    — non-reverting, no meaningful net move
        'insufficient_data'— fewer than MIN_SAMPLES points
    """
    n = len(prices)
    if n < MIN_SAMPLES:
        return {'regime': 'insufficient_data', 'theta': None, 'beta': None,
                'mean': None, 'sample_size': n}

    mean = sum(prices) / n
    d = [p - mean for p in prices]

    sxy = 0.0
    sxx = 0.0
    for i in range(1, n):
        sxy += d[i - 1] * d[i]
        sxx += d[i - 1] * d[i - 1]

    if sxx <= 0:
        return {'regime': _trend_direction(prices), 'theta': None, 'beta': None,
                'mean': round(mean, 6), 'sample_size': n}

    beta = sxy / sxx
    if 0 < beta < BETA_REVERTING_MAX:
        theta = -math.log(beta)
        return {'regime': 'reverting', 'theta': round(theta, 6),
                'beta': round(beta, 6), 'mean': round(mean, 6), 'sample_size': n}

    # non-reverting: split by direction instead of one flat 'non_reverting' bucket
    return {'regime': _trend_direction(prices), 'theta': None,
            'beta': round(beta, 6), 'mean': round(mean, 6), 'sample_size': n}


# ---------------------------------------------------------------------------
# helper: daily closes (reuses the shared exchange from screener)
# ---------------------------------------------------------------------------
def fetch_daily_closes(symbol, limit=120):
    pair = symbol if '/' in symbol else f"{symbol}/{QUOTE}"
    try:
        ohlcv = exchange.fetch_ohlcv(pair, timeframe='1d', limit=limit)
        if not ohlcv:
            return []
        return [row[4] for row in ohlcv]
    except Exception as e:
        print(f"[Regime daily fetch] {pair}: {e}")
        return []


# ---------------------------------------------------------------------------
# STANDALONE LAB — computes DAILY + HOURLY regime per asset, pushes to Supabase
# ---------------------------------------------------------------------------
LAB_SYMBOLS = [f"{t}/{QUOTE}" for t in WATCHLIST[:20]]

# regimes that count as a real (non-error) classification for the "agree" check
_REAL_REGIMES = ('reverting', 'trending_up', 'trending_down', 'trending_flat')


def run_cycle():
    rows = []
    for sym in LAB_SYMBOLS:
        tag = sym.split('/')[0]

        daily_closes = fetch_daily_closes(sym, limit=120)
        daily = classify_regime(daily_closes) if daily_closes else {'regime': 'no_data'}

        hourly_candles = fetch_hourly(sym, limit=200)
        hourly_closes = [c['c'] for c in hourly_candles] if hourly_candles else []
        hourly = classify_regime(hourly_closes) if hourly_closes else {'regime': 'no_data'}

        agree = (daily.get('regime') == hourly.get('regime')
                 and daily.get('regime') in _REAL_REGIMES)
        rows.append({
            'asset': tag,
            'daily_regime': daily.get('regime'),
            'daily_beta': daily.get('beta'),
            'daily_theta': daily.get('theta'),
            'hourly_regime': hourly.get('regime'),
            'hourly_beta': hourly.get('beta'),
            'hourly_theta': hourly.get('theta'),
            'agree': agree,
        })
        print(f"  {tag:<6} daily={daily.get('regime'):<16} hourly={hourly.get('regime'):<16}"
              f"{'  <-- agree' if agree else ''}")

    state = {'system': 'Prv1311-regime',
             'rows': rows,
             'updated': time.strftime('%Y-%m-%d %H:%M:%S')}
    with open(REGIME_LAB_LEDGER_FILE, 'w') as f:
        json.dump(state, f, indent=4)
    push_regime(state)
    return state


def run_engine():
    print("=" * 78)
    print("      PRV1311 — REGIME CLASSIFIER LAB (shared AR(1)/OU brain)")
    print("=" * 78)
    print(f"Scans : {len(LAB_SYMBOLS)} watchlist assets — DAILY + HOURLY regime")
    print(f"Logic : AR(1) beta | reverting if 0<beta<{BETA_REVERTING_MAX} | theta=-ln(beta)=speed")
    print(f"Split : non-reverting -> trending_up / trending_down / trending_flat (|move|>{TREND_FLAT_PCT}%)")
    print(f"Const : MIN_SAMPLES={MIN_SAMPLES}")
    print(f"Status: LIVE (Ctrl+C to stop)\n")
    if not os.path.exists('data'):
        os.makedirs('data')
    while True:
        try:
            print(f"\n[{time.strftime('%H:%M:%S')}] Regime scan...")
            state = run_cycle()
            reverting = [r['asset'] for r in state['rows'] if r['daily_regime'] == 'reverting']
            up = [r['asset'] for r in state['rows'] if r['daily_regime'] == 'trending_up']
            print("-" * 78)
            print(f"  Daily reverting ({len(reverting)}): {', '.join(reverting) if reverting else 'none'}")
            print(f"  Daily trending_up ({len(up)}): {', '.join(up) if up else 'none'}")
            print("-" * 78)
            time.sleep(30 * 60)
        except KeyboardInterrupt:
            print("\n[Regime Lab] stopped safely.")
            break
        except Exception as e:
            print(f"\n[Regime Lab Error] {e}")
            time.sleep(60)


if __name__ == "__main__":
    run_engine()