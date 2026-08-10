"""
================================================================================
PROJECT: Prv1311 — Cheap Window (intraday low-clustering, the timing brain)
FILE: flip_cheap_window.py
================================================================================
Finds the recurring 5-7h UTC window where an asset's daily LOWS cluster — i.e.
what time of day it's reliably cheapest. Ported from Accum-Flip 90-Day Analysis.

WHAT IT DOES: bins each day's low by UTC hour, slides a 5-7h window to find the
densest cluster, reports window start/end/length + a CONSISTENCY % (how often
the daily low actually lands inside it). Calendar-aware: compares early-month
(1-10) vs late-month (22-29) best-low-hour to flag a monthly shift.

WHO USES IT (plug point): any fleet can ask "is NOW inside this asset's cheap
window?" before firing — so entries land when the asset is statistically
cheapest in the day, not just whenever a dip appears. Answers the observed
timing pattern (8AM open, overnight bullish, traders set 7:30AM).

PURE FUNCTION cheap_window(candles) + standalone LAB scanning the watchlist.
CONSTANTS: WINDOW_MIN_HOURS=5, WINDOW_MAX_HOURS=7, MIN_DAYS_FOR_CALENDAR_SPLIT=60.
Needs hourly candles with timestamps (uses the markov_screener feed).
================================================================================
"""

import json
import time
import os
from datetime import datetime, timezone
from config import QUOTE, WATCHLIST, CHEAP_WINDOW_LEDGER_FILE
from markov_screener import fetch_hourly
from sync_supabase import push_cheap_window

WINDOW_MIN_HOURS = 5
WINDOW_MAX_HOURS = 7
MIN_DAYS_FOR_CALENDAR_SPLIT = 60


def cheap_window(candles):
    """PURE FUNCTION. candles = list of {ts(ms), l, ...} hourly, oldest->newest.
    Returns the cheap-window dict. Other engines call this to time entries."""
    base = {
        'cheap_window_start_utc_hour': None, 'cheap_window_end_utc_hour': None,
        'cheap_window_length_hours': None, 'cheap_window_consistency_pct': None,
        'sample_days': 0, 'calendar_note': 'insufficient data',
    }
    if not candles or len(candles) < 2:
        return base

    # per-day: track the hour of that day's low
    day_map = {}
    for c in candles:
        ts = c['ts']
        d = datetime.fromtimestamp(ts / 1000 if ts >= 1e12 else ts, timezone.utc)
        day_key = f"{d.year}-{d.month}-{d.day}"
        low = c['l']
        hour = d.hour
        if day_key not in day_map:
            day_map[day_key] = {'low': low, 'hour': hour, 'dom': d.day}
        elif low < day_map[day_key]['low']:
            day_map[day_key]['low'] = low
            day_map[day_key]['hour'] = hour

    day_keys = list(day_map.keys())
    total_days = len(day_keys)
    hour_counts = [0] * 24
    for k in day_keys:
        hour_counts[day_map[k]['hour']] += 1

    # slide 5-7h window for densest low-cluster
    best_window = None
    best_count = -1
    for win_len in range(WINDOW_MIN_HOURS, WINDOW_MAX_HOURS + 1):
        for start in range(24):
            count = sum(hour_counts[(start + off) % 24] for off in range(win_len))
            if count > best_count:
                best_count = count
                best_window = {'start': start, 'length': win_len}

    start_h = best_window['start'] if best_window else None
    end_h = (best_window['start'] + best_window['length']) % 24 if best_window else None
    length_h = best_window['length'] if best_window else None
    consistency = (round(best_count / total_days * 10000) / 100
                   if best_window and total_days > 0 else None)

    # calendar effect: early-month vs late-month best low hour
    calendar_note = 'insufficient data to assess calendar effect'
    if total_days >= MIN_DAYS_FOR_CALENDAR_SPLIT:
        early = [0] * 24
        late = [0] * 24
        early_days = late_days = 0
        for k in day_keys:
            e = day_map[k]
            if 1 <= e['dom'] <= 10:
                early[e['hour']] += 1
                early_days += 1
            elif 22 <= e['dom'] <= 29:
                late[e['hour']] += 1
                late_days += 1
        if early_days > 0 and late_days > 0:
            early_best = early.index(max(early))
            late_best = late.index(max(late))
            shift = ' (no shift observed)' if early_best == late_best else ' (possible shift observed)'
            calendar_note = (f"early-month best low hour (UTC): {early_best}, "
                             f"late-month best low hour (UTC): {late_best}{shift}")

    return {
        'cheap_window_start_utc_hour': start_h,
        'cheap_window_end_utc_hour': end_h,
        'cheap_window_length_hours': length_h,
        'cheap_window_consistency_pct': consistency,
        'sample_days': total_days,
        'calendar_note': calendar_note,
    }


