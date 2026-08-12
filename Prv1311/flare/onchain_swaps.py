"""
================================================================================
flare/onchain_swaps.py — direct on-chain swap-log reader for the FLR and FXRP
pools (Flare hackathon, Task 2: on-chain vs off-chain divergence measurement)
================================================================================
NOT A GOLDSKY SUBGRAPH. A real Goldsky deployment needs an account and API
key this repo doesn't have (verified 2026-08-11: no GOLDSKY_* key in .env,
and `goldsky` isn't even an npm package -- their real CLI ships separately
and needs `goldsky login`). Rather than fake a subgraph or block on getting
an account, this reads the exact same real thing a subgraph would have given
-- on-chain swap-derived OHLC -- straight from the pool contracts via the
Flare block explorer's indexed log API. Named honestly in the CHANGELOG and
roadmap as direct on-chain log indexing, not Goldsky.

WHY THE BLOCK EXPLORER, NOT eth_getLogs DIRECTLY: the public Flare RPC caps
eth_getLogs at 30 blocks per call (empirically confirmed 2026-08-11 --
anything wider returns "requested too many blocks ... maximum is set to
30"), useless for pulling a day of swap history one 54-second window at a
time. flare-explorer.flare.network (Blockscout) serves the same log data
from its own index with no such cap, and hands back a pre-decoded Swap event
plus block timestamp on every row -- no manual ABI decoding, no per-block RPC
round-trips.

WHY THESE TWO POOLS, NOT THE SPARKDEX V4 POOLS THE TASK NAMED: the v4 pools
(the largest FLR/FXRP liquidity found in the 2026-08-11 DEX survey --
$1.3-1.8M) are NOT standard Uniswap V3 pool contracts. token0()/token1()/
slot0() calls against them revert (confirmed 2026-08-11), consistent with
SparkDEX v4 being a hooks/singleton architecture this repo has no verified
ABI for -- guessing at one and silently mis-decoding prices would be worse
than picking a smaller, real pool. Used the largest USD-stable-quoted pool
of each asset confirmed to be a genuine Uniswap V3 fork instead (token0/
token1/fee/slot0 all resolve correctly):
  FLR:  WFLR/USDT0 0.3%  on Enosys v3   ($363K reserve, 2026-08-11)
  FXRP: FXRP/USDT0 0.05% on SparkDEX v3.1 ($404K reserve, 2026-08-11)

READ-ONLY, MEASUREMENT-ONLY. This module has no callers in rider_team.py or
rider_flare.py's entry/exit gate chain -- see flare/onchain_divergence.py
and docs/CHANGELOG.md 2026-08-11 for the explicit isolation check.
================================================================================
"""

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import json
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

EXPLORER_BASE = "https://flare-explorer.flare.network/api/v2"

# Uniswap V3 Swap(address,address,int256,int256,uint160,uint128,int24) topic0.
# Not computed at import time (that would need web3 loaded just for a keccak)
# -- this is the well-known, unchanging topic hash for that exact event
# signature across every standard V3 fork, including both pools below.
SWAP_TOPIC0 = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"

# Verified 2026-08-11 against the real IUniswapV3Pool interface (token0/
# token1/fee/slot0 all resolved) -- see module docstring for why these two,
# not the SparkDEX v4 pools. token0 is always the priced asset here, token1
# is always the USD-stable quote.
POOLS = {
    "FLR": {
        "pool_address": "0x3c2a7b76795e58829faaa034486d417dd0155162",
        "dex": "enosys-v3-flare",
        "pair": "WFLR/USDT0 0.3%",
        "token0_decimals": 18,   # WFLR
        "token1_decimals": 6,    # USDT0
    },
    "FXRP": {
        "pool_address": "0x88d46717b16619b37fa2dfd2f038defb4459f1f7",
        "dex": "sparkdex-v3-1",
        "pair": "FXRP/USDT0 0.05%",
        "token0_decimals": 6,    # FXRP
        "token1_decimals": 6,    # USDT0
    },
}

MIN_CALL_SPACING_S = 0.3   # Blockscout's own index, not a rate-limited third
                           # party API -- empirically fine at this spacing
                           # (2026-08-11: 6 sequential pages, zero errors).
