"""
================================================================================
flare/ftso.py — the one canonical FTSOv2 reader (Flare hackathon, Day 1)
================================================================================
Replaces three prior copies of this logic (root flare_ftso.py [Coston2, dead,
since renamed to flare_ftso_legacy.py to remove a case-insensitive filename
collision that was fatal on Linux], Flare_Trial.py\\Flare_ftso.py [mainnet,
cached, dead, since deleted along with the rest of Flare_Trial.py\\], and the
inline copy in the file then named solo_rider_flare.py, since rewritten and
renamed to solo_rider.py]. Mainnet only.

EMPIRICAL FINDING THAT SHAPED THIS DESIGN: getFeedsById does NOT return a zero
value for a feed that doesn't exist -- it REVERTS THE ENTIRE BATCH with
"feed does not exist". One bad symbol in a tracked set would otherwise freeze
every other symbol's price indefinitely. Fixed with two decisions:

  1. FIXED KNOWN-GOOD UNIVERSE. get_price() only ever serves symbols already
     confirmed (via establish_coverage()) to have a real feed -- asking for
     anything untested returns None immediately, no attempt to add it to a
     live batch. This hackathon's whole premise is a curated A/B universe, not
     open-ended symbol discovery, so this is a feature, not a limitation.
  2. BISECTION AS THE SHARED MECHANISM. _call_batch() is what both
     establish_coverage() (Task 2, discovering which of a candidate list
     resolve) and the hot-path cache refresh (self-healing if a previously-
     good feed unexpectedly stops resolving) use. On a revert, it splits the
     batch and retries each half, recursing to isolate exactly which symbol(s)
     are bad -- never falls back to the singular getFeedById to do this,
     staying inside "always getFeedsById" even while probing one symbol at a
     time (a batch of size 1 is still getFeedsById, just a short list).

OTHER DESIGN NOTES (unchanged from the first pass):
  - getFeedById/getFeedsById are `external payable`, not `view`. eth_call
    still reads them for free (value=0, no transaction, no wallet) -- but a
    future non-zero fee could revert a bare call, so FeeCalculator.
    calculateFeeByIds() is queried live and passed as `value` on every batch.
  - Module-level cache, ~2s TTL, matching FTSOv2's own ~1.8s block-latency
    cadence -- polling faster than the data changes just wastes calls.
  - get_price() returns None on no feed. NEVER falls back to a centralized
    venue -- that would make the "Flare ledger" secretly part-Coinbase.
  - record_health('flare_ftso', ...) after 3 CONSECUTIVE genuine RPC/transport
    failures (NOT "feed does not exist" reverts, which are an expected,
    meaningful response, not a failure) -- same threshold as footprint_gate's
    BLIND_TRANSPORT.

REAL BUG, FOUND LIVE (Day 5+): the coverage set (_known_good) only ever
shrank. Under a transient RPC issue that surfaces as ContractLogicError
rather than a clean transport error, bisection recurses all the way to
single symbols and EVERY one can look individually "confirmed bad" --
pruning the entire set to empty, permanently, since nothing ever re-ran
establish_coverage() after process start. divergence_recorder.py ran for
~45 hours and wrote zero rows because of exactly this. Fixed with four
additions, all still routed through _call_batch/bisection -- never
getFeedById:
  3. LIVENESS PROBE BEFORE PRUNING. Before removing any symbol from
     _known_good, _rpc_is_healthy() independently re-checks FLR/USD (feed
     index 0, Flare's own token -- as close to "always resolves" as this
     network has). If the probe ALSO fails, the failure is diagnosed as the
     network, not the feed(s) in question -- nothing gets pruned that cycle.
  4. PERIODIC RE-PROBE. Every REPROBE_INTERVAL_S, re-attempts every symbol
     ever passed to establish_coverage() (tracked in _original_candidates),
     not just what's currently in _known_good, and restores anything that
     resolves. Recovery happens automatically on the next few get_price()
     calls -- no process restart required for THIS path.
  5. EMPTY-COVERAGE TRIPWIRE. Zero coverage is not a valid steady state.
     A confirmed prune (probe passed, symbol still bad) that empties
     _known_good writes a loud rider_health row and calls sys.exit(1) --
     deliberately fatal, so the Windows service's RestartCount 999 recycles
     the process into a clean establish_coverage() rather than spinning
     forever at zero coverage. sys.exit() raises SystemExit, which is a
     BaseException, not an Exception -- it is not swallowed by any
     `except Exception` handler in divergence_recorder.py or rider_team.py's
     run loops, both of which have one.
  6. COVERAGE-SHRINK LOGGING. Every confirmed prune and every periodic
     recovery writes a rider_health row naming exactly which feeds dropped
     or returned -- not just a count.
================================================================================
"""

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import sys
import threading
import time
from dataclasses import dataclass

