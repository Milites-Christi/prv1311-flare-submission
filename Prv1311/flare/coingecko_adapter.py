"""
================================================================================
flare/coingecko_adapter.py — CoinGecko daily-close source, higher quote
resolution than Coinbase for the low-volume alts in FLARE_UNIVERSE
================================================================================
WHY THIS EXISTS: rider_team.py and rider_flare.py's pullback gate compares the
live price against a 7-day high and a 90-day floor computed from Coinbase daily
closes. For thin Coinbase alt pairs, that comparison is often a tautology, not
a real signal -- Coinbase's own quote doesn't move. Empirically verified
2026-08-11 over a same-window, same-granularity (hourly) comparison across all
16 FLARE_UNIVERSE symbols: CoinGecko's cross-venue-aggregated price returned a
distinct value on every single sample (169/169) for all 16 symbols, while
Coinbase repeated the same tick for extended stretches on the thinner pairs --
worst cases OP (8/155 distinct, ~95% flat) and FLR (22/168, ~87% flat). See
docs/CHANGELOG.md 2026-08-11 for the full per-symbol table. CoinGecko isn't
just a second source here -- it's the one with enough resolution to express a
real pullback for these symbols at all.

ENDPOINT CHOICE, EMPIRICALLY VERIFIED (2026-08-11): CoinGecko's `/ohlc` endpoint
auto-selects candle granularity from the `days` window with no override --
at days=90 it returned 23 candles spaced 4 days apart, useless for a 90-day
floor. `/market_chart` with `interval=daily` forced returns true ~24h-spaced
points (91 points at days=90) and currently works unauthenticated. That's the
primary path here. `interval=daily` is undocumented/unstable behavior on the
free tier, not a contract Claude -- CoinGecko could change or gate it without
notice, which is exactly why this module treats it as untrusted and carries an
automatic fallback rather than assuming it always works.

FALLBACK: on any failure of the interval=daily call (HTTP error, timeout,
malformed response), retry the same window through the *default* `/market_chart`
granularity (hourly, for a 2-90 day window on the free tier) and resample to
one point per UTC calendar day by taking the last sample of each day. Callers
get a `source` tag back ('coingecko_daily' vs 'coingecko_hourly_resampled') so
a decision-log record can show which path actually served the data -- a
resampled fallback is real data, but it is not the same resolution promise as
the primary path, and that distinction matters enough to log.

NEVER falls back further to Coinbase -- same rule as price_adapter.get_live_price:
a caller that wants a Coinbase fallback does that explicitly, it is not hidden
in here.
================================================================================
"""

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import threading
import time
import urllib.error
import urllib.request
import json
from datetime import datetime, timezone

# Static id map -- deliberately NOT derived from flare.price_adapter.FLARE_UNIVERSE.
# That module's import triggers a live FTSO establish_coverage() RPC call as a
# side effect; this module has to be importable on its own (rider_team.py is a
# plausible caller and has nothing to do with FTSO) without paying that cost.
COINGECKO_ID = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple",
    "LINK": "chainlink", "AAVE": "aave", "UNI": "uniswap", "ONDO": "ondo-finance",
    "AVAX": "avalanche-2", "NEAR": "near", "HBAR": "hedera-hashgraph",
    "FLR": "flare-networks", "ADA": "cardano", "XLM": "stellar",
    "ARB": "arbitrum", "OP": "optimism",
}

CG_BASE = "https://api.coingecko.com/api/v3"

# Empirically, unauthenticated /market_chart tolerates roughly this spacing
# before 429s under light, sequential use (one call per symbol per cycle, not
# a burst) -- see docs/CHANGELOG.md 2026-08-11. A tight retry loop still hit
# 429s at this spacing during the bulk 16-symbol research pass; that pass was
# an unrepresentative burst (16 calls back-to-back with nothing else going on),
# not this module's actual call pattern, so the spacing itself is unchanged
# from what was scoped -- the 429 case is handled by the daily->hourly fallback
# already, not by adding a heavier rate limiter here.
MIN_CALL_SPACING_S = 1.5

_last_call_ts = 0.0

# One bounded retry on 429 specifically, not a general retry framework --
# empirically (docs/CHANGELOG.md 2026-08-11) a sequential run of calls at
# MIN_CALL_SPACING_S alone still hit 429s after ~5 calls. A single longer
# wait here catches the common transient case before falling through to the
# caller's daily->hourly fallback, which is also unauthenticated CoinGecko
# and would otherwise usually hit the exact same 429.
RATE_LIMIT_RETRY_WAIT_S = 8.0


