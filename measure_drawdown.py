"""
measure_drawdown.py

Measurement #1 (both advisors agreed on this): the drawdown-from-entry
distribution.

For every real CORE entry signal (price <= floor * ENTRY_BAND) in XLM's
history, simulate the trade and record:
  - the MAX drawdown below entry before it recovered to the exit target
  - whether it was "shallow" (recovered fast, small dip) or "deep"

This replaces every guessed number:
  - Gemini's "70-80% shallow bounce" -> measured for real
  - the 10% / 20% / 25% rung spacing -> set to actual percentiles
  - the ladder shape -> informed by where drawdowns actually cluster

Output: median / 75th / 90th percentile drawdown depth, and the
shallow-vs-deep split. That IS the ladder's rung map.
"""

from datetime import datetime, timezone
from fetch_history import fetch_hourly_candles
from backtest import (candles_to_daily, window_ending_at, analyze_window,
                      ENTRY_BAND, entry_triggered)

MS_PER_DAY = 86400000


def measure_entries(daily):
    """
    Walk history. At each CORE entry signal (flat + price <= floor*ENTRY_BAND),
    simulate a lump-sum entry and follow it forward until it recovers to the
    exit target (avg_entry + 80% spread). Record the deepest % below entry it
    reached along the way.

    Returns a list of dicts, one per completed/observed entry.
    """
    results = []
    i = 400
    while i < len(daily):
        price = daily[i]["close"]
        res = analyze_window(window_ending_at(daily, i))
        if res is None:
            i += 1
            continue
        floor = res["range_floor"]
        spread = res["avg_weekly_spread"]

        if not entry_triggered(price, floor):
            i += 1
            continue

        # An entry fires here. Simulate holding until recovery to target.
        entry_price = price
        target = entry_price + 0.80 * spread if spread else entry_price * 1.05
        entry_date = datetime.fromtimestamp(daily[i]["timestamp"]/1000, tz=timezone.utc).date()

        deepest_low = entry_price   # track the lowest price seen while holding
        recovered = False
        days_held = 0
        j = i + 1
        while j < len(daily):
            lo = daily[j]["low"]
            hi = daily[j]["high"]
            if lo < deepest_low:
                deepest_low = lo
            if hi >= target:
                recovered = True
                days_held = (daily[j]["timestamp"] - daily[i]["timestamp"]) / MS_PER_DAY
                break
            j += 1

        # max drawdown below entry, as a positive %
        max_dd_pct = (entry_price - deepest_low) / entry_price * 100.0

        results.append({
            "date": entry_date,
            "entry": entry_price,
            "max_drawdown_pct": max_dd_pct,
            "recovered": recovered,
            "days_held": days_held,
        })

        # skip past this trade so we don't re-trigger on the same dip
        i = (j + 1) if recovered else (i + 30)

    return results


def pctile(sorted_vals, p):
    if not sorted_vals:
        return None
    idx = int(len(sorted_vals) * p / 100)
    idx = min(idx, len(sorted_vals) - 1)
    return sorted_vals[idx]


if __name__ == "__main__":
    print("Loading XLM history ...")
    candles = fetch_hourly_candles(symbol="XLM/USDT", since_year=2019)
    daily = candles_to_daily(candles)
    print(f"{len(daily)} daily candles\n")

    entries = measure_entries(daily)
    n = len(entries)
    print(f"Total CORE entry signals measured: {n}\n")

    # --- Drawdown distribution ---
    dds = sorted(e["max_drawdown_pct"] for e in entries)
    print("=== How deep does XLM fall BELOW ENTRY before recovering? ===")
    print(f"  median (50th):  -{pctile(dds, 50):.1f}%")
    print(f"  75th pctile:    -{pctile(dds, 75):.1f}%")
    print(f"  90th pctile:    -{pctile(dds, 90):.1f}%")
    print(f"  worst seen:     -{max(dds):.1f}%")
    print(f"  shallowest:     -{min(dds):.1f}%")

    # --- Shallow vs deep split (test Gemini's 70-80% claim) ---
    print("\n=== Shallow vs deep (Gemini claimed ~70-80% shallow) ===")
    for threshold in [10, 15, 20, 30]:
        shallow = sum(1 for e in entries if e["max_drawdown_pct"] <= threshold)
        print(f"  drawdown <= {threshold}%:  {shallow}/{n}  ({shallow/n*100:.0f}% of entries)")

    # --- Recovery stats ---
    recovered = [e for e in entries if e["recovered"]]
    print(f"\n=== Recovery ===")
    print(f"  entries that recovered to target: {len(recovered)}/{n} ({len(recovered)/n*100:.0f}%)")
    if recovered:
        held = sorted(e["days_held"] for e in recovered)
        print(f"  median days to recover: {pctile(held, 50):.0f}")
        print(f"  90th pctile days:       {pctile(held, 90):.0f}")
        print(f"  longest recovery:       {max(held):.0f} days")

    print("\n=== THE LADDER MAP: set rungs at the 50th / 75th / 90th percentile depths above. ===")
    print("=== Rung spacing is now DATA, not a guess. ===")