from web3 import Web3
from web3.exceptions import ContractLogicError

from supabase_client import record_health

FLARE_RPC = "https://flare-api.flare.network/ext/C/rpc"   # mainnet, deliberately hardcoded --
                                                            # this file IS the one place that's allowed to be
FLARE_REGISTRY = "0xaD67FE66660Fb8dFE9d6b1b4240d8650e30F6019"  # same address on every Flare network

CACHE_TTL_S = 2.0
BLIND_TRANSPORT_THRESHOLD = 3

LIVENESS_PROBE_SYMBOL = "FLR/USD"   # feed index 0 -- Flare's own token, the
                                    # closest thing this network has to a
                                    # feed that should never stop resolving

# 15 minutes: long enough that a process isn't constantly re-testing
# symbols it already confirmed are genuinely gone (each re-probe costs a
# real RPC round trip), short enough that a transient outage self-heals
# within the same working session rather than needing a human to notice
# and restart something. Not load-bearing precision -- a knob, not a
# threshold tied to any market behavior.
REPROBE_INTERVAL_S = 15 * 60

REGISTRY_ABI = [{
    "inputs": [{"internalType": "string", "name": "_name", "type": "string"}],
    "name": "getContractAddressByName",
    "outputs": [{"internalType": "address", "name": "", "type": "address"}],
    "stateMutability": "view", "type": "function",
}]

FTSOV2_ABI = [{
    "inputs": [{"internalType": "bytes21[]", "name": "_feedIds", "type": "bytes21[]"}],
    "name": "getFeedsById",
    "outputs": [
        {"internalType": "uint256[]", "name": "_values", "type": "uint256[]"},
        {"internalType": "int8[]", "name": "_decimals", "type": "int8[]"},
        {"internalType": "uint64", "name": "_timestamp", "type": "uint64"},
    ],
    "stateMutability": "payable", "type": "function",
}]

FEE_CALCULATOR_ABI = [{
    "inputs": [{"internalType": "bytes21[]", "name": "_feedIds", "type": "bytes21[]"}],
    "name": "calculateFeeByIds",
    "outputs": [{"internalType": "uint256", "name": "_fee", "type": "uint256"}],
    "stateMutability": "view", "type": "function",
}]


@dataclass
class FtsoPrice:
    price: float
    timestamp: int      # unix seconds, from the oracle itself
    feed_id: str         # hex string, e.g. '0x014254432f55534400000000000000000000000000'


_w3 = Web3(Web3.HTTPProvider(FLARE_RPC))
_registry = _w3.eth.contract(address=Web3.to_checksum_address(FLARE_REGISTRY), abi=REGISTRY_ABI)

_lock = threading.Lock()
_resolved_addrs = {}          # contract name -> checksummed address, resolved once, addresses don't change
_cache = {"ts": 0.0, "prices": {}}   # symbol -> FtsoPrice, refreshed as one batch every CACHE_TTL_S
_known_good = set()           # established by establish_coverage() -- get_price() only serves these
_original_candidates = set()  # every symbol ever passed to establish_coverage() -- what periodic
                              # re-probing tries to recover, a superset of _known_good that never shrinks
_consecutive_failures = 0
_last_reprobe_ts = 0.0


def _resolve(name):
    if name not in _resolved_addrs:
        addr = _registry.functions.getContractAddressByName(name).call()
        _resolved_addrs[name] = Web3.to_checksum_address(addr)
    return _resolved_addrs[name]


