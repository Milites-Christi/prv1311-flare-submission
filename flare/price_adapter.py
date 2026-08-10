"""
================================================================================
flare/price_adapter.py — FTSO price source for rider_flare.py (Flare
hackathon, Day 2, Task 2)
================================================================================
Single owner of the 20-symbol FTSO candidate universe. divergence.py and
divergence_recorder.py import FOOTPRINT_WORKER_20 from here rather than each
keeping their own copy -- that duplication is exactly how the three original
FTSO readers drifted apart before this hackathon's rebuild.

Coverage (which of the 20 actually resolve to a live feed) is established
once at import time via establish_coverage() -- the same bisection-based
discovery flare/ftso.py already does, not a second implementation of it.

get_live_price() returns a tuple, not a bare float, because rider_flare's
decision-log record needs the source tag + oracle timestamp + feed id
alongside the price -- callers that only want the number should unpack
price, _, _, _ = get_live_price(sym).
================================================================================
"""

from flare.ftso import establish_coverage, get_price

FOOTPRINT_WORKER_20 = [
    "BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "LINK/USD", "AAVE/USD", "UNI/USD",
    "ONDO/USD", "AVAX/USD", "NEAR/USD", "LDO/USD", "COTI/USD", "EUL/USD", "KAITO/USD",
    "HBAR/USD", "FLR/USD", "ADA/USD", "XLM/USD", "ARB/USD", "OP/USD",
]

# Established once at import -- every module that imports price_adapter shares
# this coverage answer instead of re-bisecting the same 20 symbols redundantly.
COVERAGE = establish_coverage(FOOTPRINT_WORKER_20)
FLARE_UNIVERSE = sorted(COVERAGE.keys())


def get_live_price(symbol):
    """'BTC/USD' -> (price, 'flare', oracle_timestamp, feed_id), or None if
    this symbol has no live FTSOv2 feed. NEVER falls back to Coinbase --
    that would make a 'Flare-priced' ledger secretly part-Coinbase. Callers
    needing a fallback must do it themselves, explicitly."""
    p = get_price(symbol)
    if p is None:
        return None
    return (p.price, 'flare', p.timestamp, p.feed_id)
