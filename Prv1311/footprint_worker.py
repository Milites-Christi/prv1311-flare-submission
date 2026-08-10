"""
footprint_worker.py — Coinbase Advanced Trade order-flow telemetry worker.

Standalone VS Code-side ingestion process. Listens to the Coinbase
`market_trades` websocket, aggregates aggressive buy/sell volume into
WALL-CLOCK-ALIGNED footprint nodes (e.g. every candle snaps to :00/:15/:30),
computes Net Delta and session CVD, and writes each closed node to Supabase.

Runs completely independently of the main execution engine. The engine only
ever READS the resulting rows (veto-only absorption gate) — this worker never
touches order execution.

Design decisions (per audit):
  - Wall-clock aligned buckets, closed by an INDEPENDENT timer loop, NOT by
    tick arrival. An idle asset still closes its node on schedule (empty node).
  - Skips the initial `snapshot` batch; only `update` events count as live flow.
  - Deduplicates by trade_id so reconnect replays never double-count.
  - CVD persists correctly across reconnects; it is NOT re-polluted by snapshots.
  - Resilient reconnect with exponential backoff + jitter.

Env required:
  SUPABASE_URL, SUPABASE_KEY   (service role or anon with insert rights)

Run:
  python footprint_worker.py                # defaults to BTC-USD, 60s nodes
  python footprint_worker.py BTC-USD ETH-USD SOL-USD --timeframe 3600
"""

import asyncio
import json
import logging
import os
import random
import sys
import time
import argparse
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

import websockets

try:
    from supabase_client import get_client, record_health
    _client = get_client()
except Exception as e:  # noqa: BLE001
    _client = None
    record_health = None
    print(f"[!] supabase_client unavailable ({e}) -- nodes will print only.")

# Single source of truth for node granularity: footprint_gate.py only ever
# queries rows at this timeframe_s. Importing it (rather than hardcoding 3600
# here too) means the two files can never drift out of sync again.
from footprint_gate import TIMEFRAME_S as GATE_TIMEFRAME_S

COINBASE_WS_URL = "wss://advanced-trade-ws.coinbase.com"
SUPABASE_TABLE = "footprint_nodes"

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_FILE = os.path.join(LOG_DIR, "footprint_worker.log")

HEALTH_OK_INTERVAL_S = 15 * 60  # throttle OK health rows to at most once per 15 min


def _install_rotating_log():
    """Tee stdout/stderr into a rotating file, in addition to the console.
    Under Task Scheduler there's no console to watch; interactive runs keep
    seeing live output exactly as before."""
    os.makedirs(LOG_DIR, exist_ok=True)
    logger = logging.getLogger("footprint_worker")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024,
                                  backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)

    class _Tee:
        def __init__(self, original, level):
            self.original = original
            self.level = level
            self._buf = ""

        def write(self, msg):
            if self.original:
                try:
                    self.original.write(msg)
                except Exception:
                    pass
            self._buf += msg
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                if line.strip():
                    logger.log(self.level, line)

        def flush(self):
            if self.original:
                try:
                    self.original.flush()
                except Exception:
                    pass

    sys.stdout = _Tee(sys.stdout, logging.INFO)
    sys.stderr = _Tee(sys.stderr, logging.ERROR)


