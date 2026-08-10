"""
rider.py

The RIDER engine -- a separate, standalone strategy from CORE.

CORE accumulates and holds through anything (never cuts, can hold years).
The RIDER is the opposite animal: nimble buy/flip/repeat.

  1. Track the rolling 7-day high.
  2. When price drops PULLBACK_PCT below that high -> a dip happened.
  3. Buy ONLY if price is still >= range_floor (a normal dip, not a breakdown).
     If price < range_floor -> stay in cash (breakdown, stand aside).
  4. Once holding, sell when price is +TARGET_PCT above the Rider's entry.
  5. After selling, recalculate and wait for the next pullback. Repeat.

Never-cut still applies: if a bought dip goes underwater and doesn't hit the
target, the Rider HOLDS (never sells at a loss) -- but this ties up the slice,
which is the cost we're measuring. We count those as "bags" so we can see how
often and how long the Rider gets stuck versus flips clean.
"""

from datetime import datetime, timezone
from fetch_history import fetch_hourly_candles
from backtest import candles_to_daily, window_ending_at, analyze_window

MS_PER_DAY = 86400000

# --- Tunable knobs (measured from XLM data, not assumed) ---
PULLBACK_PCT = 4.0        # buy when price is this % below the 7-day high
TARGET_PCT = 7.0          # flip when price is this % above the Rider's entry
LOOKBACK_HIGH_DAYS = 7    # window for the "recent high" the dip is measured from
SLICE_USD = 1666.67       # one Rider slice


def rolling_high(daily, i, lookback_days=LOOKBACK_HIGH_DAYS):
    """Highest close in the trailing lookback_days ending at index i (inclusive)."""
    start_ts = daily[i]["timestamp"] - lookback_days * MS_PER_DAY
    hi = daily[i]["close"]
    j = i
    while j >= 0 and daily[j]["timestamp"] >= start_ts:
        if daily[j]["close"] > hi:
            hi = daily[j]["close"]
        j -= 1
    return hi


def run_rider(daily, start_index, end_index, verbose=False):
    """
    Run the Rider continuously over daily[start_index:end_index].
    Returns a results dict with the real, live win/bag counts and profit.
    """
    # Rider state
    holding = False
    entry_price = None
    entry_units = 0.0
    entry_index = None

    treasury = 0.0            # realized profit
    flips = 0                 # clean wins (hit target)
    entries = 0               # total buys
    days_in_position = []     # how long each completed flip took (days)
    skipped_breakdown = 0     # times we stood aside (price < floor)

    for i in range(start_index, end_index):
        today = daily[i]
        price = today["close"]

        # current 90-day floor (the safety line) as of today
        res = analyze_window(window_ending_at(daily, i))
        floor = res["range_floor"] if res else None

        if not holding:
            # ---- looking for an entry ----
            hi7 = rolling_high(daily, i)
            drop_pct = (hi7 - price) / hi7 * 100.0 if hi7 > 0 else 0.0

            if drop_pct >= PULLBACK_PCT:
                # a dip happened -- is it a healthy dip or a breakdown?
                if floor is not None and price < floor:
                    skipped_breakdown += 1   # below the floor -> stand aside
                else:
                    # BUY
                    holding = True
                    entry_price = price
                    entry_units = SLICE_USD / price
                    entry_index = i
                    entries += 1
                    if verbose:
                        d = datetime.fromtimestamp(today["timestamp"]/1000, tz=timezone.utc).date()
                        print(f"  {d}  BUY  @ ${price:.4f}  (dip {drop_pct:.1f}% from 7d high)")
        else:
            # ---- holding: look for the +TARGET_PCT flip ----
            target = entry_price * (1 + TARGET_PCT / 100.0)
            if today["high"] >= target:
                # SELL at target (clean flip)
                sell_price = target
                profit = entry_units * sell_price - SLICE_USD
                treasury += profit
                flips += 1
                held_days = (today["timestamp"] - daily[entry_index]["timestamp"]) / MS_PER_DAY
                days_in_position.append(held_days)
                if verbose:
                    d = datetime.fromtimestamp(today["timestamp"]/1000, tz=timezone.utc).date()
                    print(f"  {d}  SELL @ ${sell_price:.4f}  +{TARGET_PCT:.0f}%  profit ${profit:.2f}  (held {held_days:.0f}d)")
                holding = False
                entry_price = None
                entry_units = 0.0
                entry_index = None
            # else: HOLD. never cut. (this is a potential 'bag' if it drags on.)

    # if still holding at the end, it's an open bag
    open_bag = holding
    open_bag_days = ((daily[end_index-1]["timestamp"] - daily[entry_index]["timestamp"]) / MS_PER_DAY) if holding else 0

    return {
        "treasury": treasury,
        "entries": entries,
        "flips": flips,
        "win_rate": (flips / entries * 100.0) if entries else 0.0,
        "avg_days_per_flip": (sum(days_in_position)/len(days_in_position)) if days_in_position else 0,
        "max_days_per_flip": max(days_in_position) if days_in_position else 0,
        "skipped_breakdown": skipped_breakdown,
        "open_bag": open_bag,
        "open_bag_days": open_bag_days,
    }


def report(label, r):
    print(f"\n=== {label} ===")
    print(f"  Entries: {r['entries']}   Clean flips: {r['flips']}   "
          f"LIVE win rate: {r['win_rate']:.0f}%")
    print(f"  Realized profit: ${r['treasury']:.2f}")
    print(f"  Avg days per flip: {r['avg_days_per_flip']:.1f}   "
          f"Longest flip: {r['max_days_per_flip']:.0f} days")
    print(f"  Stood aside (below floor): {r['skipped_breakdown']} times")
    if r["open_bag"]:
        print(f"  ** Still holding a bag at end: {r['open_bag_days']:.0f} days and counting **")


if __name__ == "__main__":
    print("Loading XLM history ...")
    candles = fetch_hourly_candles(symbol="XLM/USDT", since_year=2019)
    daily = candles_to_daily(candles)
    print(f"{len(daily)} daily candles")

    def idx_on_or_after(y, m, d):
        from datetime import date as _date
        target = _date(y, m, d)
        for idx, row in enumerate(daily):
            if datetime.fromtimestamp(row["timestamp"]/1000, tz=timezone.utc).date() >= target:
                return idx
        return len(daily) - 1

    # Run across the WHOLE tradeable history first (the honest full picture)
    full = run_rider(daily, 400, len(daily), verbose=False)
    report(f"FULL HISTORY (2021-2026)  trigger -{PULLBACK_PCT:.0f}% / flip +{TARGET_PCT:.0f}%", full)

    # Then the bear window we know hurts (Dec 2021 -> Nov 2024)
    b_start = idx_on_or_after(2021, 12, 1)
    b_end = idx_on_or_after(2024, 11, 24)
    bear = run_rider(daily, b_start, b_end, verbose=False)
    report("BEAR WINDOW (Dec 2021 -> Nov 2024)", bear)

    # Then a bull window (Oct 2020 -> May 2021 run-up) where it should shine
    u_start = idx_on_or_after(2020, 10, 1)
    u_end = idx_on_or_after(2021, 5, 20)
    bull = run_rider(daily, max(u_start, 400), u_end, verbose=False)
    report("BULL WINDOW (Oct 2020 -> May 2021)", bull)

    print("\n=== Compare LIVE win rate to the 74% ceiling. And watch the bull vs bear split. ===")