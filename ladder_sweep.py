"""
ladder_sweep.py

Sweep ladder SHAPES across the four windows to find where bull-profit-kept
crosses bear-protection. All shapes use the same measured rung depths
(-0/-20/-40/-60%); only the BITE SIZES change -- from timid to aggressively
front-loaded.

Shapes tested:
  LUMP      10 at once      (baseline: best bull, worst bear freeze)
  4-3-2-1   timid front     (best bear softening, worst bull cost)
  5-2-2-1   moderate front
  6-2-1-1   heavy front
  7-1-1-1   very heavy front (near-lump, minimal reserve)
"""

from datetime import datetime, timezone, date as _date
from fetch_history import fetch_hourly_candles
from backtest import (candles_to_daily, window_ending_at, analyze_window,
                      ENTRY_BAND, entry_triggered)
import portfolio_state as ps

DEPTHS = [0.00, 0.20, 0.40, 0.60]   # rung depths from first entry (measured)

SHAPES = {
    "LUMP     ": None,               # special-cased: all 10 at once
    "4-3-2-1  ": [4, 3, 2, 1],
    "5-2-2-1  ": [5, 2, 2, 1],
    "6-2-1-1  ": [6, 2, 1, 1],
    "7-1-1-1  ": [7, 1, 1, 1],
}

WINDOWS = [
    ("Bear->Bull", (2020, 10, 1), (2021, 5, 20)),
    ("Bull->Bear", (2021, 5, 20), (2021, 12, 15)),
    ("Calm Bear ", (2022, 1, 1),  (2022, 12, 31)),
    ("Calm Bull ", (2024, 10, 1), (2025, 1, 31)),
]


def idx_on_or_after(daily, y, m, d):
    target = _date(y, m, d)
    for idx, row in enumerate(daily):
        if datetime.fromtimestamp(row["timestamp"] / 1000, tz=timezone.utc).date() >= target:
            return idx
    return len(daily) - 1


def run_shape(daily, start, end, bites):
    """
    Run CORE with a given ladder shape. bites=None means lump-sum (10 at once
    + 1 reserve on floor break). Otherwise deploy bites[k] slices at DEPTHS[k].
    Returns realized + open P&L.
    """
    state = ps.new_state()
    first_entry = None
    rungs_filled = 0

    for i in range(max(start, 400), end):
        price = daily[i]["close"]
        res = analyze_window(window_ending_at(daily, i))
        if res is None:
            continue
        floor, top, spread = res["range_floor"], res["range_top"], res["avg_weekly_spread"]

        if not state["core_holding"]:
            if entry_triggered(price, floor):
                if bites is None:
                    ps.core_deploy(state, price, ps.CORE_MAX_SLICES)
                else:
                    ps.core_deploy(state, price, bites[0])
                    first_entry = price
                    rungs_filled = 1
        else:
            avg = ps.core_average_entry(state)
            target = avg + 0.80 * spread if (avg and spread) else None
            in_profit = avg is not None and price > avg

            if in_profit and target is not None and price >= target:
                ps.core_sell_all(state, price); first_entry = None; rungs_filled = 0
            elif in_profit and price > top:
                ps.core_sell_all(state, price); first_entry = None; rungs_filled = 0
            elif bites is None:
                # lump-sum reserve behavior
                if price < floor and not state["core_reserve_used"]:
                    ps.core_deploy_reserve(state, price)
            else:
                # ladder: fill next rung if price reached its depth
                if rungs_filled < len(bites) and first_entry:
                    rung_price = first_entry * (1 - DEPTHS[rungs_filled])
                    if price <= rung_price:
                        ps.core_deploy(state, price, bites[rungs_filled])
                        rungs_filled += 1

    last = daily[end - 1]["close"]
    return state["treasury"], state["core_units"] * last - state["core_usd_in"]


if __name__ == "__main__":
    print("Loading XLM history ...")
    candles = fetch_hourly_candles(symbol="XLM/USDT", since_year=2019)
    daily = candles_to_daily(candles)
    print(f"{len(daily)} daily candles\n")

    # precompute window indices
    wins = [(name, idx_on_or_after(daily, *s), idx_on_or_after(daily, *e))
            for name, s, e in WINDOWS]

    # header
    print("=" * 92)
    print("LADDER SHAPE SWEEP  (realized + open P&L per window; open<0 = paper bag)")
    print("=" * 92)
    header = "  SHAPE     " + "".join(f"| {name:^18}" for name, _, _ in wins)
    print(header)
    print("-" * 92)

    for shape_name, bites in SHAPES.items():
        row = f"  {shape_name}"
        for _, s, e in wins:
            realized, openpl = run_shape(daily, s, e, bites)
            total = realized + openpl
            # show realized and open compactly
            cell = f"| R{realized:>6.0f} O{openpl:>7.0f}"
            row += f" {cell}"
        print(row)

    print("-" * 92)
    print("R = realized profit (banked)   O = open P&L (paper; negative = bag)")
    print("\nRead each column:")
    print("  Bull windows (1,4): higher R is better -> which shape keeps most bull profit?")
    print("  Bear windows (2,3): less-negative O is better -> which shape softens the bag most?")
    print("  The winner: best bull R without a much worse bear O. That's your shape.")
    print("=" * 92)