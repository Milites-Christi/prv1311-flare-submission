"""
================================================================================
flare/divergence_recorder.py — continuous FTSO-vs-Coinbase divergence logger
(Flare hackathon, Day 2, Task 1)
================================================================================
Records one row per A/B symbol per cycle to Supabase `oracle_divergence` --
this IS the week's dataset, not a debug tool. The twin ledger (rider_flare)
and this recorder both need wall-clock time to accumulate; the on-chain
contract read doesn't, so both collectors go live today and the contract is
Day 3 -- every hour this isn't running is comparison data that can't be
recovered later.

Cost: two Coinbase calls per minute total -- one exchange.fetch_tickers()
bulk call for all 16 A/B symbols' venue side (Day 2 change #2: 16x less
traffic than per-symbol fetch_ticker, and it tightens timestamp_gap_ms
because one venue snapshot lines up against one oracle batch), plus whatever
ftso.py's own ~2s cache costs get_price() on the oracle side.

Same operational shape as footprint_worker.py / rider_team.py: rotating log,
STARTUP health row, 3-consecutive-failure threshold, Ctrl+C-safe so it can
run standalone or under Task Scheduler.

REAL BUG, FOUND LIVE (Day 5+): this ran for ~45 hours and wrote zero rows,
with no complaint anywhere. A cycle that resolved zero rows (oracle side
had no coverage) returned silently -- "no rows resolved this cycle" only
ever went to a log file nobody was watching. Fixed with three additions,
all against the rule this project runs on: a degraded state that isn't
reported is the same as a bug that isn't fixed.
  1. A zero-row cycle now writes a rider_health row naming WHY -- distinct
     between the venue call failing outright, ftso.py returning nothing
     for every symbol (empty coverage), Coinbase returning nothing for
     every symbol, or a partial mismatch with no overlap between the two.
  2. Every cycle -- not just STARTUP, not just failures -- writes a
     rider_health row with rows_written as an explicit field, so a human
     or a dashboard can tell "silently producing nothing" apart from
     "hasn't run yet" without reading the log file.
  3. ZERO_ROW_FATAL_THRESHOLD consecutive zero-row cycles calls sys.exit(1)
     -- deliberately fatal, same mechanism as flare/ftso.py's own
     empty-coverage tripwire, so the Windows service's RestartCount 999
     recycles the process instead of running blind indefinitely.
================================================================================
"""

import os
import sys
import time
import logging
from logging.handlers import RotatingFileHandler

from screener import exchange
from flare.ftso import get_price
from flare.price_adapter import FLARE_UNIVERSE
from supabase_client import get_client, record_health

CYCLE_SEC = 60
BLIND_TRANSPORT_THRESHOLD = 3

# At CYCLE_SEC=60s, 5 consecutive zero-row cycles is 5 minutes of confirmed
# dead data collection -- long enough that one transient blip (a slow RPC
# round trip, one bad tick) doesn't trigger a restart over nothing, short
# enough that the failure this is fixing (~45 HOURS of silent zero-row
# cycles) becomes a few minutes of downtime instead of days of missing data.
ZERO_ROW_FATAL_THRESHOLD = 5

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
LOG_FILE = os.path.join(LOG_DIR, "divergence_recorder.log")

_consecutive_failures = 0
_consecutive_zero_rows = 0