def in_cheap_window(cw, now_hour_utc=None):
    """Helper: is the current UTC hour inside the asset's cheap window?"""
    start = cw.get('cheap_window_start_utc_hour')
    length = cw.get('cheap_window_length_hours')
    if start is None or length is None:
        return False
    if now_hour_utc is None:
        now_hour_utc = datetime.now(timezone.utc).hour
    for off in range(length):
        if (start + off) % 24 == now_hour_utc:
            return True
    return False


# ---------------------------------------------------------------------------
# STANDALONE LAB
# ---------------------------------------------------------------------------
LAB_SYMBOLS = [f"{t}/{QUOTE}" for t in WATCHLIST[:20]]


def run_cycle():
    rows = []
    now_hour = datetime.now(timezone.utc).hour
    for sym in LAB_SYMBOLS:
        candles = fetch_hourly(sym, limit=2200)   # ~90 days hourly
        if not candles or len(candles) < 48:
            continue
        cw = cheap_window(candles)
        cw['asset'] = sym.split('/')[0]
        cw['live_now'] = in_cheap_window(cw, now_hour)
        rows.append(cw)
        w = f"{cw['cheap_window_start_utc_hour']:02d}-{cw['cheap_window_end_utc_hour']:02d} UTC" \
            if cw['cheap_window_start_utc_hour'] is not None else "n/a"
        live = " <-- LIVE NOW" if cw['live_now'] else ""
        print(f"  {cw['asset']:<6} window {w:<12} consistency {cw['cheap_window_consistency_pct']}%  ({cw['sample_days']}d){live}")

    state = {'system': 'Prv1311-cheap-window',
             'now_hour_utc': now_hour,
             'rows': rows,
             'updated': time.strftime('%Y-%m-%d %H:%M:%S')}
    with open(CHEAP_WINDOW_LEDGER_FILE, 'w') as f:
        json.dump(state, f, indent=4)
    push_cheap_window(state)
    return state


def run_engine():
    print("=" * 78)
    print("      PRV1311 — CHEAP WINDOW LAB (intraday low-clustering)")
    print("=" * 78)
    print(f"Scans : {len(LAB_SYMBOLS)} watchlist assets (~90d hourly)")
    print(f"Logic : densest {WINDOW_MIN_HOURS}-{WINDOW_MAX_HOURS}h UTC window where daily lows cluster + consistency%")
    print(f"Status: LIVE (Ctrl+C to stop)\n")
    if not os.path.exists('data'):
        os.makedirs('data')
    while True:
        try:
            print(f"\n[{time.strftime('%H:%M:%S')}] Cheap-window scan...")
            state = run_cycle()
            live = [r['asset'] for r in state['rows'] if r.get('live_now')]
            print("-" * 78)
            print(f"  In cheap window NOW ({len(live)}): {', '.join(live) if live else 'none'}")
            print("-" * 78)
            time.sleep(60 * 60)   # hourly is plenty; windows shift slowly
        except KeyboardInterrupt:
            print("\n[Cheap Window Lab] stopped safely.")
            break
        except Exception as e:
            print(f"\n[Cheap Window Lab Error] {e}")
            time.sleep(60)


if __name__ == "__main__":
    run_engine()