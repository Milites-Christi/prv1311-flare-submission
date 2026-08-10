"""
================================================================================
flare/divergence.py — FTSO vs. Coinbase divergence measurement (Flare
hackathon, Day 1, Task 3). This IS the deliverable, not a debug print.
================================================================================
Pulls every symbol in the A/B universe (assets with both a live FTSOv2 feed
AND footprint_gate coverage) from both price sources and reports the spread.
This is the actual hypothesis under test this week: does a decentralized
oracle diverge from a centralized venue, by how much, and is it worse on
thinner assets than on BTC/ETH.

Coinbase side reads exchange.fetch_tickers() (bulk, one call for the whole
universe) directly, not screener.fetch_live_price() -- this is a measurement
script, not a trading gate, so it skips the ticker-sanity-check overhead
deliberately and instead reads the exchange's own reported ticker timestamp
for a fair side-by-side comparison against the oracle's own timestamp. Bulk
over per-symbol matters here specifically: one venue snapshot lining up
against one oracle batch tightens timestamp_gap_ms, and it's 16x less
Coinbase traffic than 16 individual fetch_ticker calls (Day 2 change #2).
================================================================================
"""

import time
from dataclasses import dataclass, asdict

from screener import exchange
from flare.ftso import get_price
from flare.price_adapter import FLARE_UNIVERSE


def ab_universe():
    """The A/B universe: symbols with BOTH a live FTSOv2 feed AND footprint
    worker coverage. Coverage was already established once, at import time,
    by price_adapter -- every A/B script shares that one answer instead of
    re-bisecting the same 20 symbols redundantly."""
    return FLARE_UNIVERSE


@dataclass
class DivergenceRow:
    symbol: str
    ftso_price: float
    ftso_ts: int
    feed_id: str
    venue_price: float
    venue_ts: float
    spread_bps: float


def measure_divergence(symbols):
    rows = []
    try:
        tickers = exchange.fetch_tickers(symbols)
    except Exception as e:
        print(f"[!] Coinbase bulk ticker fetch failed: {e}")
        tickers = {}

    for sym in symbols:
        ftso = get_price(sym)
        ticker = tickers.get(sym)
        if ticker is None:
            venue_price, venue_ts = None, None
        else:
            venue_price = ticker.get("last")
            venue_ts = ticker["timestamp"] / 1000.0 if ticker.get("timestamp") else None

        if ftso is None or venue_price is None:
            continue

        spread_bps = (ftso.price - venue_price) / venue_price * 10000.0
        rows.append(DivergenceRow(
            symbol=sym, ftso_price=ftso.price, ftso_ts=ftso.timestamp, feed_id=ftso.feed_id,
            venue_price=venue_price, venue_ts=venue_ts, spread_bps=spread_bps,
        ))
    return rows


def print_report(rows, label=""):
    print("=" * 100)
    print(f"FTSO vs Coinbase divergence{f' -- {label}' if label else ''}  ({time.strftime('%Y-%m-%d %H:%M:%S')})")
    print("=" * 100)
    print(f"{'symbol':<10} {'ftso_price':<14} {'ftso_ts':<12} {'venue_price':<14} {'venue_ts':<12} {'spread_bps':>12}")
    for r in sorted(rows, key=lambda r: abs(r.spread_bps), reverse=True):
        print(f"{r.symbol:<10} {r.ftso_price:<14.6g} {r.ftso_ts:<12} {r.venue_price:<14.6g} "
              f"{r.venue_ts:<12.0f} {r.spread_bps:>12.2f}")


if __name__ == "__main__":
    universe = ab_universe()
    print(f"A/B universe ({len(universe)}): {universe}\n")
    rows = measure_divergence(universe)
    print_report(rows)
