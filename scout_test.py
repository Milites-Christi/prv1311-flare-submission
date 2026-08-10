"""
scout_test.py

CORE 6-2-1-1 with the falling-floor scout gating ENTRY, vs. without it,
across the four windows.

The scout should keep CORE OUT of the sustained bear (window 3) -- standing
aside instead of deploying into a decaying range -- WITHOUT hurting the
bull/chop windows (where the floor is flat or rising, so the scout stays out
of the way and lets CORE deploy normally).
"""

from datetime import datetime, timezone, date as _date
from fetch_history import fetch_hourly_candles
from backtest import (candles_to_daily, window_ending_at, analyze_window,
                      entry_triggered)
from scout import floor_is_falling
import portfolio_state as ps

CORE_BITES = [6, 2, 1, 1]
CORE_DEPTHS = [0.00, 0.20, 0.40, 0.60]


def idx_on_or_after(daily, y, m, d):
    target = _date(y, m, d)
    for idx, row in enumerate(daily):
        if datetime.fromtimestamp(row["timestamp"] / 1000, tz=timezone.utc).date() >= target:
            return idx
    return len(daily) - 1


def run_core_ladder(daily, start, end, use_scout):
    """CORE 6-2-1-1 ladder; if use_scout, the FIRST rung only fires when the
    floor is NOT falling. Once in, the ladder + exits are unchanged."""
    state = ps.new_state()
    first_entry = None
    rungs = 0
    stood_aside = 0

    for i in range(max(start, 400), end):
        price = daily[i]["close"]
        res = analyze_window(window_ending_at(daily, i))
        if res is None:
            continue
        floor, top, spread = res["range_floor"], res["range_top"], res["avg_weekly_spread"]

        if not state["core_holding"]:
            if entry_triggered(price, floor):
                # SCOUT GATE -- only on the first entry
                if use_scout and floor_is_falling(daily, i):
                    stood_aside += 1
                    continue
                ps.core_deploy(state, price, CORE_BITES[0])
                first_entry = price
                rungs = 1
        else:
            avg = ps.core_average_entry(state)
            target = avg + 0.80 * spread if (avg and spread) else None
            in_profit = avg is not None and price > avg
            if in_profit and target is not None and price >= target:
                ps.core_sell_all(state, price); first_entry = None; rungs = 0
            elif in_profit and price > top:
                ps.core_sell_all(state, price); first_entry = None; rungs = 0
            elif rungs < len(CORE_BITES) and first_entry:
                rung_price = first_entry * (1 - CORE_DEPTHS[rungs])
                if price <= rung_price:
                    ps.core_deploy(state, price, CORE_BITES[rungs])
                    rungs += 1

    last = daily[end - 1]["close"]
    return {"realized": state["treasury"],
            "open": state["core_units"] * last - state["core_usd_in"],
            "holding": state["core_holding"],
            "stood_aside": stood_aside}


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

    print("=" * 84)
    print("CORE 6-2-1-1: WITHOUT scout  vs  WITH falling-floor scout")
    print("=" * 84)

    for label, (sy, sm, sd), (ey, em, ed) in WINDOWS:
        start = idx_on_or_after(daily, sy, sm, sd)
        end = idx_on_or_after(daily, ey, em, ed)

        off = run_core_ladder(daily, start, end, use_scout=False)
        on = run_core_ladder(daily, start, end, use_scout=True)

        print(f"\n{label}")
        print("-" * 84)
        print(f"  WITHOUT scout: realized ${off['realized']:>8.2f}   open ${off['open']:>10.2f}")
        on_line = f"  WITH scout:    realized ${on['realized']:>8.2f}   open ${on['open']:>10.2f}"
        if on["stood_aside"]:
            on_line += f"   [stood aside {on['stood_aside']}x]"
        print(on_line)

    print("\n" + "=" * 84)
    print("Read: does WITH-scout shrink the bear bag (win 3 open) by standing aside,")
    print("WITHOUT hurting the bull windows (1,4 should stay ~unchanged)?")
    print("=" * 84)