def _throttled_get(url):
    global _last_call_ts
    elapsed = time.time() - _last_call_ts
    if elapsed < MIN_CALL_SPACING_S:
        time.sleep(MIN_CALL_SPACING_S - elapsed)
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                body = r.read().decode()
            _last_call_ts = time.time()
            return json.loads(body)
        except urllib.error.HTTPError as e:
            _last_call_ts = time.time()
            if e.code == 429 and attempt < 2:
                time.sleep(RATE_LIMIT_RETRY_WAIT_S * (attempt + 1))
                continue
            raise


def _validate_prices(data):
    """CoinGecko's response is untrusted external data -- validate its shape
    before anything downstream treats it as a price series. Returns a list of
    (timestamp_ms, price) or raises ValueError."""
    if not isinstance(data, dict) or "prices" not in data:
        raise ValueError("market_chart response missing 'prices'")
    prices = data["prices"]
    if not isinstance(prices, list) or not prices:
        raise ValueError("market_chart 'prices' is empty or not a list")
    out = []
    for point in prices:
        if not (isinstance(point, list) and len(point) == 2):
            raise ValueError("malformed price point in market_chart response")
        ts, price = point
        if not isinstance(ts, (int, float)) or not isinstance(price, (int, float)):
            raise ValueError("non-numeric price point in market_chart response")
        out.append((int(ts), float(price)))
    return out


def _resample_to_daily(hourly_points):
    """[(ts_ms, price), ...] any granularity -> one point per UTC calendar
    day, keeping the LAST sample seen for that day (closest thing to a daily
    close this data has)."""
    by_day = {}
    for ts, price in hourly_points:
        day = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date()
        by_day[day] = (ts, price)  # later entries overwrite -- input is time-ordered
    return [by_day[d] for d in sorted(by_day)]


def get_daily_closes(symbol, days=90):
    """'XRP' -> ([(timestamp_ms, price), ...] oldest-first, source_tag), or
    (None, None) if both the primary and fallback calls failed. source_tag is
    'coingecko_daily' or 'coingecko_hourly_resampled' -- never silently
    treated as equivalent by this function; that's the caller's call."""
    cg_id = COINGECKO_ID.get(symbol)
    if cg_id is None:
        return None, None

    try:
        data = _throttled_get(
            f"{CG_BASE}/coins/{cg_id}/market_chart?vs_currency=usd&days={days}&interval=daily")
        return _validate_prices(data), "coingecko_daily"
    except (urllib.error.URLError, ValueError, json.JSONDecodeError, TimeoutError):
        pass  # fall through to the hourly-resample fallback below

    try:
        data = _throttled_get(
            f"{CG_BASE}/coins/{cg_id}/market_chart?vs_currency=usd&days={days}")
        return _resample_to_daily(_validate_prices(data)), "coingecko_hourly_resampled"
    except (urllib.error.URLError, ValueError, json.JSONDecodeError, TimeoutError):
        return None, None


# CoinGecko's free-tier /market_chart caps historical range at 365 days
# (empirically confirmed 2026-08-11: days=400 -> 401 Unauthorized, days=365 ->
# 366 points). screener.py's MIN_HISTORY_CANDLES maturity gate (280) fits
# comfortably under that cap, so this is the "full window" fetch size for the
# per-cycle cache below -- same role as screener.MAX_DAILY_CANDLES (400) plays
# for the Coinbase path, just capped to what this source can actually serve.
CG_MAX_DAILY_DAYS = 365

# Thread-local cache, TTL-based rather than screener.py's hard per-cycle
# clear. EMPIRICALLY REQUIRED, not a stylistic choice: wiring this in and
# running one real rider_flare cycle (16 symbols, 2026-08-11) with a hard
# per-cycle clear reproduced the same 429 burst seen during the original
# research pass -- 6 of 16 symbols (including FLR) exhausted both the daily
# and hourly-resample paths and came back empty. Coinbase's daily-candle
# fetch has no such rate limit, which is why its cache can safely be "explicit
# clear, no TTL, an extra call is cheap." CoinGecko's isn't cheap -- a hard
# clear every 15-minute cycle means this exact burst repeats forever. Daily
# candles only change once a day regardless of source, so a 1-hour TTL loses
# nothing that matters to a 90-day floor or 7-day high while cutting real call
# volume by ~4x and giving a failed symbol multiple cycles to recover before
# its cached value actually expires. Deliberately a SEPARATE cache from
# screener's, not a shared one: this module has to be importable without
# pulling screener's Coinbase-only cache into the mix.
CG_CACHE_TTL_S = 3600

