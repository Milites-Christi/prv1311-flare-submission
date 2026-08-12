"""
================================================================================
PROJECT: Prv1311 — screener (Coinbase live data)
FILE: screener.py
================================================================================
Live data access + the statistical signals the engines trigger on:
  1. fetch_live_price        -- current price
  2. calculate_90_day_floor  -- CORE trigger (5th-percentile floor)
                                + 180-DAY MATURITY GATE (bans unseasoned coins)
  3. rolling_7_day_high      -- RIDER trigger reference
  4. run_triple_confirmation -- discovery scanner (dynamic RSI + absorption + VWAP)
Coinbase quotes in USD; real live volume. The three institutional checks live in
their own modules (dynamic_rsi/taker_absorption/vwap_bands), imported here.
================================================================================
"""

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import threading
import ccxt
from config import EXCHANGE_ID, QUOTE, MAX_TICKER_DIVERGENCE_PCT
from dynamic_rsi import get_dynamic_rsi
from taker_absorption import check_absorption
from vwap_bands import calculate_vwap_bands

# Serializes every call through the one shared exchange instance across all of
# run_all.py's threads. Small and explicit, no token bucket -- closes the
# thread-safety gap in ccxt's own rate limiter under true concurrent access.
# Checked every exchange.<method>() call site in the repo for nested/recursive
# calls before adding this (ccxt's REST methods are synchronous with no
# callbacks, and no call site here invokes a second exchange call from within
# an already-active one) -- a plain, non-reentrant Lock is safe.
_exchange_lock = threading.Lock()


