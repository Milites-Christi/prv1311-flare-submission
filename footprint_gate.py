"""
footprint_gate.py — Order-flow VETO gate for the PRV1311 engine.

Per-asset only. Judges the asset being traded on ITS OWN order flow —
no BTC/ETH overlay. Desk logic: read the flow of the thing you're
actually buying.

    from footprint_gate import check_flow
    decision = check_flow("EUL-USD")
    if decision.veto:
        # stay in cash — do NOT deploy this tranche
        ...

SHIELD, never SPEAR: can VETO an entry, never triggers one.
FAIL SAFE: any missing data / thin book / error => veto = True.
"""

from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field

from supabase_client import get_client, record_health

_client = get_client()

# --------------------------------------------------------------------------- #
# Tunable thresholds
# --------------------------------------------------------------------------- #
LOOKBACK_NODES = 3          # how many recent 1h nodes to evaluate
TIMEFRAME_S = 3600          # must match the worker's --timeframe
MIN_TRADES_WINDOW = 150     # total trades across the 3-node window below this
                            #   = dead book -> veto. Cumulative, NOT per-node, so a
                            #   single quiet hour on a thin name (EUL/COTI/KAITO)
                            #   won't perma-lock it.
TOXIC_DELTA_RATIO = -0.15   # window net delta below this fraction of volume -> veto
MAX_STALE_S = TIMEFRAME_S      # newest node's CLOSE older than one timeframe
                              #   (1h) => feed stale => veto


@dataclass
class FlowDecision:
    veto: bool
    reason: str
    asset: str
    detail: dict = field(default_factory=dict)
    verdict: str = "CLEAR"   # CLEAR | VETO | BLIND_NO_DATA | BLIND_TRANSPORT

    def __bool__(self):
        return not self.veto


# Counts consecutive BLIND_TRANSPORT results only. A query that succeeds --
# CLEAR, VETO, or BLIND_NO_DATA alike -- is a heartbeat and resets this to 0.
# Uncovered assets (no footprint rows yet) return BLIND_NO_DATA every cycle on
# a perfectly healthy pipeline; counting those would false-alarm constantly.
_consecutive_blind_transport = 0


def _blind_transport(asset_product_id, reason, detail):
    global _consecutive_blind_transport
    _consecutive_blind_transport += 1
    print("=" * 70)
    print(f"[FOOTPRINT BLIND_TRANSPORT] {asset_product_id} -- {reason} -- "
          f"flow gate unreachable, failing safe to veto "
          f"(consecutive: {_consecutive_blind_transport})")
    print("=" * 70)
    if _consecutive_blind_transport == 3:
        record_health("footprint_gate", "BLIND_TRANSPORT",
                       {"asset": asset_product_id, "reason": reason, **detail},
                       _consecutive_blind_transport)
    return FlowDecision(True, reason, asset_product_id, detail,
                        verdict="BLIND_TRANSPORT")


def _reset_blind_transport():
    global _consecutive_blind_transport
    _consecutive_blind_transport = 0


# --------------------------------------------------------------------------- #
def _fetch_nodes(product_id: str, limit: int):
    """Newest `limit` nodes for a product at the gate timeframe, newest first."""
    res = (
        _client.table("footprint_nodes")
        .select("bucket_start,net_delta,cumulative_delta,"
                "buy_volume,sell_volume,trade_count")
        .eq("product_id", product_id)
        .eq("timeframe_s", TIMEFRAME_S)
        .order("bucket_start", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data or []


def _now_utc():
    return datetime.now(timezone.utc)


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _window_delta_ratio(nodes) -> float:
    total_vol = sum((n["buy_volume"] + n["sell_volume"]) for n in nodes)
    if total_vol <= 0:
        return -1.0
    net = sum(n["net_delta"] for n in nodes)
    return net / total_vol


# --------------------------------------------------------------------------- #
def check_flow(asset_product_id: str) -> FlowDecision:
    """Veto/clear for deploying into this asset, based ONLY on its own flow.
    Any transport error => BLIND_TRANSPORT (veto=True). Zero rows for this
    asset => BLIND_NO_DATA (veto=True, expected/normal for uncovered symbols).
    A successful query whose data says the tape is bad => VETO. Clean tape
    => CLEAR. All four still fail safe to veto except CLEAR."""
    try:
        nodes = _fetch_nodes(asset_product_id, LOOKBACK_NODES)
        _reset_blind_transport()  # a response came back -- heartbeat

        # 0. Any coverage at all?
        if len(nodes) == 0:
            return FlowDecision(True, "no_coverage", asset_product_id,
                                {"have": 0, "need": LOOKBACK_NODES},
                                verdict="BLIND_NO_DATA")

        # 1. Enough history?
        if len(nodes) < LOOKBACK_NODES:
            return FlowDecision(
                True, "insufficient_history", asset_product_id,
                {"have": len(nodes), "need": LOOKBACK_NODES}, verdict="VETO")

        # 2. Staleness — measured from the node's CLOSE, not its start.
        newest_close = _parse_iso(nodes[0]["bucket_start"]) \
            + timedelta(seconds=TIMEFRAME_S)
        age = (_now_utc() - newest_close).total_seconds()
        if age > MAX_STALE_S:
            return FlowDecision(True, "feed_stale", asset_product_id,
                                {"age_since_close_s": int(age), "max_s": MAX_STALE_S},
                                verdict="VETO")

        # 3. Dead book — CUMULATIVE trade count across the window.
        total_trades = sum(n["trade_count"] for n in nodes)
        if total_trades < MIN_TRADES_WINDOW:
            return FlowDecision(
                True, "dead_book", asset_product_id,
                {"min_window": MIN_TRADES_WINDOW,
                 "total_trades": total_trades,
                 "per_node": [n["trade_count"] for n in nodes]}, verdict="VETO")

        # 4. Toxic own-flow — sellers dominated without absorption.
        ratio = _window_delta_ratio(nodes)
        if ratio < TOXIC_DELTA_RATIO:
            return FlowDecision(
                True, "toxic_asset_flow", asset_product_id,
                {"delta_ratio": round(ratio, 4),
                 "threshold": TOXIC_DELTA_RATIO}, verdict="VETO")

        # Clean.
        return FlowDecision(
            False, "clean", asset_product_id,
            {"delta_ratio": round(ratio, 4),
             "trades": [n["trade_count"] for n in nodes]}, verdict="CLEAR")

    except Exception as e:  # noqa: BLE001
        return _blind_transport(asset_product_id, f"error:{type(e).__name__}",
                                {"msg": str(e)})


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    targets = sys.argv[1:] or ["BTC-USD"]
    for t in targets:
        d = check_flow(t)
        state = "VETO — stay in cash" if d.veto else "CLEAR — engine may enter"
        print(f"\n{t}: {state}")
        print(f"  reason: {d.reason}")
        print(f"  detail: {d.detail}")