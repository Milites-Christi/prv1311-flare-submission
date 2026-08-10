"""
scorecard.py

The honest report card. Runs three configurations across four market
windows and prints them side by side:
  - CORE alone   (the patient accumulator)
  - RIDER alone  (the nimble flipper)
  - BOTH         (running together, shared treasury)

No new strategy logic -- it calls the engines already built and tested
in backtest.py and rider.py. This just measures.

The four windows (XLM history):
  1. Bear->Bull transition : Oct 2020 -> May 2021  (the run-up)
  2. Bull->Bear transition : May 2021 -> Dec 2021  (the top rolling over)
  3. Calm Bear             : Jan 2022 -> Dec 2022  (the grinding decline)
  4. Calm Bull / recovery  : Oct 2024 -> Jan 2025  (the late-2024 rip)
"""

from datetime import datetime, timezone, date as _date
from fetch_history import fetch_hourly_candles
from backtest import candles_to_daily, window_ending_at, analyze_window, run_backtest
from rider import run_rider
import portfolio_state as ps


def idx_on_or_after(daily, y, m, d):
    target = _date(y, m, d)
    for idx, row in enumerate(daily):
        if datetime.fromtimestamp(row["timestamp"] / 1000, tz=timezone.utc).date() >= target:
            return idx
    return len(daily) - 1


def core_result(daily, start, end):
    """Run CORE alone over the window; return realized treasury + open state."""
    # ensure a full 90-day window exists behind the start
    safe_start = max(start, 400)
    state, log = run_backtest(daily, safe_start, end, verbose=False)
    # unrealized on anything still open at the end
    last_price = daily[end - 1]["close"]
    core_open = state["core_units"] * last_price - state["core_usd_in"]
    return {
        "realized": state["treasury"],
        "open_unrealized": core_open,
        "still_holding": state["core_holding"],
    }


def rider_result(daily, start, end):
    safe_start = max(start, 400)
    return run_rider(daily, safe_start, end, verbose=False)


WINDOWS = [
    ("1. Bear->Bull  (Oct'20-May'21)", (2020, 10, 1), (2021, 5, 20)),
    ("2. Bull->Bear  (May'21-Dec'21)", (2021, 5, 20), (2021, 12, 15)),
    ("3. Calm Bear   (Jan'22-Dec'22)", (2022, 1, 1),  (2022, 12, 31)),
    ("4. Calm Bull   (Oct'24-Jan'25)", (2024, 10, 1), (2025, 1, 31)),
]


if __name__ == "__main__":
    print("Loading XLM history ...")
    candles = fetch_hourly_candles(symbol="XLM/USDT", since_year=2019)
    daily = candles_to_daily(candles)
    print(f"{len(daily)} daily candles\n")

    print("=" * 74)
    print("SCORECARD -- CORE vs RIDER vs BOTH, across four market regimes")
    print("(all figures on a $1,666/slice basis; CORE uses 10+1, RIDER uses 1)")
    print("=" * 74)

    for label, (sy, sm, sd), (ey, em, ed) in WINDOWS:
        start = idx_on_or_after(daily, sy, sm, sd)
        end = idx_on_or_after(daily, ey, em, ed)
        days = end - start

        core = core_result(daily, start, end)
        rider = rider_result(daily, start, end)

        # "both" = simply the sum of their realized results (they run
        # independently on separate capital, shared treasury)
        both_realized = core["realized"] + rider["treasury"]

        print(f"\n{label}   ({days} days)")
        print("-" * 74)
        # CORE
        core_line = f"  CORE   realized ${core['realized']:>9.2f}"
        if core["still_holding"]:
            core_line += f"   (still holding, unrealized ${core['open_unrealized']:>9.2f})"
        print(core_line)
        # RIDER
        rider_line = (f"  RIDER  realized ${rider['treasury']:>9.2f}"
                      f"   flips {rider['flips']:>2}/{rider['entries']:<2}"
                      f"   avg {rider['avg_days_per_flip']:>4.0f}d/flip"
                      f"   longest {rider['max_days_per_flip']:>4.0f}d")
        if rider["open_bag"]:
            rider_line += f"  [BAG {rider['open_bag_days']:.0f}d]"
        print(rider_line)
        # BOTH
        print(f"  BOTH   realized ${both_realized:>9.2f}")

    print("\n" + "=" * 74)
    print("Read: which engine carries which regime? Where does RIDER freeze?")
    print("Where does CORE's long-hold show up? Does BOTH smooth the ride?")
    print("=" * 74)