def _install_rotating_log():
    """Tee stdout/stderr into a rotating file, in addition to the console --
    same pattern as footprint_worker.py/rider_team.py. Under Task Scheduler
    there's no console to watch; interactive runs keep seeing live output."""
    os.makedirs(LOG_DIR, exist_ok=True)
    logger = logging.getLogger("divergence_recorder")
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
    """One cycle: bulk venue fetch + oracle batch (via ftso.py's own cache),
    one oracle_divergence row per symbol that resolved on both sides. Never
    raises on an ordinary bad cycle -- logged and skipped, not fatal to the
    loop -- EXCEPT via the explicit ZERO_ROW_FATAL_THRESHOLD escalation
    below, which is deliberately fatal by design, not a bug."""
    global _consecutive_failures, _consecutive_zero_rows
    try:
        tickers = exchange.fetch_tickers(FLARE_UNIVERSE)
        # REAL BUG, FOUND LIVE alongside the coverage fix: Coinbase's bulk
        # fetch_tickers() response has NO per-ticker timestamp at all --
        # both ccxt's `timestamp` and `datetime` fields are null for every
        # symbol, confirmed against the raw response. This has been true
        # since the Day 2 bulk-fetch efficiency change and was never
        # caught until this outage forced a full trace. fetch_time_ms is
        # an honest fallback -- the moment this batch was fetched, not the
        # exchange's own tick time -- used only when the ticker itself has
        # nothing better to offer.
        fetch_time_ms = time.time() * 1000.0
    except Exception as e:
        _consecutive_failures += 1
        print(f"[DIVERGENCE RECORDER] venue fetch failed ({e}) -- "
              f"consecutive={_consecutive_failures}")
        record_health("divergence_recorder", "CYCLE", {
            "rows_written": 0, "reason": "venue_fetch_failed", "error": str(e),
        }, _consecutive_failures)
        if _consecutive_failures == BLIND_TRANSPORT_THRESHOLD:
            record_health("divergence_recorder", "BLIND_TRANSPORT",
                          {"error": str(e)}, _consecutive_failures)
        return

    rows = []
    ftso_missing = []
    ticker_missing = []
    venue_price_missing = []
    for sym in FLARE_UNIVERSE:
        ftso = get_price(sym)
        ticker = tickers.get(sym)
        if ftso is None:
            ftso_missing.append(sym)
        if ticker is None:
            ticker_missing.append(sym)
        if ftso is None or ticker is None:
            continue
        venue_price = ticker.get("last")
        if venue_price is None:
            venue_price_missing.append(sym)
            continue
        # ticker's own timestamp/datetime win if a future ccxt/exchange
        # version ever populates them for bulk responses; fetch_time_ms is
        # the fallback that's actually exercised today (see the comment
        # above the fetch_tickers() call).
        venue_ts_ms = ticker.get("timestamp") or fetch_time_ms

        # oracle_timestamp is stored in the oracle's own native unit (unix
        # seconds); venue_timestamp in the venue's own native unit (unix ms)
        # -- deliberately NOT normalized to the same unit, so each column
        # stays an honest, unmodified copy of what its source reported.
        # timestamp_gap_ms is the one column that does the normalizing.
        timestamp_gap_ms = abs(ftso.timestamp * 1000 - venue_ts_ms)
        spread_bps = (ftso.price - venue_price) / venue_price * 10000.0
        rows.append({
            "symbol": sym,
            "feed_id": ftso.feed_id,
            "oracle_value": ftso.price,
            "oracle_timestamp": ftso.timestamp,
            "venue_value": venue_price,
            "venue_timestamp": int(venue_ts_ms),
            "timestamp_gap_ms": int(timestamp_gap_ms),
            "divergence_bps": spread_bps,
        })

    if not rows:
        _consecutive_zero_rows += 1
        n = len(FLARE_UNIVERSE)
        if len(ftso_missing) == n:
            reason = "empty_coverage"          # ftso.py has no coverage for anything
        elif len(ticker_missing) == n:
            reason = "venue_no_data"           # Coinbase returned nothing usable for any symbol
        elif len(venue_price_missing) + len(ticker_missing) == n:
            reason = "venue_price_missing"     # tickers present but every one lacks a usable 'last' price
        else:
            reason = "no_overlap"              # both sides had SOME data, but never the same symbol at once
        print(f"[DIVERGENCE RECORDER] no rows resolved this cycle -- reason={reason} "
              f"(ftso_missing={len(ftso_missing)}/{n}, ticker_missing={len(ticker_missing)}/{n}, "
              f"venue_price_missing={len(venue_price_missing)}/{n}) "
              f"consecutive_zero={_consecutive_zero_rows}")
        record_health("divergence_recorder", "CYCLE", {
            "rows_written": 0,
            "reason": reason,
            "venue_price_missing": venue_price_missing,
            "ftso_missing": ftso_missing,
            "ticker_missing": ticker_missing,
            "consecutive_zero_rows": _consecutive_zero_rows,
        }, _consecutive_zero_rows)

        if _consecutive_zero_rows >= ZERO_ROW_FATAL_THRESHOLD:
            record_health("divergence_recorder", "ZERO_ROWS_FATAL", {
                "consecutive_zero_rows": _consecutive_zero_rows,
                "reason": reason,
            }, _consecutive_zero_rows)
            print(f"[DIVERGENCE RECORDER] FATAL: {_consecutive_zero_rows} consecutive "
                  f"zero-row cycles -- exiting so the service restarts.")
            sys.exit(1)
        return

    _consecutive_zero_rows = 0
    try:
        get_client().table("oracle_divergence").insert(rows).execute()
        _consecutive_failures = 0
        print(f"[{time.strftime('%H:%M:%S')}] recorded "
              f"{len(rows)}/{len(FLARE_UNIVERSE)} symbols")
        record_health("divergence_recorder", "CYCLE", {
            "rows_written": len(rows),
            "universe_size": len(FLARE_UNIVERSE),
        }, 0)
    except Exception as e:
        _consecutive_failures += 1
        print(f"[DIVERGENCE RECORDER] insert failed ({e}) -- "
              f"consecutive={_consecutive_failures}")
        record_health("divergence_recorder", "CYCLE", {
            "rows_written": 0, "reason": "insert_failed", "error": str(e),
        }, _consecutive_failures)
        if _consecutive_failures == BLIND_TRANSPORT_THRESHOLD:
            record_health("divergence_recorder", "BLIND_TRANSPORT",
                          {"error": str(e)}, _consecutive_failures)


def run_recorder():
    _install_rotating_log()

    env_loaded = bool(os.getenv("SUPABASE_URL")) and bool(os.getenv("SUPABASE_KEY"))
    record_health("divergence_recorder", "STARTUP", {
        "env_loaded": env_loaded,
        "universe": FLARE_UNIVERSE,
        "cycle_sec": CYCLE_SEC,
    }, 0)

    print("=" * 78)
    print("      FLARE — DIVERGENCE RECORDER (continuous)")
    print("=" * 78)
    print(f"Universe : {len(FLARE_UNIVERSE)} symbols -- {FLARE_UNIVERSE}")
    print(f"Cadence  : every {CYCLE_SEC}s")
    print(f"Status   : LIVE (Ctrl+C to stop)\n")

    while True:
        try:
            _record_cycle()
            time.sleep(CYCLE_SEC)
        except KeyboardInterrupt:
            print("\n[Divergence Recorder] stopped safely.")
            break
        except Exception as e:
            print(f"\n[Divergence Recorder Error] {e}")
            time.sleep(CYCLE_SEC)


if __name__ == "__main__":
    run_recorder()
