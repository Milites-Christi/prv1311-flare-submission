"""
final_scorecard.py

THE CAPSTONE. The fully-tuned two-engine system running together:
  - CORE:  6-2-1-1 ladder (-0/-20/-40/-60%), never-cut, no trend filter
  - RIDER: -4% pullback / +7% flip, 200-day trend filter ON

Both run on the same daily loop, sharing one treasury, across four windows.
Reports the full scorecard locked back in the spec:
  total return, max drawdown, longest time underwater, and the realized/open split.
"""

from datetime import datetime, timezone, date as _date
from fetch_history import fetch_hourly_candles
from backtest import (candles_to_daily, window_ending_at, analyze_window,
                      ENTRY_BAND, entry_triggered)
from rider import rolling_high, PULLBACK_PCT, TARGET_PCT
from trend_filter import trend_is_up
import portfolio_state as ps

CORE_BITES = [6, 2, 1, 1]
CORE_DEPTHS = [0.00, 0.20, 0.40, 0.60]
RIDER_SLICE = 1666.67


def idx_on_or_after(daily, y, m, d):
    target = _date(y, m, d)
    for idx, row in enumerate(daily):
        if datetime.fromtimestamp(row["timestamp"] / 1000, tz=timezone.utc).date() >= target:
            return idx
    return len(daily) - 1


def run_combined(daily, start, end):
    """
    Run CORE (6-2-1-1 ladder) and RIDER (filtered) together on one loop,
    one shared treasury. Track equity each day to compute drawdown + underwater.
    """
    state = ps.new_state()
    core_first_entry = None
    core_rungs = 0

    # rider state (independent)
    r_holding = False
    r_entry = r_units = 0.0

    # equity tracking for drawdown / underwater
    equity_curve = []   # total value each day = treasury + open positions marked to price
    peak_equity = 0.0
    max_dd = 0.0
    underwater_start = None
    longest_underwater = 0

    for i in range(max(start, 400), end):
        price = daily[i]["close"]
        res = analyze_window(window_ending_at(daily, i))
        if res is None:
            continue
        floor, top, spread = res["range_floor"], res["range_top"], res["avg_weekly_spread"]

        # ---------------- CORE (6-2-1-1 ladder) ----------------
        if not state["core_holding"]:
            if entry_triggered(price, floor):
                ps.core_deploy(state, price, CORE_BITES[0])
                core_first_entry = price
                core_rungs = 1
        else:
            avg = ps.core_average_entry(state)
            target = avg + 0.80 * spread if (avg and spread) else None
            in_profit = avg is not None and price > avg
            if in_profit and target is not None and price >= target:
                ps.core_sell_all(state, price); core_first_entry = None; core_rungs = 0
            elif in_profit and price > top:
                ps.core_sell_all(state, price); core_first_entry = None; core_rungs = 0
            elif core_rungs < len(CORE_BITES) and core_first_entry:
                rung_price = core_first_entry * (1 - CORE_DEPTHS[core_rungs])
                if price <= rung_price:
                    ps.core_deploy(state, price, CORE_BITES[core_rungs])
                    core_rungs += 1

        # ---------------- RIDER (filtered) ----------------
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

        # ---------------- equity mark-to-market ----------------
        core_val = state["core_units"] * price
        rider_val = r_units * price if r_holding else 0.0
        core_cost = state["core_usd_in"]
        rider_cost = RIDER_SLICE if r_holding else 0.0
        # equity = banked treasury + current value of open positions - their cost basis
        open_pl = (core_val - core_cost) + (rider_val - rider_cost)
        equity = state["treasury"] + open_pl
        equity_curve.append(equity)

        # drawdown tracking
        if equity > peak_equity:
            peak_equity = equity
        dd = peak_equity - equity
        if dd > max_dd:
            max_dd = dd

        # underwater tracking (open positions in the red)
        if open_pl < 0:
            if underwater_start is None:
                underwater_start = i
            span = i - underwater_start
            if span > longest_underwater:
                longest_underwater = span
        else:
            underwater_start = None

    last = daily[end - 1]["close"]
    core_open = state["core_units"] * last - state["core_usd_in"]
    rider_open = (r_units * last - RIDER_SLICE) if r_holding else 0.0

    return {
        "realized": state["treasury"],
        "open": core_open + rider_open,
        "total": state["treasury"] + core_open + rider_open,
        "max_dd": max_dd,
        "longest_underwater": longest_underwater,
        "core_holding": state["core_holding"],
        "rider_holding": r_holding,
    }


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
    print("FINAL SCORECARD -- fully-tuned system (CORE 6-2-1-1 + RIDER filtered), together")
    print("Capital: $20k vehicle. Figures are $ on that base.")
    print("=" * 84)

    grand_realized = 0.0
    for label, (sy, sm, sd), (ey, em, ed) in WINDOWS:
        start = idx_on_or_after(daily, sy, sm, sd)
        end = idx_on_or_after(daily, ey, em, ed)
        r = run_combined(daily, start, end)
        grand_realized += r["realized"]

        print(f"\n{label}")
        print("-" * 84)
        print(f"  Realized (banked):        ${r['realized']:>10.2f}")
        print(f"  Open (paper, unrealized): ${r['open']:>10.2f}")
        print(f"  TOTAL (real + paper):     ${r['total']:>10.2f}")
        print(f"  Max drawdown:             ${r['max_dd']:>10.2f}")
        print(f"  Longest underwater:       {r['longest_underwater']:>6} days")
        flags = []
        if r["core_holding"]: flags.append("CORE still holding")
        if r["rider_holding"]: flags.append("RIDER still holding")
        if flags:
            print(f"  At window end: {', '.join(flags)}")

    print("\n" + "=" * 84)
    print(f"  SUM OF REALIZED (banked) ACROSS ALL 4 WINDOWS: ${grand_realized:.2f}")
    print("=" * 84)
    print("\nThis is the whole machine. Realized = money actually banked. Open = paper")
    print("positions held (never cut). Max drawdown + underwater = the honest risk panel")
    print("your spec always lacked. Read it as the true face of the tuned system.")