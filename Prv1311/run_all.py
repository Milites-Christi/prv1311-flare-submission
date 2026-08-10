"""
================================================================================
PROJECT: Prv1311 — Run All (single-terminal orchestrator)
FILE: run_all.py
================================================================================
Runs every engine + lab in ONE terminal, staggered so they don't stampede the
Coinbase API (the 429 Too Many Requests problem). One engine erroring does not
kill the others. Ctrl+C stops all.

RATE-LIMIT DESIGN:
  - 60-second stagger between engine STARTS (not 2s) so their first scans don't
    all hit at once.
  - Heavy hourly scanners (Markov, Cheap Window) run on LONG cadences — they pull
    thousands of candles across many coins and don't need frequent refresh.
  - Trading fleets stay on a reasonable cadence; the fast one (OBI, single depth
    call each) can run often.
================================================================================
"""

import os
import sys
import logging
import threading
import time
import traceback
from logging.handlers import RotatingFileHandler

from supabase_client import record_health
import scavengers
import dogs
import markov
import core
import flip_ewma_buyzone
import regime
import flip_cheap_window
import orderbook_imbalance


def worker(name, setup, cycle, period_sec):
    try:
        state = setup() if setup else None
    except Exception:
        print(f"[{name}] setup failed:\n{traceback.format_exc()}")
        state = None
    while True:
        try:
            if state is not None:
                cycle(state)
            else:
                cycle()
        except Exception:
            print(f"[{name}] cycle error:\n{traceback.format_exc()}")
        time.sleep(period_sec)


# (name, setup_fn, cycle_fn, period_seconds)
# Cadences spread out to respect Coinbase rate limits. Heavy scanners run rarely.
#
# rider_team is DELIBERATELY NOT HERE -- it runs as the PRV1311-RiderTeam Windows
# service (Task Scheduler, AtStartup, SYSTEM). Driving it here too would mean two
# processes writing data/rider_ledger.json and the same Supabase rows concurrently.
# If you ever need to run it manually for a one-off test, use rider_team.py directly,
# not run_all.py -- and stop the service first.
JOBS = [
    ("SCAV",    scavengers.load_state,  lambda s: scavengers.run_cycle(s, scavengers.get_universe_markets(limit=50)), 10 * 60),
    ("DOGS",    dogs.load_state,        lambda s: dogs.run_cycle(s, dogs.get_universe_markets(limit=50)),             30 * 60),
    ("CORE",    core.load_state,        lambda s: core.run_cycle(s, core.get_universe_markets(limit=50)),             30 * 60),
    ("MARKOV",  markov.load_state,      lambda s: markov.run_cycle(s),                                                30 * 60),
    ("EWMA",    None,                   flip_ewma_buyzone.run_cycle,                                                  30 * 60),
    ("REGIME",  None,                   regime.run_cycle,                                                             60 * 60),
    ("CHEAP",   None,                   flip_cheap_window.run_cycle,                                                 120 * 60),
    ("OBI",     None,                   orderbook_imbalance.run_cycle,                                                10 * 60),
]

STAGGER_SEC = 60   # gap between each engine's first start

# Engines that run as their own Windows service (Task Scheduler), NOT as a thread
# here. If one of these ever shows up back in JOBS, something regressed -- it means
# two processes are about to fight over the same ledger file and Supabase rows.
SERVICE_MANAGED = [
    ("RIDER (rider_team.py)",           "PRV1311-RiderTeam"),
    ("FOOTPRINT (footprint_worker.py)", "PRV1311-FootprintWorker"),
]

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_FILE = os.path.join(LOG_DIR, "run_all.log")


def _install_rotating_log():
    """Tee stdout/stderr into a rotating file, in addition to the console --
    same pattern as footprint_worker.py/rider_team.py. Under Task Scheduler
    there's no console to watch; interactive runs keep seeing live output."""
    os.makedirs(LOG_DIR, exist_ok=True)
    logger = logging.getLogger("run_all")
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


def main():
    _install_rotating_log()

    env_loaded = bool(os.getenv("SUPABASE_URL")) and bool(os.getenv("SUPABASE_KEY"))
    record_health("run_all", "STARTUP", {
        "env_loaded": env_loaded,
        "engines_driven": [name for name, _, _, _ in JOBS],
        "service_managed": [label for label, _ in SERVICE_MANAGED],
        "stagger_sec": STAGGER_SEC,
    }, 0)

    print("=" * 78)
    print("      PRV1311 — RUN ALL (one terminal, staggered, rate-limit safe)")
    print("=" * 78)
    print("  DRIVEN HERE (threads in this process):")
    for name, _, _, period in JOBS:
        print(f"    {name:<8} every {period // 60} min")
    print()
    print("  SERVICE-MANAGED (NOT run here -- Windows Task Scheduler):")
    for label, task_name in SERVICE_MANAGED:
        print(f"    {label:<32} -> {task_name}")
    print()
    print(f"  Staggered {STAGGER_SEC}s apart on startup. Ctrl+C stops everything.\n")

    for name, setup, cycle, period in JOBS:
        t = threading.Thread(target=worker, args=(name, setup, cycle, period),
                             name=name, daemon=True)
        t.start()
        print(f"[{time.strftime('%H:%M:%S')}] started {name}")
        time.sleep(STAGGER_SEC)   # wide stagger so first scans don't collide

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\n[Run All] stopping — all engines will halt.")


if __name__ == "__main__":
    main()