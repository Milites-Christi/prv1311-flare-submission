"""
================================================================================
flare/rider_flare.py — FTSO-priced Rider twin (Flare hackathon, Day 2, Task 3)
================================================================================
Same CORE/RIDER entry/exit gate logic as rider_team.py -- imported, not
copied, via rider_team.run_engine()'s default-preserving parameterization --
run against a fixed 16-symbol universe (the confirmed FTSOv2 A/B set, from
flare/price_adapter.py) priced off Flare mainnet instead of Coinbase's ticker.

HONEST ABOUT WHAT'S FLARE-PRICED AND WHAT ISN'T: the price used for
entry/exit decisions is FTSO. The daily candles feeding the regime + anomaly
gates, the 90-day floor, and the rolling 7-day high are now CoinGecko
(flare.coingecko_adapter), not Coinbase -- swapped 2026-08-11 after verifying
CoinGecko's cross-venue-aggregated price has meaningfully finer quote
resolution than Coinbase's thin alt pairs (see docs/CHANGELOG.md 2026-08-11
for the per-symbol before/after). This is a pure data-source swap through the
same ohlcv_fn parameter mechanism price_fn already used -- none of the gate
comparisons themselves changed. Order-book imbalance and order-flow still
read Coinbase -- Flare has no order book or trade tape to build those from,
and CoinGecko doesn't have live order-book depth either.

Runs decoupled from rider_team.py -- separate ledger
(data/rider_flare_ledger.json), separate Supabase state table
(rider_flare_state), decision/cycle rows tagged fleet='rider_flare'. The
mandatory reason this fleet tag is explicit rather than relying on any
column default: if it were ever silently missing, rows would land tagged
'rider' with no error, and the whole week's A/B comparison would be
contaminated with no signal that it happened.
================================================================================
"""

import os

import rider_team
from flare.price_adapter import get_live_price, FLARE_UNIVERSE
from flare.coingecko_adapter import get_cg_daily_ohlcv, clear_cg_daily_cache

FLEET = 'rider_flare'
LEDGER_FILE = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "rider_flare_ledger.json"))
STATE_TABLE = "rider_flare_state"
HEALTH_COMPONENT = "rider_flare_engine"


def _price_fn(symbol):
    """Adapts price_adapter's (price, source, oracle_ts, feed_id) tuple down
    to the bare float rider_team.run_cycle()/run_engine() expect. FTSO-only
    -- returns None (never falls back to Coinbase) for anything outside the
    confirmed 16, which is every symbol rider_team's gates would otherwise
    try, since _universe() below is the only thing keeping evaluation
    scoped to the 16 in the first place."""
    result = get_live_price(symbol)
    return result[0] if result else None


def _universe():
    """Fixed to the confirmed FTSOv2 A/B set -- not a live market scan.
    Anything outside this list has no Flare price to trade on."""
    return FLARE_UNIVERSE


if __name__ == "__main__":
    rider_team.run_engine(
        price_fn=_price_fn,
        fleet=FLEET,
        ledger_file=LEDGER_FILE,
        health_component=HEALTH_COMPONENT,
        state_table=STATE_TABLE,
        universe_fn=_universe,
        log_name='rider_flare',
        ohlcv_fn=get_cg_daily_ohlcv,
        clear_cache_fn=clear_cg_daily_cache,
    )