_last_call_ts = 0.0


def _get(url):
    global _last_call_ts
    elapsed = time.time() - _last_call_ts
    if elapsed < MIN_CALL_SPACING_S:
        time.sleep(MIN_CALL_SPACING_S - elapsed)
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode()
    finally:
        _last_call_ts = time.time()
    return json.loads(body)


def _parse_swap_log(item, token0_decimals, token1_decimals):
    """One Blockscout log item -> (unix_ts, price) or None. Blockscout's
    response is untrusted external data -- validate the decoded shape before
    trusting any of it, same posture as coingecko_adapter._validate_prices.
    price = |amount1| / 10**dec1  /  |amount0| / 10**dec0 -- the executed
    trade price of THIS swap (token0 in asset terms, token1 in USD terms),
    not the resting pool price after it. Sign tells direction, not needed
    once both are made absolute."""
    decoded = item.get("decoded")
    if not isinstance(decoded, dict):
        return None
    params = {p.get("name"): p.get("value") for p in decoded.get("parameters", [])
              if isinstance(p, dict)}
    ts_raw = item.get("block_timestamp")
    if not ts_raw or "amount0" not in params or "amount1" not in params:
        return None
    try:
        amount0 = abs(int(params["amount0"]))
        amount1 = abs(int(params["amount1"]))
        if amount0 == 0:
            return None
        price = (amount1 / (10 ** token1_decimals)) / (amount0 / (10 ** token0_decimals))
        ts = int(datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp())
        return ts, price
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def fetch_recent_swaps(symbol, max_age_hours=24, max_pages=200):
    """symbol: 'FLR' or 'FXRP'. Paginates flare-explorer's indexed log API
    (not eth_getLogs -- see module docstring) back through this pool's
    history until either max_age_hours is exceeded or max_pages is hit,
    whichever comes first -- a bounded recent window, matching the existing
    flare/divergence.py posture of "recent measurement," not a full backfill.
    Returns [(unix_ts, price), ...] oldest-first, or [] on total failure."""
    cfg = POOLS.get(symbol)
    if cfg is None:
        return []
    cutoff = time.time() - max_age_hours * 3600
    url = f"{EXPLORER_BASE}/addresses/{cfg['pool_address']}/logs"
    swaps = []
    for _page in range(max_pages):
        try:
            data = _get(url)
        except (urllib.error.URLError, json.JSONDecodeError):
            break
        items = data.get("items", [])
        if not items:
            break
        hit_cutoff = False
        for item in items:
            if item.get("topics", [None])[0] != SWAP_TOPIC0:
                continue
            parsed = _parse_swap_log(item, cfg["token0_decimals"], cfg["token1_decimals"])
            if parsed is None:
                continue
            ts, price = parsed
            if ts < cutoff:
                hit_cutoff = True
                continue
            swaps.append((ts, price))
        if hit_cutoff:
            break
        params = data.get("next_page_params")
        if not params:
            break
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{EXPLORER_BASE}/addresses/{cfg['pool_address']}/logs?{qs}"
    return sorted(swaps)


def get_onchain_daily_ohlc(symbol, max_age_hours=24, max_pages=200):
    """Real OHLC this time (unlike coingecko_adapter's shim) -- built from
    actual per-swap executed prices, not a single aggregated point. Returns
    {date: {'open','high','low','close','swap_count'}} for whichever UTC
    calendar days the fetched swaps cover."""
    swaps = fetch_recent_swaps(symbol, max_age_hours, max_pages)
    by_day = defaultdict(list)
    for ts, price in swaps:
        day = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        by_day[day].append(price)
    return {
        day: {
            "open": prices[0], "high": max(prices), "low": min(prices),
            "close": prices[-1], "swap_count": len(prices),
        }
        for day, prices in sorted(by_day.items())
    }


if __name__ == "__main__":
    for sym in ["FLR", "FXRP"]:
        ohlc = get_onchain_daily_ohlc(sym, max_age_hours=6, max_pages=30)
        print(f"{sym}: {ohlc}")