def _ftso_contract():
    return _w3.eth.contract(address=_resolve("FtsoV2"), abi=FTSOV2_ABI)


def _fee_calculator_contract():
    return _w3.eth.contract(address=_resolve("FeeCalculator"), abi=FEE_CALCULATOR_ABI)


def feed_id_bytes(symbol: str) -> bytes:
    """'BTC/USD' -> Flare 21-byte feed id. Category 1 = crypto."""
    name_bytes = symbol.encode("utf-8")
    return (1).to_bytes(1, "big") + name_bytes + b"\x00" * (20 - len(name_bytes))


def _call_batch(symbols):
    """One getFeedsById call for exactly these symbols. Returns
    {symbol: FtsoPrice} for whichever resolved. On a 'feed does not exist'
    revert, bisects and recurses to isolate the bad symbol(s) -- still only
    ever calls getFeedsById, including at the size-1 base case. Genuine
    transport/RPC errors (not reverts) propagate to the caller unchanged."""
    if not symbols:
        return {}
    ids = [feed_id_bytes(s) for s in symbols]
    try:
        fee = _fee_calculator_contract().functions.calculateFeeByIds(ids).call()
        values, decimals, ts = _ftso_contract().functions.getFeedsById(ids).call({"value": fee})
    except ContractLogicError:
        if len(symbols) == 1:
            return {}   # confirmed via bisection: this symbol has no feed
        mid = len(symbols) // 2
        left = _call_batch(symbols[:mid])
        right = _call_batch(symbols[mid:])
        return {**left, **right}

    result = {}
    for sym, value, dec in zip(symbols, values, decimals):
        if value == 0:
            continue
        result[sym] = FtsoPrice(price=value / (10 ** dec), timestamp=ts,
                                feed_id="0x" + feed_id_bytes(sym).hex())
    return result


def establish_coverage(candidate_symbols):
    """Task 2: test a candidate list against live mainnet FTSOv2 via
    bisection (never getFeedById). Updates the known-good set that
    get_price() serves from and returns {symbol: FtsoPrice} for whichever
    resolved -- callers get the coverage answer AND a fresh price in one
    pass, nothing wasted. Also records candidate_symbols into
    _original_candidates -- the full set periodic re-probing tries to
    recover, which _known_good alone can't represent once something's
    been pruned from it."""
    candidates = sorted(set(candidate_symbols))
    resolved = _call_batch(candidates)
    with _lock:
        _known_good.update(resolved.keys())
        _original_candidates.update(candidates)
        _cache["prices"].update(resolved)
        _cache["ts"] = time.time()
    return resolved


def _rpc_is_healthy():
    """Independent liveness probe, deliberately not gated on _known_good
    membership -- re-checks LIVENESS_PROBE_SYMBOL fresh every time it's
    called. If this fails, treat it as the network being unhealthy, not as
    evidence about whatever symbol prompted the check."""
    try:
        result = _call_batch([LIVENESS_PROBE_SYMBOL])
        return LIVENESS_PROBE_SYMBOL in result
    except Exception:
        return False


def _maybe_reprobe_full_universe():
    """Periodic recovery, independent of the hot path's per-call cache
    refresh: every REPROBE_INTERVAL_S, re-attempts every symbol that's
    EVER been in _original_candidates but is currently missing from
    _known_good, and restores anything that resolves. This is what makes
    recovery possible without a process restart -- establish_coverage() is
    otherwise only ever called once, at import time, by price_adapter.py.
    Acquires _lock in two short, separate sections (not one, and not
    nested inside get_price()'s own lock) so the network call itself
    doesn't hold the lock -- matches the existing acquire-then-network-call
    pattern elsewhere in this file rather than introducing a new one."""
    global _last_reprobe_ts
    now = time.time()
    with _lock:
        if now - _last_reprobe_ts < REPROBE_INTERVAL_S:
            return
        _last_reprobe_ts = now
        missing = sorted(_original_candidates - _known_good)
    if not missing:
        return
    try:
        recovered = _call_batch(missing)
    except Exception as e:
        print(f"[FLARE FTSO] periodic re-probe failed ({type(e).__name__}: {e}) "
              f"-- will retry in {REPROBE_INTERVAL_S}s")
        return
    if recovered:
        with _lock:
            _known_good.update(recovered.keys())
            _cache["prices"].update(recovered)
        print(f"[FLARE FTSO] periodic re-probe recovered: {sorted(recovered.keys())}")
        record_health("flare_ftso", "COVERAGE_RESTORED",
                      {"recovered": sorted(recovered.keys())}, 0)