# --------------------------------------------------------------------------- #
# Wall-clock bucket helpers
# --------------------------------------------------------------------------- #
def bucket_start(ts: float, timeframe: int) -> float:
    """Floor a unix timestamp to the wall-clock grid for this timeframe.

    timeframe=60   -> snaps to each minute  (:00 seconds)
    timeframe=900  -> snaps to :00/:15/:30/:45
    timeframe=3600 -> snaps to the top of each hour
    Grid is anchored to the unix epoch, which is aligned to real UTC clock
    boundaries, so every asset with the same timeframe shares identical edges.
    """
    return (int(ts) // timeframe) * timeframe


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Per-product footprint state
# --------------------------------------------------------------------------- #
class Footprint:
    """Holds the in-progress node for one product plus its session CVD."""

    def __init__(self, product_id: str, timeframe: int):
        self.product_id = product_id
        self.timeframe = timeframe
        self.current_bucket = None          # wall-clock start of open node
        self.buy_vol = 0.0
        self.sell_vol = 0.0
        self.delta = 0.0
        self.trade_count = 0
        self.cumulative_delta = 0.0         # session CVD, survives reconnects
        self.seen_trade_ids = set()         # dedup within the open node
        self._lock = asyncio.Lock()

    async def add_trade(self, trade_id: str, qty: float, side: str, ts: float):
        """Fold one live trade into the correct wall-clock node.

        If the trade belongs to a newer bucket than the one currently open,
        the open node is closed first (handles the case where trades jump
        across a boundary). Trades for an already-closed bucket are dropped.
        """
        async with self._lock:
            b = bucket_start(ts, self.timeframe)

            if self.current_bucket is None:
                self.current_bucket = b

            # Trade lands in a future bucket -> close everything up to it.
            if b > self.current_bucket:
                await self._roll_to(b)

            # Late trade for a bucket we already closed -> ignore.
            if b < self.current_bucket:
                return

            if trade_id in self.seen_trade_ids:
                return  # dedup: reconnect replay
            self.seen_trade_ids.add(trade_id)

            if side == "BUY":
                self.buy_vol += qty
                self.delta += qty
            elif side == "SELL":
                self.sell_vol += qty
                self.delta -= qty
            self.trade_count += 1

    async def tick_clock(self, now: float, on_close):
        """Called by the timer loop. Closes the node once wall-clock passes
        its boundary, even if no trades arrived (emits an empty node)."""
        async with self._lock:
            if self.current_bucket is None:
                # No trades ever seen; align to the current grid so idle
                # assets still emit empty nodes on schedule.
                self.current_bucket = bucket_start(now, self.timeframe)
                return None
            if now >= self.current_bucket + self.timeframe:
                node = self._snapshot_and_reset(
                    next_bucket=bucket_start(now, self.timeframe)
                )
                await on_close(node)
                return node
        return None

    async def _roll_to(self, new_bucket: float):
        """Close the open node and advance. Caller holds the lock."""
        node = self._snapshot_and_reset(next_bucket=new_bucket)
        if self._on_close_ref:
            await self._on_close_ref(node)

    _on_close_ref = None  # set by worker so _roll_to can flush

    def _snapshot_and_reset(self, next_bucket: float) -> dict:
        self.cumulative_delta += self.delta
        node = {
            "product_id": self.product_id,
            "bucket_start": iso(self.current_bucket),
            "bucket_end": iso(self.current_bucket + self.timeframe),
            "timeframe_s": self.timeframe,
            "buy_volume": round(self.buy_vol, 8),
            "sell_volume": round(self.sell_vol, 8),
            "net_delta": round(self.delta, 8),
            "cumulative_delta": round(self.cumulative_delta, 8),
            "trade_count": self.trade_count,
        }
        # reset for next node
        self.current_bucket = next_bucket
        self.buy_vol = 0.0
        self.sell_vol = 0.0
        self.delta = 0.0
        self.trade_count = 0
        self.seen_trade_ids.clear()
        return node


# --------------------------------------------------------------------------- #
# Supabase sink
# --------------------------------------------------------------------------- #
class Sink:
    def __init__(self):
        self.client = _client
        self._last_ok_health = 0.0
        if self.client:
            print("[*] Supabase sink connected.")
        else:
            print("[!] supabase package missing — nodes will print only.")

    async def write(self, node: dict):
        # Always log so the worker is observable even without a DB.
        print(f"[NODE] {node['product_id']} "
              f"{node['bucket_start']} d={node['net_delta']:+.4f} "
              f"cvd={node['cumulative_delta']:+.4f} n={node['trade_count']}")
        if not self.client:
            return
        try:
            # supabase-py is sync; run in a thread so we never block the loop.
            await asyncio.to_thread(
                lambda: self.client.table(SUPABASE_TABLE).insert(node).execute()
            )
        except Exception as e:  # noqa: BLE001
            print(f"[!] Supabase insert failed ({node['product_id']}): {e}")
            return

        if record_health:
            now = time.time()
            if now - self._last_ok_health >= HEALTH_OK_INTERVAL_S:
                self._last_ok_health = now
                record_health("footprint_worker", "OK",
                              {"last_node": node["product_id"],
                               "bucket_end": node["bucket_end"]}, 0)


# --------------------------------------------------------------------------- #
# Worker
# --------------------------------------------------------------------------- #
class FootprintWorker:
    def __init__(self, product_ids, timeframe: int):
        self.product_ids = product_ids
        self.timeframe = timeframe
        self.sink = Sink()
        self.books = {p: Footprint(p, timeframe) for p in product_ids}
        for fp in self.books.values():
            fp._on_close_ref = self.sink.write
        self._consecutive_reconnects = 0

    async def run(self):
        await asyncio.gather(
            self._stream_forever(),
            self._clock_loop(),
        )

    async def _clock_loop(self):
        """Independent wall-clock timer. Closes nodes on schedule regardless
        of whether any trades arrived — this is the fix for time-dilation."""
        while True:
            now = time.time()
            for fp in self.books.values():
                await fp.tick_clock(now, self.sink.write)
            # Sleep to the next 1s boundary; cheap and precise enough.
            await asyncio.sleep(1.0)

    async def _stream_forever(self):
        """Connect + resubscribe with exponential backoff and jitter."""
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(
                    COINBASE_WS_URL, ping_interval=20, ping_timeout=20
                ) as ws:
                    await ws.send(json.dumps({
                        "type": "subscribe",
                        "product_ids": self.product_ids,
                        "channel": "market_trades",
                    }))
                    print(f"[*] Subscribed: {', '.join(self.product_ids)} "
                          f"| {self.timeframe}s wall-clock nodes")
                    backoff = 1.0  # reset after a clean connect
                    self._consecutive_reconnects = 0
                    await self._consume(ws)
            except Exception as e:  # noqa: BLE001
                self._consecutive_reconnects += 1
                sleep = backoff + random.uniform(0, backoff)
                print(f"[!] WS dropped: {e} — reconnecting in {sleep:.1f}s")
                if record_health:
                    record_health("footprint_worker", "RECONNECT",
                                  {"error": str(e), "sleep_s": round(sleep, 1)},
                                  self._consecutive_reconnects)
                await asyncio.sleep(sleep)
                backoff = min(backoff * 2, 30.0)

    async def _consume(self, ws):
        async for message in ws:
            data = json.loads(message)
            if data.get("channel") != "market_trades":
                continue
            for event in data.get("events", []):
                # SKIP the snapshot batch — only live updates are real flow.
                if event.get("type") == "snapshot":
                    continue
                for trade in event.get("trades", []):
                    fp = self.books.get(trade.get("product_id"))
                    if fp is None:
                        continue
                    ts = _parse_ts(trade.get("time"))
                    await fp.add_trade(
                        trade_id=str(trade.get("trade_id")),
                        qty=float(trade["size"]),
                        side=trade["side"],
                        ts=ts,
                    )


def _parse_ts(t) -> float:
    """Coinbase sends ISO-8601 trade times; fall back to local clock."""
    if not t:
        return time.time()
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return time.time()


# --------------------------------------------------------------------------- #
def main():
    _install_rotating_log()

    ap = argparse.ArgumentParser(description="Coinbase footprint/delta worker")
    ap.add_argument("products", nargs="*", default=["BTC-USD"],
                    help="Coinbase product IDs, e.g. BTC-USD ETH-USD")
    ap.add_argument("--timeframe", type=int, default=GATE_TIMEFRAME_S,
                    help=f"Seconds per wall-clock node. Must match "
                         f"footprint_gate.TIMEFRAME_S ({GATE_TIMEFRAME_S}) or the "
                         f"flow gate will never see these nodes.")
    args = ap.parse_args()
    products = args.products or ["BTC-USD"]

    env_loaded = bool(os.getenv("SUPABASE_URL")) and bool(os.getenv("SUPABASE_KEY"))
    timeframe_ok = (args.timeframe == GATE_TIMEFRAME_S)

    if record_health:
        record_health("footprint_worker",
                      "STARTUP" if timeframe_ok else "FATAL_CONFIG",
                      {"products": products, "timeframe": args.timeframe,
                       "gate_timeframe": GATE_TIMEFRAME_S, "env_loaded": env_loaded},
                      0)

    if not timeframe_ok:
        raise SystemExit(
            f"[FATAL] --timeframe {args.timeframe} != footprint_gate.TIMEFRAME_S "
            f"({GATE_TIMEFRAME_S}). The gate only ever queries timeframe_s="
            f"{GATE_TIMEFRAME_S} rows -- running at a different granularity "
            f"produces nodes the gate can never see. Refusing to start."
        )

    # Supervisor loop: ANY crash (unhandled error, event-loop death) relaunches
    # the whole worker. Only Ctrl+C stops it. This is what keeps the feed alive
    # overnight — the gate treats a stale feed as a veto, so the feed must never
    # silently die.
    while True:
        worker = FootprintWorker(products, args.timeframe)
        try:
            asyncio.run(worker.run())
        except KeyboardInterrupt:
            print("\n[*] Worker terminated by user.")
            break
        except Exception as e:  # noqa: BLE001
            print(f"\n[!!!] Worker crashed: {type(e).__name__}: {e}")
            print("[*] Relaunching in 5s...")
            time.sleep(5)


if __name__ == "__main__":
    main()
