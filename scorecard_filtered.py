"""
scorecard_filtered.py

Runs CORE and RIDER across the four windows TWICE each -- once unfiltered
(the baseline we already have) and once with the 200-day trend gate -- so we
can measure exactly what the filter fixes and whether it costs anything in
the good windows.

We re-implement thin filtered versions of the two run loops here rather than
editing the originals, so the baseline engines stay untouched for comparison.
"""

from datetime import datetime, timezone, date as _date
from fetch_history import fetch_hourly_candles
from backtest import (candles_to_daily, window_ending_at, analyze_window,
                      ENTRY_BAND, entry_triggered)
from rider import rolling_high, PULLBACK_PCT, TARGET_PCT, SLICE_USD
from trend_filter import trend_is_up
import portfolio_state as ps

MS_PER_DAY = 86400000


def idx_on_or_after(daily, y, m, d):
    target = _date(y, m, d)
    for idx, row in enumerate(daily):
        if datetime.fromtimestamp(row["timestamp"] / 1000, tz=timezone.utc).date() >= target:
            return idx
    return len(daily) - 1


def run_core(daily, start, end, use_filter):
    """CORE loop, optionally gated by the trend filter on ENTRY only."""
    state = ps.new_state()
    for i in range(max(start, 400), end):
        price = daily[i]["close"]
        res = analyze_window(window_ending_at(daily, i))
        if res is None:
            continue
        floor, top, spread = res["range_floor"], res["range_top"], res["avg_weekly_spread"]

        if not state["core_holding"]:
            # ENTRY -- gated by trend if filter on
            if entry_triggered(price, floor):
                if (not use_filter) or trend_is_up(daily, i):
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
            # else hold

    last_price = daily[end - 1]["close"]
    open_pl = state["core_units"] * last_price - state["core_usd_in"]
    return {"realized": state["treasury"], "open": open_pl, "holding": state["core_holding"]}


def run_rider_f(daily, start, end, use_filter):
    """RIDER loop, optionally gated by the trend filter on ENTRY only."""
    holding = False
    entry_price = entry_units = 0.0
    entry_index = None
    treasury = 0.0
    entries = flips = 0
    stood_aside = 0

    for i in range(max(start, 400), end):
        price = daily[i]["close"]
        res = analyze_window(window_ending_at(daily, i))
        floor = res["range_floor"] if res else None

        if not holding:
            hi7 = rolling_high(daily, i)
            drop = (hi7 - price) / hi7 * 100.0 if hi7 > 0 else 0.0
            if drop >= PULLBACK_PCT:
                below_floor = floor is not None and price < floor
                blocked_by_trend = use_filter and not trend_is_up(daily, i)
                if below_floor or blocked_by_trend:
                    stood_aside += 1
                else:
                    holding = True
                    entry_price = price
                    entry_units = SLICE_USD / price
                    entry_index = i
                    entries += 1
        else:
            target = entry_price * (1 + TARGET_PCT / 100.0)
            if daily[i]["high"] >= target:
                treasury += entry_units * target - SLICE_USD
                flips += 1
                holding = False

    open_bag_days = ((daily[end-1]["timestamp"] - daily[entry_index]["timestamp"]) / MS_PER_DAY) if holding else 0
    return {"realized": treasury, "entries": entries, "flips": flips,
            "stood_aside": stood_aside, "open_bag_days": open_bag_days}


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

    print("=" * 78)
    print("200-DAY TREND FILTER: does gating entries by the long trend fix the bear?")
    print("=" * 78)

    for label, (sy, sm, sd), (ey, em, ed) in WINDOWS:
        start = idx_on_or_after(daily, sy, sm, sd)
        end = idx_on_or_after(daily, ey, em, ed)

        core_off = run_core(daily, start, end, use_filter=False)
        core_on  = run_core(daily, start, end, use_filter=True)
        rider_off = run_rider_f(daily, start, end, use_filter=False)
        rider_on  = run_rider_f(daily, start, end, use_filter=True)

        print(f"\n{label}")
        print("-" * 78)
        print(f"  CORE   OFF: realized ${core_off['realized']:>8.2f}  "
              f"open ${core_off['open']:>9.2f}   |   "
              f"ON: realized ${core_on['realized']:>8.2f}  open ${core_on['open']:>9.2f}")
        print(f"  RIDER  OFF: realized ${rider_off['realized']:>8.2f}  "
              f"flips {rider_off['flips']}/{rider_off['entries']}  bag {rider_off['open_bag_days']:.0f}d   |   "
              f"ON: realized ${rider_on['realized']:>8.2f}  "
              f"flips {rider_on['flips']}/{rider_on['entries']}  aside {rider_on['stood_aside']}")

    print("\n" + "=" * 78)
    print("Read: in bears (win 2,3) does ON cut the frozen bags? In bulls (1,4)")
    print("does ON keep most of the profit, or does the lagging MA choke the good windows?")
    print("=" * 78)