class _LockedExchange:
    """Transparent proxy: every callable attribute access serializes through
    _exchange_lock; non-callable attributes (e.g. .markets) pass through
    directly since they're just cached data, not network calls."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        attr = getattr(self._real, name)
        if not callable(attr):
            return attr

        def _locked(*args, **kwargs):
            with _exchange_lock:
                return attr(*args, **kwargs)
        return _locked


exchange = _LockedExchange(getattr(ccxt, EXCHANGE_ID)({'enableRateLimit': True}))

# --- Per-cycle daily-candle cache (Task E) -----------------------------------
# calculate_90_day_floor, rolling_7_day_high, and callers of daily closes (the
# regime gate, the anomaly gate) each independently pulled the same daily
# series for the same symbol. One real fetch per symbol per cycle now, shared
# by all of them -- same inputs, same outputs, fewer round trips. Deliberately
# EXCLUDES rider_decision_log.get_candle_count(): that fetch is independent BY
# DESIGN (it exists to catch calculate_90_day_floor failing while candle data
# is actually fine -- sharing the cache would erase exactly the discrepancy
# it's built to detect).
MAX_DAILY_CANDLES = 400
# Thread-local, not a plain module dict: run_all.py runs 8 engines as threads
# sharing this module, each calling clear_daily_cache() at the start of its
# OWN cycle. A shared dict would let one engine's clear wipe another's
# in-progress cache mid-cycle, plus a narrow race between a miss-check and the
# fetch that fills it (KeyError if another thread cleared in between). Thread-
# local storage means each thread's cache is only ever touched by that thread
# -- "per-cycle" correctly means "per-thread's-current-cycle." No behavior
# change for rider_team.py/footprint_worker.py (single-threaded processes,
# where thread-local == global).
_daily_cache_local = threading.local()


def _daily_cache():
    if not hasattr(_daily_cache_local, "store"):
        _daily_cache_local.store = {}
    return _daily_cache_local.store


def clear_daily_cache():
    """Call once at the start of each cycle. Explicit clear, no TTL -- a stale
    daily series carried across cycles is worse than an extra call."""
    _daily_cache().clear()


def get_daily_ohlcv(symbol, limit=MAX_DAILY_CANDLES):
    """Shared per-cycle cache for daily OHLCV candles, oldest -> newest, same
    shape as a raw fetch_ohlcv call. Fetches MAX_DAILY_CANDLES once per symbol
    (the largest window any consumer needs) and slices for callers wanting
    fewer -- identical to each of them fetching independently, since both are
    "the most recent N candles as of now" against the same underlying data.
    Exceptions propagate unchanged to the caller (not swallowed here): a
    failed fetch is simply never cached, so the next caller for that symbol
    retries independently, exactly as before this cache existed."""
    pair = symbol if '/' in symbol else f"{symbol}/{QUOTE}"
    cache = _daily_cache()
    if pair not in cache:
        ohlcv = exchange.fetch_ohlcv(pair, timeframe='1d', limit=MAX_DAILY_CANDLES)
        cache[pair] = ohlcv if ohlcv else []
    cached = cache[pair]
    if not cached:
        return cached
    return cached[-limit:] if limit < len(cached) else cached

# Minimum daily-candle history for an asset to be tradeable. A 90-day floor is
# meaningless on a coin that has only existed a few months -- it hasn't survived
# a full cycle, so its "floor" is a guess. Both CORE and RIDER need a valid
# floor, so this single gate bans unseasoned coins from the WHOLE system.
MIN_HISTORY_CANDLES = 280   # ~full year of daily candles (Coinbase caps ~300);
                            # young coins return fewer, revealing their age.
                            # LIGHTER (~199) fails, XLM (~300) passes.


# ==============================================================================
# CORE DATA FUNCTIONS (used by CORE + RIDER engines)
# ==============================================================================
def fetch_live_price(symbol):
    """Fetch current live price. Accepts 'XLM/USD' or 'XLM'.
    Sanity-checks the ticker against the last daily close -- diverges by more
    than MAX_TICKER_DIVERGENCE_PCT => reject (return None) rather than let a
    stale/corrupted print flow into any engine. One chokepoint, protects all of
    them. Logged loudly on rejection -- a silent None here would be exactly the
    failure class this was built to eliminate."""
    pair = symbol if '/' in symbol else f"{symbol}/{QUOTE}"
    try:
        ticker = exchange.fetch_ticker(pair)
        last = ticker['last']
    except Exception as e:
        print(f"[API Error] price for {pair}: {e}")
        return None

    try:
        ohlcv = get_daily_ohlcv(symbol, 2)
        last_close = ohlcv[-1][4] if ohlcv else None
    except Exception:
        last_close = None  # sanity check is a bonus safety net -- fail OPEN if unavailable

    if last_close and last:
        divergence_pct = abs(last - last_close) / last_close * 100.0
        if divergence_pct > MAX_TICKER_DIVERGENCE_PCT:
            print("=" * 70)
            print(f"[TICKER SANITY REJECT] {pair}: ticker={last} last_close={last_close} "
                  f"divergence={divergence_pct:.1f}% (max {MAX_TICKER_DIVERGENCE_PCT:.0f}%)")
            print("=" * 70)
            return None

    return last


def _percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    n = len(sorted_vals)
    rank = (p / 100.0) * (n - 1)
    low = int(rank)
    high = min(low + 1, n - 1)
    frac = rank - low
    return sorted_vals[low] + frac * (sorted_vals[high] - sorted_vals[low])


def calculate_90_day_floor(symbol, lookback_days=90, percentile=5, ohlcv_fn=None):
    """
    CORE trigger: 5th-percentile of the last ~90 daily closes.

    MATURITY GATE: the asset must return at least MIN_HISTORY_CANDLES daily
    candles. Coinbase caps daily history at ~300 candles for everyone, so a
    mature coin hits the cap (~300) while a young coin returns fewer, revealing
    its true age. LIGHTER (~199 candles, launched Dec 2025) fails this; XLM
    (~300, capped) passes. This guards against unseasoned coins whose 90-day
    floor is a pre-unlock guess. Returns None if too young OR on failure.

    ohlcv_fn: optional override for the candle source, same call/return shape
    as get_daily_ohlcv(symbol, limit). Every existing caller passes nothing
    and gets byte-identical Coinbase behavior -- this exists so rider_flare.py
    can inject flare.coingecko_adapter.get_cg_daily_ohlcv instead, without a
    second copy of this function's math.
    """
    pair = symbol if '/' in symbol else f"{symbol}/{QUOTE}"
    fetch = ohlcv_fn or get_daily_ohlcv
    try:
        ohlcv = fetch(symbol, MAX_DAILY_CANDLES)
        if not ohlcv:
            return None
        # MATURITY GATE
        if len(ohlcv) < MIN_HISTORY_CANDLES:
            return None
        # FLOOR from the recent lookback window
        closes = [row[4] for row in ohlcv[-lookback_days:]]
        if len(closes) < 30:
            return None
        return _percentile(sorted(closes), percentile)
    except Exception as e:
        print(f"[Floor Error] {pair}: {e}")
        return None


def rolling_7_day_high(symbol, lookback_days=7, ohlcv_fn=None):
    """RIDER reference: highest close in the last N days. None on failure.
    ohlcv_fn: same override as calculate_90_day_floor -- see its docstring."""
    pair = symbol if '/' in symbol else f"{symbol}/{QUOTE}"
    fetch = ohlcv_fn or get_daily_ohlcv
    try:
        ohlcv = fetch(symbol, lookback_days + 2)
        if not ohlcv or len(ohlcv) < 3:
            return None
        closes = [row[4] for row in ohlcv[-lookback_days:]]
        return max(closes)
    except Exception as e:
        print(f"[7d-high Error] {pair}: {e}")
        return None


# ==============================================================================
# DISCOVERY SCANNER — triple-confirmation (dynamic RSI + absorption + VWAP)
# ==============================================================================
def run_triple_confirmation(symbol, verbose=True):
    """
    Runs all three institutional checks. Returns a dict:
      { 'approved': bool, 'rsi': {...}, 'absorption': {...}, 'vwap': {...} }
    approved True ONLY when all three agree on the BUY side.
    NOTE: this is now the DISCOVERY gate (vets what ENTERS the basket), not a
    gate on CORE execution.
    """
    base = symbol.split('/')[0]
    rsi = get_dynamic_rsi(base)
    absorp = check_absorption(base)
    vwap = calculate_vwap_bands(base)

    rsi_pass = bool(rsi and rsi['signal'] == 'OVERSOLD')
    abs_pass = bool(absorp and absorp['signal'] == 'ABSORPTION')
    vwap_pass = bool(vwap and vwap['signal'] == 'OVEREXT_LOWER')
    approved = rsi_pass and abs_pass and vwap_pass

    if verbose:
        print(f"\n[{base}] TRIPLE-CONFIRMATION")
        print(f"  [1] Dynamic RSI   : {'PASS' if rsi_pass else 'fail'}"
              + (f" (RSI {rsi['rsi']} vs floor {rsi['lower']})" if rsi else " (no data)"))
        print(f"  [2] Absorption    : {'PASS' if abs_pass else 'fail'}"
              + (f" (sell {absorp['taker_sell_pct']}%, Δprice {absorp['price_delta_pct']}%)" if absorp else " (no data)"))
        print(f"  [3] VWAP band     : {'PASS' if vwap_pass else 'fail'}"
              + (f" (price ${vwap['price']:.4f} vs lower ${vwap['lower']:.4f})" if vwap else " (no data)"))
        print("  >>> " + ("APPROVED" if approved else "rejected") + " <<<")

    return {'symbol': base, 'approved': approved,
            'rsi': rsi, 'absorption': absorp, 'vwap': vwap}


if __name__ == "__main__":
    for test in ['XLM', 'XRP', 'ADA']:
        floor = calculate_90_day_floor(test)
        print(f"{test}: floor = {floor}")