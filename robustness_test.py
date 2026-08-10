"""
robustness_test.py

THE make-or-break test: does the XLM-tuned system generalize, or is it overfit?

Runs the EXACT same tuned system -- CORE 6-2-1-1 ladder + RIDER (-4%/+7%,
200-day filter) -- on THREE assets (XLM, XRP, ALGO) across the four windows.
NOTHING is re-tuned per asset. That's the whole point: if it only works on
XLM, it's a curve-fit, not a strategy.
"""

from datetime import datetime, timezone, date as _date
from fetch_history import fetch_hourly_candles
from backtest import (candles_to_daily, window_ending_at, analyze_window,
                      entry_triggered)
from rider import rolling_high, PULLBACK_PCT, TARGET_PCT
from trend_filter import trend_is_up
import portfolio_state as ps

CORE_BITES = [6, 2, 1, 1]
CORE_DEPTHS = [0.00, 0.20, 0.40, 0.60]
RIDER_SLICE = 1666.67

ASSETS = ["XLM/USDT", "XRP/USDT", "ALGO/USDT"]

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


def run_combined(daily, start, end):
    """Identical to final_scorecard's combined engine. No per-asset changes."""
    state = ps.new_state()
    core_first = None
    core_rungs = 0
    r_holding = False
    r_entry = r_units = 0.0

    for i in range(max(start, 400), end):
        price = daily[i]["close"]
        res = analyze_window(window_ending_at(daily, i))
        if res is None:
            continue
        floor, top, spread = res["range_floor"], res["range_top"], res["avg_weekly_spread"]

        # CORE 6-2-1-1
        if not state["core_holding"]:
            if entry_triggered(price, floor):
                ps.core_deploy(state, price, CORE_BITES[0])
                core_first = price
                core_rungs = 1
        else:
            avg = ps.core_average_entry(state)
            target = avg + 0.80 * spread if (avg and spread) else None
            in_profit = avg is not None and price > avg
            if in_profit and target is not None and price >= target:
                ps.core_sell_all(state, price); core_first = None; core_rungs = 0
            elif in_profit and price > top:
                ps.core_sell_all(state, price); core_first = None; core_rungs = 0
            elif core_rungs < len(CORE_BITES) and core_first:
                rung = core_first * (1 - CORE_DEPTHS[core_rungs])
                if price <= rung:
                    ps.core_deploy(state, price, CORE_BITES[core_rungs])
                    core_rungs += 1

        # RIDER filtered
        if not r_holding:
            hi7 = rolling_high(daily, i)
            drop = (hi7 - price) / hi7 * 100.0 if hi7 > 0 else 0.0
            if drop >= PULLBACK_PCT:
                below_floor = floor is not None and price < floor
                trend_down = not trend_is_up(daily, i)
                if not (below_floor or trend_down):
                    r_holding = True
                    r_entry = price
                    r_units = RIDER_SLICE / price
        else:
            rtarget = r_entry * (1 + TARGET_PCT / 100.0)
            if daily[i]["high"] >= rtarget:
                state["treasury"] += r_units * rtarget - RIDER_SLICE
                r_holding = False

    last = daily[end - 1]["close"]
    core_open = state["core_units"] * last - state["core_usd_in"]
    rider_open = (r_units * last - RIDER_SLICE) if r_holding else 0.0
    return {"realized": state["treasury"], "open": core_open + rider_open}


if __name__ == "__main__":
    # load all three assets once
    data = {}
    for sym in ASSETS:
        print(f"Loading {sym} ...")
        candles = fetch_hourly_candles(symbol=sym, since_year=2019)
        data[sym] = candles_to_daily(candles)
        print(f"  {len(data[sym])} daily candles")
    print()

    print("=" * 90)
    print("ROBUSTNESS TEST -- SAME tuned system (CORE 6-2-1-1 + RIDER filtered), NOTHING re-tuned")
    print("=" * 90)
    print(f"  {'WINDOW':<12}" + "".join(f"| {sym.split('/')[0]:^22} " for sym in ASSETS))
    print("-" * 90)

    asset_totals = {sym: 0.0 for sym in ASSETS}

    for name, s, e in WINDOWS:
        row = f"  {name:<12}"
        for sym in ASSETS:
            daily = data[sym]
            start = idx_on_or_after(daily, *s)
            end = idx_on_or_after(daily, *e)
            r = run_combined(daily, start, end)
            asset_totals[sym] += r["realized"]
            row += f"| R{r['realized']:>7.0f} O{r['open']:>8.0f} "
        print(row)

    print("-" * 90)
    tot_row = f"  {'REALIZED SUM':<12}"
    for sym in ASSETS:
        tot_row += f"|  ${asset_totals[sym]:>18.0f} "
    print(tot_row)
    print("=" * 90)
    print("R = realized (banked)   O = open (paper) per window")
    print("\nTHE QUESTION: does XRP and ALGO look broadly like XLM -- positive realized in")
    print("bull/chop, frozen-but-not-lost in the bear? If yes, the strategy GENERALIZES.")
    print("If XRP/ALGO are wildly different or negative, the XLM tuning was overfit.")
    print("=" * 90)