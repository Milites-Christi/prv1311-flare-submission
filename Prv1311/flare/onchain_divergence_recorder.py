"""
================================================================================
flare/onchain_divergence_recorder.py — continuous CoinGecko-vs-on-chain
divergence logger, FLR and FXRP (Flare hackathon, Task 2 follow-on)
================================================================================
Same operational shape as flare/divergence_recorder.py (FTSO vs. Coinbase):
rotating log, STARTUP health row, per-cycle health row with rows_written,
BLIND_TRANSPORT threshold, Ctrl+C-safe loop so it can run standalone or under
Task Scheduler. New inputs, not a new pattern -- see that file and
flare/onchain_divergence.py's own docstring for what's actually different
(CoinGecko off-chain price vs. a real on-chain swap price, not FTSO vs
Coinbase).

REQUIRES THE onchain_divergence TABLE TO EXIST FIRST. This process has no
DDL access (confirmed 2026-08-11 -- no exec_sql RPC, no Postgres connection
string in .env) and cannot create it. Run this once, in the Supabase SQL
editor, before starting the recorder:

    create table public.onchain_divergence (
      id bigint generated always as identity primary key,
      ts timestamptz not null default now(),
      symbol text not null,
      pool_address text not null,
      pool_pair text not null,
      offchain_source text not null default 'coingecko',
      offchain_value double precision,
      onchain_value double precision,
      onchain_swap_timestamp int8,
      timestamp_gap_ms int8,
      divergence_bps double precision
    );
    alter table public.onchain_divergence enable row level security;
    create policy "public read onchain_divergence"
      on public.onchain_divergence for select to public using (true);

READ-ONLY, MEASUREMENT-ONLY -- see flare/onchain_divergence.py's docstring
for the explicit isolation statement. This recorder writes to its own new
table and nothing else; it is not read by rider_team.py or rider_flare.py.
================================================================================
"""

import os
import sys
import time
import logging
from logging.handlers import RotatingFileHandler

from flare.onchain_divergence import measure_divergence
from flare.onchain_swaps import POOLS
from supabase_client import get_client, record_health

CYCLE_SEC = 300   # 5 min -- FLR/FXRP swap far less often than a 60s FTSO
                  # cadence needs; matching divergence_recorder.py's 60s here
                  # would just be 5x the CoinGecko call volume for no more
                  # signal (the on-chain side already looks back 6h per call).
BLIND_TRANSPORT_THRESHOLD = 3
ZERO_ROW_FATAL_THRESHOLD = 5

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
LOG_FILE = os.path.join(LOG_DIR, "onchain_divergence_recorder.log")

_consecutive_failures = 0
_consecutive_zero_rows = 0


def _install_rotating_log():
    os.makedirs(LOG_DIR, exist_ok=True)
    logger = logging.getLogger("onchain_divergence_recorder")
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


def _record_cycle():
    global _consecutive_failures, _consecutive_zero_rows
    try:
        rows = measure_divergence()
    except Exception as e:
        _consecutive_failures += 1
        print(f"[ONCHAIN DIVERGENCE RECORDER] measure_divergence failed ({e}) -- "
              f"consecutive={_consecutive_failures}")
        record_health("onchain_divergence_recorder", "CYCLE", {
            "rows_written": 0, "reason": "measure_failed", "error": str(e),
        }, _consecutive_failures)
        if _consecutive_failures == BLIND_TRANSPORT_THRESHOLD:
            record_health("onchain_divergence_recorder", "BLIND_TRANSPORT",
                          {"error": str(e)}, _consecutive_failures)
        return

    if not rows:
        _consecutive_zero_rows += 1
        print(f"[ONCHAIN DIVERGENCE RECORDER] no rows resolved this cycle "
              f"consecutive_zero={_consecutive_zero_rows}")
        record_health("onchain_divergence_recorder", "CYCLE", {
            "rows_written": 0, "consecutive_zero_rows": _consecutive_zero_rows,
        }, _consecutive_zero_rows)
        if _consecutive_zero_rows >= ZERO_ROW_FATAL_THRESHOLD:
            record_health("onchain_divergence_recorder", "ZERO_ROWS_FATAL", {
                "consecutive_zero_rows": _consecutive_zero_rows,
            }, _consecutive_zero_rows)
            print(f"[ONCHAIN DIVERGENCE RECORDER] FATAL: {_consecutive_zero_rows} "
                  f"consecutive zero-row cycles -- exiting so the service restarts.")
            sys.exit(1)
        return

    _consecutive_zero_rows = 0
    now_ms = time.time() * 1000.0
    payload = [{
        "symbol": r.symbol,
        "pool_address": r.pool_address,
        "pool_pair": r.pool_pair,
        "offchain_source": "coingecko",
        "offchain_value": r.offchain_price,
        "onchain_value": r.onchain_price,
        "onchain_swap_timestamp": r.onchain_swap_ts,
        "timestamp_gap_ms": int(abs(now_ms - r.onchain_swap_ts * 1000)),
        "divergence_bps": r.spread_bps,
    } for r in rows]

    try:
        get_client().table("onchain_divergence").insert(payload).execute()
        _consecutive_failures = 0
        print(f"[{time.strftime('%H:%M:%S')}] recorded {len(rows)}/{len(POOLS)} symbols: "
              + ", ".join(f"{r.symbol}={r.spread_bps:+.1f}bps" for r in rows))
        record_health("onchain_divergence_recorder", "CYCLE", {
            "rows_written": len(rows), "universe_size": len(POOLS),
        }, 0)
    except Exception as e:
        _consecutive_failures += 1
        print(f"[ONCHAIN DIVERGENCE RECORDER] insert failed ({e}) -- "
              f"consecutive={_consecutive_failures}")
        record_health("onchain_divergence_recorder", "CYCLE", {
            "rows_written": 0, "reason": "insert_failed", "error": str(e),
        }, _consecutive_failures)
        if _consecutive_failures == BLIND_TRANSPORT_THRESHOLD:
            record_health("onchain_divergence_recorder", "BLIND_TRANSPORT",
                          {"error": str(e)}, _consecutive_failures)


def run_recorder():
    _install_rotating_log()

    env_loaded = bool(os.getenv("SUPABASE_URL")) and bool(os.getenv("SUPABASE_KEY"))
    record_health("onchain_divergence_recorder", "STARTUP", {
        "env_loaded": env_loaded,
        "symbols": list(POOLS.keys()),
        "cycle_sec": CYCLE_SEC,
    }, 0)

    print("=" * 78)
    print("      FLARE — ON-CHAIN DIVERGENCE RECORDER (continuous)")
    print("=" * 78)
    print(f"Symbols  : {list(POOLS.keys())}")
    print(f"Cadence  : every {CYCLE_SEC}s")
    print(f"Status   : LIVE (Ctrl+C to stop)\n")

    while True:
        try:
            _record_cycle()
            time.sleep(CYCLE_SEC)
        except KeyboardInterrupt:
            print("\n[Onchain Divergence Recorder] stopped safely.")
            break
        except Exception as e:
            print(f"\n[Onchain Divergence Recorder Error] {e}")
            time.sleep(CYCLE_SEC)


if __name__ == "__main__":
    run_recorder()
