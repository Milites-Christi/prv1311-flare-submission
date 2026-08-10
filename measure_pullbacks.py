"""
measure_pullbacks.py

A throwaway measurement tool -- NOT part of the strategy. Its only job is to
answer, from XLM's real 6-year history, the questions two AIs were guessing at:

  1. What is XLM's actual daily move, and how much does it vary by regime?
     (Challenge 1: is a fixed 5% trigger sane across regimes?)
  2. Where does range_floor actually sit relative to price? (Challenge 2)
  3. When a -X% pullback from a recent high happens, how often does it then
     bounce +7% vs. keep falling? (Challenge 3: how often does the Rider get bagged?)

We set the Rider's trigger on THIS, not on anyone's assertion.
"""

from datetime import datetime, timezone
from fetch_history import fetch_hourly_candles
from backtest import candles_to_daily, window_ending_at, analyze_window

MS_PER_DAY = 86400000


def daily_moves(daily):
    """Percent change close-to-close, per day. The raw 'daily noise'."""
    moves = []
    for i in range(1, len(daily)):
        prev = daily[i - 1]["close"]
        curr = daily[i]["close"]
        if prev > 0:
            moves.append((curr - prev) / prev * 100.0)
    return moves


def pct_stats(label, values):
    """Print simple distribution stats for a list of numbers."""
    if not values:
        print(f"  {label}: no data")
        return
    vals = sorted(values)
    n = len(vals)
    mean = sum(vals) / n
    median = vals[n // 2]
    p90 = vals[int(n * 0.90)]
    p10 = vals[int(n * 0.10)]
    print(f"  {label}: mean {mean:.2f}%  median {median:.2f}%  "
          f"10th {p10:.2f}%  90th {p90:.2f}%  (n={n})")


def rolling_high(daily, i, lookback_days=7):
    """Highest close in the trailing `lookback_days` ending at index i."""
    start_ts = daily[i]["timestamp"] - lookback_days * MS_PER_DAY
    hi = daily[i]["close"]
    j = i
    while j >= 0 and daily[j]["timestamp"] >= start_ts:
        if daily[j]["close"] > hi:
            hi = daily[j]["close"]
        j -= 1
    return hi


def measure_pullback_outcomes(daily, trigger_pct, target_pct=7.0, floor_guard=True, max_wait_days=30):
    """
    For every day where price drops `trigger_pct`% below its 7-day high
    (and, if floor_guard, is still >= range_floor), simulate the Rider's
    bet: does price reach +target_pct% within max_wait_days (a WIN / clean
    flip) or not (a BAG -- the thing we're counting)?

    Returns (wins, bags, skipped_below_floor).
    """
    wins = 0
    bags = 0
    skipped = 0

    i = 400  # start where a full 90-day window exists
    while i < len(daily):
        price = daily[i]["close"]
        hi7 = rolling_high(daily, i, 7)
        if hi7 <= 0:
            i += 1
            continue

        drop = (hi7 - price) / hi7 * 100.0  # how far below the 7-day high

        # did a pullback of at least trigger_pct happen?
        if drop >= trigger_pct:
            # floor guard: only "buy" if still in the healthy part of the range
            if floor_guard:
                res = analyze_window(window_ending_at(daily, i))
                if res and res["range_floor"] and price < res["range_floor"]:
                    skipped += 1
                    i += 1
                    continue

            # simulate: does it hit +target_pct% within max_wait_days?
            entry = price
            target = entry * (1 + target_pct / 100.0)
            hit = False
            j = i + 1
            end_ts = daily[i]["timestamp"] + max_wait_days * MS_PER_DAY
            while j < len(daily) and daily[j]["timestamp"] <= end_ts:
                if daily[j]["high"] >= target:
                    hit = True
                    break
                j += 1

            if hit:
                wins += 1
            else:
                bags += 1

            # jump past this episode so we don't double-count the same dip
            i = j + 1
        else:
            i += 1

    return wins, bags, skipped


if __name__ == "__main__":
    print("Loading XLM history ...")
    candles = fetch_hourly_candles(symbol="XLM/USDT", since_year=2019)
    daily = candles_to_daily(candles)
    print(f"{len(daily)} daily candles\n")

    # ---- Challenge 1: real daily noise, whole history + by regime ----
    print("=== Challenge 1: XLM daily move (close-to-close) ===")
    all_moves = daily_moves(daily)
    abs_moves = [abs(m) for m in all_moves]
    pct_stats("Whole history (abs move)", abs_moves)

    # crude regime split by year to show variation
    print("\n  By year (abs daily move):")
    for year in range(2020, 2026):
        yr = [abs((daily[i]["close"] - daily[i-1]["close"]) / daily[i-1]["close"] * 100.0)
              for i in range(1, len(daily))
              if datetime.fromtimestamp(daily[i]["timestamp"]/1000, tz=timezone.utc).year == year
              and daily[i-1]["close"] > 0]
        pct_stats(f"    {year}", yr)

    # ---- Challenge 3: pullback -> win or bag, at several trigger depths ----
    print("\n=== Challenge 3: of pullbacks that fire, how many WIN (+7%) vs BAG? ===")
    print("(win = hit +7% within 30 days; bag = didn't. floor guard ON.)\n")
    for trig in [3.0, 4.0, 5.0, 6.0, 8.0]:
        wins, bags, skipped = measure_pullback_outcomes(daily, trig)
        total = wins + bags
        if total > 0:
            win_rate = wins / total * 100.0
            print(f"  trigger -{trig:.0f}%:  {wins} wins / {bags} bags  "
                  f"({win_rate:.0f}% win rate)   [{skipped} skipped: below floor]")
        else:
            print(f"  trigger -{trig:.0f}%:  never fired")

    print("\n=== Read: which trigger depth gives the best win rate WITHOUT firing so rarely it's useless? ===")