def get_price(symbol: str):
    """'BTC/USD' -> FtsoPrice, or None. Only ever serves symbols already
    confirmed via establish_coverage() -- an untested symbol returns None
    immediately, it is never added to a live batch on the fly. NEVER falls
    back to a centralized venue; that decision belongs to the caller, not
    hidden in here."""
    global _consecutive_failures
    _maybe_reprobe_full_universe()   # outside the lock below -- see its own docstring
    with _lock:
        if symbol not in _known_good:
            return None
        stale = (time.time() - _cache["ts"]) > CACHE_TTL_S
        if not stale and symbol in _cache["prices"]:
            return _cache["prices"].get(symbol)

        tracked = sorted(_known_good)
        try:
            resolved = _call_batch(tracked)
            _consecutive_failures = 0
        except Exception as e:
            _consecutive_failures += 1
            print(f"[FLARE FTSO] batch refresh failed ({type(e).__name__}: {e}) "
                  f"-- consecutive={_consecutive_failures}")
            if _consecutive_failures == BLIND_TRANSPORT_THRESHOLD:
                record_health("flare_ftso", "BLIND_TRANSPORT",
                              {"error": str(e), "tracked_symbols": tracked},
                              _consecutive_failures)
            return _cache["prices"].get(symbol)  # serve stale rather than nothing, on transport failure

        vanished = _known_good - resolved.keys()
        if vanished:
            # LIVENESS PROBE BEFORE PRUNING: a batch that individually
            # confirms several symbols as "no feed" via bisection is
            # trustworthy only if the RPC/contract itself is actually
            # healthy right now. If the probe also fails, this is a
            # network issue masquerading as several dead feeds -- prune
            # nothing, let the next cycle try again.
            if _rpc_is_healthy():
                print(f"[FLARE FTSO] previously-good feed(s) stopped resolving, "
                      f"removed from tracked set: {sorted(vanished)}")
                _known_good.difference_update(vanished)
                record_health("flare_ftso", "COVERAGE_SHRUNK", {
                    "dropped": sorted(vanished),
                    "remaining": sorted(_known_good),
                }, 0)

                if not _known_good:
                    # EMPTY-COVERAGE TRIPWIRE: zero coverage is not a
                    # valid steady state -- this is what silently starved
                    # divergence_recorder.py for ~45 hours. sys.exit()
                    # raises SystemExit (a BaseException, not an
                    # Exception), so it is NOT caught by the
                    # `except Exception` handlers in divergence_recorder.py
                    # or rider_team.py's run loops -- the process actually
                    # dies, and the Windows service's RestartCount 999
                    # brings it back with a fresh establish_coverage().
                    record_health("flare_ftso", "EMPTY_COVERAGE_FATAL", {
                        "dropped": sorted(vanished),
                        "original_candidates": sorted(_original_candidates),
                    }, 0)
                    print("[FLARE FTSO] FATAL: coverage set is now empty. "
                          "Exiting so the service restarts into a clean "
                          "establish_coverage() rather than running blind.")
                    sys.exit(1)
            else:
                print(f"[FLARE FTSO] {len(vanished)} feed(s) appeared to fail "
                      f"({sorted(vanished)}), but the liveness probe "
                      f"({LIVENESS_PROBE_SYMBOL}) also failed -- treating as a "
                      f"network issue, not pruning anything this cycle.")

        _cache["prices"] = resolved
        _cache["ts"] = time.time()
        return resolved.get(symbol)


if __name__ == "__main__":
    test_candidates = ["BTC/USD", "ETH/USD", "FLR/USD", "SOL/USD", "NOTASYMBOL/USD"]
    resolved = establish_coverage(test_candidates)
    print(f"known-good: {sorted(_known_good)}")
    for sym in test_candidates:
        print(sym, "->", get_price(sym))
