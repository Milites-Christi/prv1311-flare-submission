"""
rider_decision_log.py — decision-log recorder for rider_team.py's entry loop.

Additive only: records what the existing gates already decided; changes
nothing about when a candidate fires, in what order, or against what
thresholds. Every write is wrapped so a Supabase hiccup can never propagate
into the trading loop.

log_decision(record) buffers one row per candidate; flush_decisions() upserts
the whole cycle's buffer in one call (called once at the end of run_cycle,
not once per candidate). log_cycle(record) writes the one-row-per-cycle
summary immediately.
"""

import time
from supabase_client import get_client
from screener import exchange, MIN_HISTORY_CANDLES

_decision_buffer = []

# candle_count is its own OHLCV fetch, independent of calculate_90_day_floor
# (which collapses maturity-fail / math-fail / API-error into one
# indistinguishable None). Doesn't change intraday -- cache 1h/symbol so a
# ~100-symbol universe doesn't double the Coinbase call volume every cycle.
_candle_cache = {}   # symbol -> (fetched_at, count)
CANDLE_CACHE_TTL_S = 3600


def log_decision(record: dict) -> None:
    try:
        _decision_buffer.append(record)
    except Exception:
        pass


def flush_decisions() -> None:
    global _decision_buffer
    if not _decision_buffer:
        return
    try:
        get_client().table("rider_decisions").insert(_decision_buffer).execute()
    except Exception:
        pass
    finally:
        _decision_buffer = []


def log_cycle(record: dict) -> None:
    try:
        get_client().table("rider_cycles").insert(record).execute()
    except Exception:
        pass


def get_candle_count(symbol: str):
    """Independent candle count + maturity read for logging only. Cached 1h
    per symbol, including failures (a transient API blip is cached as None
    for the TTL rather than hammered every cycle)."""
    now = time.time()
    cached = _candle_cache.get(symbol)
    if cached and (now - cached[0]) < CANDLE_CACHE_TTL_S:
        return cached[1]
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1d', limit=400)
        count = len(ohlcv) if ohlcv else 0
    except Exception:
        count = None
    _candle_cache[symbol] = (now, count)
    return count


def maturity_ok(candle_count):
    return (candle_count >= MIN_HISTORY_CANDLES) if candle_count is not None else None
