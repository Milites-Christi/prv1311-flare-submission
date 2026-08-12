"""
================================================================================
flare/decision_hash.py — canonical serialization + fetch for a single
rider_decisions row, so its keccak256 commitment can be recomputed later
================================================================================
The whole point of decisionHash in DivergenceAnchor.recordDivergence() is that
a judge (or anyone) can independently fetch the exact rider_decisions row a
call anchors and recompute the identical hash -- proving the decision context
wasn't revised after the on-chain commitment. That only holds if serialization
is byte-identical every time the same row is serialized, no matter when, where,
or how many times it's re-fetched. Three things threaten that, each handled
below:

  1. Dict key order isn't guaranteed to match table column order, insertion
     order, or anything stable across clients/languages a verifier might use.
     FIELD_ORDER pins it explicitly, once, as the single source of truth for
     both the writer (flare/deploy_anchor.py) and any future verifier/test.
     Pulled from the live schema via PostgREST's OpenAPI endpoint on
     2026-08-10 (GET {SUPABASE_URL}/rest/v1/ with Accept: application/
     openapi+json) -- not guessed, not copied from memory.

  2. json.dumps' float serialization calls float.__repr__, which is the
     shortest string that round-trips to the same float -- correct and stable
     *within* CPython, but not something to lean on when a verifier might be
     a different Python version, a different language, or hand-recomputing
     this by eye. FLOAT_PRECISION fixes every float field to a fixed decimal-
     place STRING (not a JSON number) so the byte output never depends on
     repr internals. 10 decimal places is chosen because: the smallest price
     ticks seen in this schema (sub-cent altcoins, e.g. 0.00335) only need
     ~5-6 decimals of real precision, so 10 leaves comfortable headroom
     without pushing into the noisy tail of a double's ~15-17 significant-
     digit budget even for BTC-sized prices (6 integer digits + 10 fraction
     digits = 16 significant digits, still within budget). Because the same
     stored double is fetched -- never recomputed -- on both sides of a
     verification, this formatting is byte-identical every time regardless
     of which precision was chosen; 10 was picked for headroom, not because
     fewer digits would break determinism.

  3. A field that's None (an unreached gate -- e.g. regime_ok when an earlier
     gate already blocked the candidate) must serialize as JSON null, not get
     dropped from the object (which would silently change the hash of every
     row with fewer populated gates than another) and not become the string
     "None"/"null" (which would make a real None indistinguishable from a
     genuine null-ish text value in a text column).
================================================================================
"""

import json

from supabase_client import get_client

# Exact column order for rider_decisions, confirmed live via PostgREST's
# OpenAPI schema endpoint on 2026-08-10 -- not guessed. See module docstring
# point 1. Any future schema change must update this list explicitly; it is
# never inferred from dict order at call time.
FIELD_ORDER = [
    "id",
    "ts",
    "cycle_id",
    "symbol",
    "source",
    "price",
    "rolling_7d_high",
    "pullback_pct",
    "floor_value",
    "floor_buffer_ok",
    "candle_count",
    "maturity_ok",
    "volume_24h",
    "liquidity_ok",
    "regime_label",
    "regime_ok",
    "obi_value",
    "obi_ok",
    "flow_verdict",
    "flow_reason",
    "fired",
    "block_reason",
    "fleet",
    "limit_price",
    "polled_price",
    "pct_below_high",
    "user_id",
]

# double precision columns in rider_decisions -- everything else (text,
# uuid, bool, int) is emitted as its native JSON type, unchanged.
FLOAT_FIELDS = {
    "price",
    "rolling_7d_high",
    "pullback_pct",
    "floor_value",
    "volume_24h",
    "obi_value",
    "limit_price",
    "polled_price",
    "pct_below_high",
}

FLOAT_PRECISION = 10  # decimal places -- see module docstring point 2


def canonicalize_decision_row(row: dict) -> bytes:
    """Deterministic byte serialization of one rider_decisions row.

    Same row in -> same bytes out, always -- that's the entire contract.
    Fixed field order (FIELD_ORDER), fixed-precision string formatting for
    every float field, None preserved as JSON null, compact separators so
    no whitespace choice can vary the output. Pass the result straight to
    Web3.keccak() to get the on-chain decisionHash.
    """
    missing = [f for f in FIELD_ORDER if f not in row]
    if missing:
        raise ValueError(f"row is missing expected rider_decisions fields: {missing}")

    ordered = {}
    for field in FIELD_ORDER:
        value = row[field]
        if value is None:
            ordered[field] = None
        elif field in FLOAT_FIELDS:
            ordered[field] = format(float(value), f".{FLOAT_PRECISION}f")
        else:
            ordered[field] = value

    payload = json.dumps(ordered, sort_keys=False, separators=(",", ":"))
    return payload.encode("utf-8")


def fetch_latest_decision_row(symbol: str, fleet: str) -> dict:
    """Most recent rider_decisions row for this exact symbol + fleet pair,
    by ts descending. Raises if none exist -- never silently substitutes a
    different symbol or fleet."""
    client = get_client()
    res = (
        client.table("rider_decisions")
        .select("*")
        .eq("symbol", symbol)
        .eq("fleet", fleet)
        .order("ts", desc=True)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise ValueError(
            f"no rider_decisions rows found for symbol={symbol!r} fleet={fleet!r}"
        )
    return res.data[0]
