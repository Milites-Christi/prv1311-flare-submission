"""
ladder_test.py

CORE with a WIDE, FRONT-LOADED, STEPPED entry ladder vs. the original
lump-sum CORE, across the four windows.

Rungs are set from XLM's MEASURED drawdown-from-entry distribution
(median -42%, 75th -57%, 90th -82%), so the spacing is data-backed, not
guessed. Fixed anchor: all rung depths are measured from the FIRST entry.

  Rung 1: price <= floor * ENTRY_BAND   -> 4 slices  (catches shallow bounces)
  Rung 2: price <= entry1 * (1 - 0.20)  -> 3 slices  (-20%)
  Rung 3: price <= entry1 * (1 - 0.40)  -> 2 slices  (-40%)
  Rung 4: price <= entry1 * (1 - 0.60)  -> 1 slice   (-60%, deep capitulation)
  cap 10 slices. Exits UNCHANGED (avg+80%spread or range_top, in profit only).
"""

from datetime import datetime, timezone, date as _date
from fetch_history import fetch_hourly_candles
from backtest import (candles_to_daily, window_ending_at, analyze_window,
                      ENTRY_BAND, entry_triggered)
import portfolio_state as ps

# --- Ladder config (bites and depths; depths from measured drawdowns) ---
LADDER = [
    (0.00, 4),   # rung 1: at entry trigger, 4 slices
    (0.20, 3),   # rung 2: -20% from first entry, 3 slices
    (0.40, 2),   # rung 3: -40%, 2 slices
    (0.60, 1),   # rung 4: -60%, 1 slice
]


def idx_on_or_after(daily, y, m, d):
    target = _date(y, m, d)
    for idx, row in enumerate(daily):
        if datetime.fromtimestamp(row["timestamp"] / 1000, tz=timezone.utc).date() >= target:
            return idx
    return len(daily) - 1


def run_core_lump(daily, start, end):
    """Original CORE: all 10 at once + 1 reserve on floor break."""
    state = ps.new_state()
    for i in range(max(start, 400), end):
        price = daily[i]["close"]
        res = analyze_window(window_ending_at(daily, i))
        if res is None:
            continue
        floor, top, spread = res["range_floor"], res["range_top"], res["avg_weekly_spread"]
        if not state["core_holding"]:
            if entry_triggered(price, floor):
                ps.core_deploy(state, price, ps.CORE_MAX_SLICES)
        else:
            avg = ps.core_average_entry(state)
            target = avg + 0.80 * spread if (avg and spread) else None
            in_profit = avg is not None and price > avg
            if in_profit and target is not None and price >= target:
                ps.core_sell_all(state, price)
            elif in_profit and price > top:
                ps.core_sell_all(state, price)
            elif price < floor and not state["core_reserve_used"]:
                ps.core_deploy_reserve(state, price)
    last = daily[end - 1]["close"]
    return {"realized": state["treasury"],
            "open": state["core_units"] * last - state["core_usd_in"],
            "holding": state["core_holding"],
            "avg": ps.core_average_entry(state)}


def run_core_ladder(daily, start, end):
    """CORE with the wide front-loaded ladder (fixed anchor from first entry)."""
    state = ps.new_state()
    first_entry_price = None      # anchor for the rung depths
    rungs_filled = 0              # how many ladder rungs have deployed

    for i in range(max(start, 400), end):
        price = daily[i]["close"]
        res = analyze_window(window_ending_at(daily, i))
        if res is None:
            continue
        floor, top, spread = res["range_floor"], res["range_top"], res["avg_weekly_spread"]

        if not state["core_holding"]:
            # --- RUNG 1: first entry ---
            if entry_triggered(price, floor):
                depth, slices = LADDER[0]
                ps.core_deploy(state, price, slices)
                first_entry_price = price
                rungs_filled = 1
        else:
            # --- check exits FIRST (in profit only), same rules ---
            avg = ps.core_average_entry(state)
            target = avg + 0.80 * spread if (avg and spread) else None
            in_profit = avg is not None and price > avg

            if in_profit and target is not None and price >= target:
                ps.core_sell_all(state, price)
                first_entry_price = None
                rungs_filled = 0
            elif in_profit and price > top:
                ps.core_sell_all(state, price)
                first_entry_price = None
                rungs_filled = 0
            else:
                # --- not exiting: check if the NEXT ladder rung should fill ---
                if rungs_filled < len(LADDER) and first_entry_price:
                    next_depth, next_slices = LADDER[rungs_filled]
                    rung_price = first_entry_price * (1 - next_depth)
                    if price <= rung_price:
                        ps.core_deploy(state, price, next_slices)
                        rungs_filled += 1
                # else: HOLD. underwater waits. never cut.

    last = daily[end - 1]["close"]
    return {"realized": state["treasury"],
            "open": state["core_units"] * last - state["core_usd_in"],
            "holding": state["core_holding"],
            "avg": ps.core_average_entry(state),
            "rungs": rungs_filled}


WINDOWS = [
    ("1. Bear->Bull (Oct'20-May'21)", (2020, 10, 1), (2021, 5, 20)),
    ("2. Bull->Bear (May'21-Dec'21)", (2021, 5, 20), (2021, 12, 15)),
    ("3. Calm Bear  (Jan'22-Dec'22)", (2022, 1, 1),  (2022, 12, 31)),
    ("4. Calm Bull  (Oct'24-Jan'25)", (2024, 10, 1), (2025, 1, 31)),
]


if __name__ == "__main__":
    print("Loading XLM history ...")
    candles = fetch_hourly_candles(symbol="XLM/USDT", since_year=2019)
    daily = candles_to_daily(candles)
    print(f"{len(daily)} daily candles\n")

    print("=" * 80)
    print("CORE: LUMP-SUM (10 at once) vs WIDE LADDER (4-3-2-1 at -0/-20/-40/-60%)")
    print("=" * 80)

    for label, (sy, sm, sd), (ey, em, ed) in WINDOWS:
        start = idx_on_or_after(daily, sy, sm, sd)
        end = idx_on_or_after(daily, ey, em, ed)

        lump = run_core_lump(daily, start, end)
        lad = run_core_ladder(daily, start, end)

        print(f"\n{label}")
        print("-" * 80)
        lump_line = f"  LUMP    realized ${lump['realized']:>8.2f}   open ${lump['open']:>10.2f}"
        if lump["holding"]:
            lump_line += f"   [holding, avg ${lump['avg']:.4f}]"
        print(lump_line)
        lad_line = f"  LADDER  realized ${lad['realized']:>8.2f}   open ${lad['open']:>10.2f}"
        if lad["holding"]:
            lad_line += f"   [holding {lad['rungs']} rungs, avg ${lad['avg']:.4f}]"
        print(lad_line)

    print("\n" + "=" * 80)
    print("Read: in the bear (win 3) does LADDER's open loss shrink vs LUMP?")
    print("In bulls (1,4) does LADDER keep most of the profit, or does 4-slice-")
    print("first-bite leave too much on the bench?")
    print("=" * 80)