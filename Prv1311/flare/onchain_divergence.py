"""
================================================================================
flare/onchain_divergence.py — CoinGecko (off-chain) vs on-chain swap-derived
divergence measurement, FLR and FXRP (Flare hackathon, Task 2 follow-on)
================================================================================
Same shape as flare/divergence.py (FTSO vs. Coinbase), new inputs: this
compares CoinGecko's live off-chain price against the most recent real swap
price read straight from the FLR and FXRP pool contracts (flare.onchain_swaps
-- see that module's docstring for why these two pools, and why this reads
the block explorer's indexed logs rather than a Goldsky subgraph). This IS
the deliverable, not a debug print, same as divergence.py's own opening line.

READ-ONLY, MEASUREMENT-ONLY -- explicitly not wired into any gate. Nothing in
rider_team.py's or rider_flare.py's entry/exit chain imports this module or
flare.onchain_swaps; confirmed by grep, logged in docs/CHANGELOG.md 2026-08-11.
================================================================================
"""

import time
from dataclasses import dataclass

from flare.coingecko_adapter import get_current_price
from flare.onchain_swaps import fetch_recent_swaps, POOLS

# On-chain symbol -> CoinGecko reference symbol. Not always identity: FXRP is
# a wrapped, over-collateralized representation of XRP (flare-fassets), not
# its own asset -- coingecko_adapter.COINGECKO_ID has no 'FXRP' entry because
# there's no independent FXRP market to quote. The meaningful off-chain
# reference for the FXRP pool IS XRP's price -- that comparison is the actual
# question ("does the wrap hold its peg on-chain"), not a workaround.
OFFCHAIN_REFERENCE_SYMBOL = {"FLR": "FLR", "FXRP": "XRP"}


@dataclass
class OnchainDivergenceRow:
    symbol: str
    offchain_price: float          # CoinGecko, live
    onchain_price: float           # most recent on-chain swap price
    onchain_swap_ts: int           # unix seconds, the swap's own block timestamp
    pool_address: str
    pool_pair: str
    spread_bps: float


def measure_divergence(symbols=None):
    """One-shot: for each symbol, CoinGecko's live price vs. the most recent
    real swap price from its pool. Skips a symbol if either side has nothing
    -- never fabricates a comparison out of partial data."""
    symbols = symbols or list(POOLS.keys())
    rows = []
    for sym in symbols:
        offchain_price = get_current_price(OFFCHAIN_REFERENCE_SYMBOL.get(sym, sym))
        swaps = fetch_recent_swaps(sym, max_age_hours=6, max_pages=50)
        if offchain_price is None or not swaps:
            continue
        onchain_ts, onchain_price = swaps[-1]   # most recent swap (list is oldest-first)
        spread_bps = (onchain_price - offchain_price) / offchain_price * 10000.0
        rows.append(OnchainDivergenceRow(
            symbol=sym, offchain_price=offchain_price, onchain_price=onchain_price,
            onchain_swap_ts=onchain_ts, pool_address=POOLS[sym]["pool_address"],
            pool_pair=POOLS[sym]["pair"], spread_bps=spread_bps,
        ))
    return rows


def print_report(rows, label=""):
    print("=" * 100)
    print(f"CoinGecko (off-chain) vs on-chain swap divergence{f' -- {label}' if label else ''}  "
          f"({time.strftime('%Y-%m-%d %H:%M:%S')})")
    print("=" * 100)
    print(f"{'symbol':<8} {'offchain':<14} {'onchain':<14} {'pool':<20} {'spread_bps':>12}")
    for r in sorted(rows, key=lambda r: abs(r.spread_bps), reverse=True):
        print(f"{r.symbol:<8} {r.offchain_price:<14.6g} {r.onchain_price:<14.6g} "
              f"{r.pool_pair:<20} {r.spread_bps:>12.2f}")


if __name__ == "__main__":
    rows = measure_divergence()
    print_report(rows)