_cg_daily_cache_local = threading.local()


def _cg_daily_cache():
    if not hasattr(_cg_daily_cache_local, "store"):
        _cg_daily_cache_local.store = {}
    return _cg_daily_cache_local.store


def clear_cg_daily_cache():
    """Call once at the start of each cycle, same call site as
    screener.clear_daily_cache() -- but TTL-gated, not a blind full clear:
    only entries past CG_CACHE_TTL_S are actually dropped. A fresh entry
    survives across cycles on purpose (see CG_CACHE_TTL_S comment above)."""
    now = time.time()
    cache = _cg_daily_cache()
    stale = [k for k, (fetched_at, _rows) in cache.items() if now - fetched_at > CG_CACHE_TTL_S]
    for k in stale:
        del cache[k]


def get_cg_daily_ohlcv(symbol, limit=CG_MAX_DAILY_DAYS):
    """Drop-in replacement for screener.get_daily_ohlcv(), same call shape and
    same return shape ([ts_ms, open, high, low, close, volume] rows,
    oldest-first) so it can be injected wherever that function is, unmodified,
    via the ohlcv_fn parameter added to calculate_90_day_floor/
    rolling_7_day_high/daily_closes. CoinGecko's market_chart is a price
    series, not real OHLC -- there's no genuine open/high/low/volume here, so
    each row sets open=high=low=close=price and volume=0.0. This is an honest
    shape-compatibility shim, not fabricated spread data: every caller in
    this codebase only ever reads the close (index 4). Accepts 'XRP/USD' or
    'XRP'. Returns [] if the symbol has no CoinGecko id or both fetch paths
    failed -- callers already treat an empty/short series as a fetch failure
    (screener.calculate_90_day_floor's maturity gate), same as a real Coinbase
    outage would."""
    base = symbol.split('/')[0].upper()
    cache = _cg_daily_cache()
    if base not in cache:
        points, source = get_daily_closes(base, days=CG_MAX_DAILY_DAYS)
        if points:
            rows = [[ts, price, price, price, price, 0.0] for ts, price in points]
            cache[base] = (time.time(), rows)
            print(f"[coingecko_adapter] {base}: {len(points)} daily points via {source}")
        else:
            cache[base] = (time.time(), [])
            print(f"[coingecko_adapter] {base}: fetch failed (both daily and hourly-resample paths)")
    _fetched_at, cached = cache[base]
    if not cached:
        return cached
    return cached[-limit:] if limit < len(cached) else cached


def get_current_price(symbol):
    """'XRP' -> current USD price (float) via /simple/price, or None on
    failure. Lightweight single-call live quote, deliberately NOT routed
    through the daily-candle cache above (different lifetime, different
    purpose). Used by flare/onchain_divergence.py for a live off-chain-vs-
    on-chain comparison, same shape as flare/divergence.py's live
    FTSO-vs-Coinbase comparison."""
    base = symbol.split('/')[0].upper()
    cg_id = COINGECKO_ID.get(base)
    if cg_id is None:
        return None
    try:
        data = _throttled_get(f"{CG_BASE}/simple/price?ids={cg_id}&vs_currencies=usd")
        price = data.get(cg_id, {}).get("usd") if isinstance(data, dict) else None
        return float(price) if isinstance(price, (int, float)) else None
    except (urllib.error.URLError, json.JSONDecodeError, ValueError, TypeError):
        return None


if __name__ == "__main__":
    for sym in ["XRP", "OP", "FLR"]:
        points, source = get_daily_closes(sym, days=14)
        n = len(points) if points else 0
        print(f"{sym}: {n} daily points via {source}  (expect ~14-15 points, source='coingecko_daily')")

    print()
    clear_cg_daily_cache()
    for sym in ["XRP/USD", "OP/USD"]:
        ohlcv = get_cg_daily_ohlcv(sym, limit=90)
        print(f"{sym}: {len(ohlcv)} rows via get_cg_daily_ohlcv "
              f"(expect ~90, shape [ts,o,h,l,c,v])  sample last row: {ohlcv[-1] if ohlcv else None}")
