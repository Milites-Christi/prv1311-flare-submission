"""
backtest.py

Piece 3 of the backtest: the day-by-day engine.

We build this in SECTIONS, testing each before stacking the next:
  Section 1 (this file, for now): the window-roll -- for any simulated day,
            compute range_floor / range_top / avg_weekly_spread from ONLY the
            trailing 90 days ending on that day. Never looks at the future.
  Section 2: entry check   (coming next)
  Section 3: exit checks    (after that)
  Section 4: scorecard      (last)

THE CARDINAL RULE OF BACKTESTING:
When standing on simulated day X, we may only use data up to and including
day X. Peeking at even one future candle turns the results into fiction.
Every function here is built so it physically cannot see past "today".
"""

from datetime import datetime, timezone
from fetch_history import fetch_hourly_candles

# We reuse the EXACT analysis logic you already built and verified in Step 1.
from range_percentiles import percentile
from weekly_spread import analyze_weekly_spread

# --- Tunable constants ---
LOOKBACK_DAYS = 90                 # trailing window for the range analysis
RANGE_FLOOR_PERCENTILE = 5
RANGE_TOP_PERCENTILE = 95
MS_PER_DAY = 86400000


def candles_to_daily(candles):
    """
    Collapse hourly candles into one summary per calendar day (UTC).

    For each day we keep: the day's timestamp (midnight UTC), open (first
    hour's open), high (max of the day), low (min of the day), close (last
    hour's close). Stepping the backtest daily means ~2,500 days instead of
    ~60,000 hours -- fast, and plenty granular for this strategy.

    Returns a list of daily dicts in chronological order.
    """
    days = {}  # key: "YYYY-MM-DD", value: accumulating daily summary

    for c in candles:
        dt = datetime.fromtimestamp(c["timestamp"] / 1000, tz=timezone.utc)
        day_key = dt.strftime("%Y-%m-%d")

        if day_key not in days:
            # midnight-UTC timestamp for this day, in ms
            day_midnight = int(datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc).timestamp() * 1000)
            days[day_key] = {
                "timestamp": day_midnight,
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],   # will be overwritten by each later hour -> ends as last close
            }
        else:
            d = days[day_key]
            if c["high"] > d["high"]:
                d["high"] = c["high"]
            if c["low"] < d["low"]:
                d["low"] = c["low"]
            d["close"] = c["close"]     # keep updating so it ends on the last hour's close

    # dict preserves insertion order (Python 3.7+), and candles came in
    # chronological order, so this list is already sorted by day.
    return list(days.values())


def window_ending_at(daily, today_index):
    """
    Return the slice of daily candles that make up the trailing LOOKBACK_DAYS
    window ENDING at daily[today_index] (inclusive).

    today_index is the position in the `daily` list we're currently standing on.
    We look BACKWARD from it -- never forward. If there isn't a full 90 days of
    history yet, we return what we have (the caller decides if that's enough).
    """
    today_ts = daily[today_index]["timestamp"]
    window_start_ts = today_ts - (LOOKBACK_DAYS * MS_PER_DAY)

    window = []
    # walk backward from today until we fall off the 90-day edge
    i = today_index
    while i >= 0 and daily[i]["timestamp"] >= window_start_ts:
        window.append(daily[i])
        i -= 1

    window.reverse()  # put back in chronological order
    return window


def analyze_window(window):
    """
    Given a trailing window of daily candles, compute the three numbers the
    strategy needs -- using the SAME logic as your Step 1 files:
      range_floor  = 5th percentile of daily closes
      range_top    = 95th percentile of daily closes
      avg_weekly_spread = average weekly high-low spread

    Returns None if the window is too short to be meaningful.
    """
    if len(window) < 30:   # need a reasonable amount of history to be useful
        return None

    closes = sorted(c["close"] for c in window)
    range_floor = percentile(closes, RANGE_FLOOR_PERCENTILE)
    range_top = percentile(closes, RANGE_TOP_PERCENTILE)

    # reuse the weekly-spread analyzer; it expects {"value","timestamp"} shape
    price_objs = [{"value": c["close"], "timestamp": c["timestamp"]} for c in window]
    spread_result = analyze_weekly_spread(price_objs)

    return {
        "range_floor": range_floor,
        "range_top": range_top,
        "avg_weekly_spread": spread_result["avg_weekly_spread"],
        "window_days": len(window),
    }

# ============================================================
# SECTION 2: the entry check.
# One question per simulated day: should CORE deploy?
# Rule (locked): CORE is flat AND price <= range_floor * ENTRY_BAND
# ============================================================

# --- Tunable constant for entry ---
ENTRY_BAND = 1.05   # buy when price <= range_floor * 1.05 (within 5% above the floor)


def entry_triggered(price, range_floor):
    """
    Returns True if `price` is at or below the entry band (range_floor * ENTRY_BAND).

    This is the whole discipline in one line: only buy when price is genuinely
    near the bottom of its own recent range -- not just 'kind of low'. The 1.05
    band means we don't demand a perfect bottom-tick; we catch dips that get
    within 5% of the floor, which fills far more reliably than 'price <= floor'
    exactly (the exact 5th-percentile floor is only touched ~5% of the time).
    """
    if range_floor is None:
        return False
    return price <= range_floor * ENTRY_BAND

# ============================================================
# SECTION 3: the day-by-day engine.
# Walks history one day at a time and RUNS the strategy, wiring
# the state tracker (Piece 2) to the window/entry logic (Sec 1-2).
# Now state-gated: CORE only enters when FLAT, so the 46 phantom
# signals collapse into real entries.
# ============================================================

import portfolio_state as ps   # our scoreboard from Piece 2

# --- Tunable exit constants ---
TACTICAL_TARGET_PCT = 0.07     # TACTICAL sells at +7% from its entry


def run_backtest(daily, start_index, end_index, verbose=False):
    """
    Walk daily[start_index : end_index], running the full strategy.
    Returns the final state plus a per-day log for the scorecard.

    DAILY SEQUENCE (locked rules, in order):
      1. Roll 90-day window ending today -> floor, top, spread
      2. CORE entry:  if flat AND price <= floor*ENTRY_BAND -> deploy 10
      3. CORE exits (if holding), FIRST to fire wins:
           a. price >= avg_entry + 0.80*spread -> sell all (target)
           b. price >  range_top               -> sell all (opportunistic)
      4. CORE reserve: if holding AND price < floor AND reserve unused -> deploy
      5. else -> HOLD. Underwater waits. No stop-loss. Ever.
      6. TACTICAL entry: if flat AND same trigger -> deploy 1
      7. TACTICAL exit:  if holding AND price >= entry*1.07 -> sell
      8. record daily snapshot, advance
    """
    state = ps.new_state()
    log = []

    for i in range(start_index, end_index):
        today = daily[i]
        price = today["close"]
        date = datetime.fromtimestamp(today["timestamp"] / 1000, tz=timezone.utc).date()

        window = window_ending_at(daily, i)
        result = analyze_window(window)
        if result is None:
            continue
        floor = result["range_floor"]
        top = result["range_top"]
        spread = result["avg_weekly_spread"]

        action = None

        # ---------------- CORE ----------------
        if not state["core_holding"]:
            if entry_triggered(price, floor):
                ps.core_deploy(state, price, ps.CORE_MAX_SLICES)
                action = f"CORE ENTRY 10 slices @ ${price:.4f}"
        else:
            avg_entry = ps.core_average_entry(state)
            target = avg_entry + 0.80 * spread if (avg_entry and spread) else None

            # PROFIT GUARD: no exit may fire below average entry. Never cut at
            # a loss. In a bear the rolling range_top decays below our cost;
            # without this guard the range_top exit sells into a loss (this is
            # what produced the -$10,192 / -$591 / -$1,966 sells).
            in_profit = avg_entry is not None and price > avg_entry

            if in_profit and target is not None and price >= target:
                state, profit = ps.core_sell_all(state, price)
                action = f"CORE EXIT (target) @ ${price:.4f}  profit ${profit:.2f}"
            elif in_profit and price > top:
                state, profit = ps.core_sell_all(state, price)
                action = f"CORE EXIT (range_top) @ ${price:.4f}  profit ${profit:.2f}"
            elif price < floor and not state["core_reserve_used"]:
                ps.core_deploy_reserve(state, price)
                action = f"CORE RESERVE @ ${price:.4f}  new avg ${ps.core_average_entry(state):.4f}"
            # else: HOLD. underwater waits. no stop-loss. EVER.

        # -------------- TACTICAL --------------
        if not state["tac_holding"]:
            if entry_triggered(price, floor):
                ps.tac_deploy(state, price)
                action = (action + f" | TAC ENTRY @ ${price:.4f}") if action else f"TAC ENTRY @ ${price:.4f}"
        else:
            tac_target = state["tac_entry_price"] * (1 + TACTICAL_TARGET_PCT)
            if price >= tac_target:
                state, tprofit = ps.tac_sell(state, price)
                tac_str = f"TAC EXIT (+7%) @ ${price:.4f}  profit ${tprofit:.2f}"
                action = (action + " | " + tac_str) if action else tac_str

        # 8. daily snapshot for the scorecard
        core_value = state["core_units"] * price
        tac_value = state["tac_units"] * price
        open_unrealized = (core_value - state["core_usd_in"]) + (tac_value - state["tac_usd_in"])

        log.append({
            "date": date,
            "price": price,
            "treasury": state["treasury"],
            "open_unrealized": open_unrealized,
            "core_holding": state["core_holding"],
            "core_avg_entry": ps.core_average_entry(state),
            "action": action,
        })

        if verbose and action:
            print(f"  {date}  ${price:.4f}  | {action}")

    return state, log

# ============================================================
# TACTICAL-FOCUSED TEST: Dec 2021 -> Nov 23 2024.
# Watch whether the "one slice" actually cycles during the long
# ranging bear, or freezes at its entry like CORE.
# ============================================================
if __name__ == "__main__":
    from datetime import date as _date

    print("Fetching + collapsing to daily candles ...")
    candles = fetch_hourly_candles(symbol="XLM/USDT", since_year=2019)
    daily = candles_to_daily(candles)

    # Find the index range for Dec 2021 -> Nov 23 2024
    def index_on_or_after(target):
        for idx, d in enumerate(daily):
            dd = datetime.fromtimestamp(d["timestamp"] / 1000, tz=timezone.utc).date()
            if dd >= target:
                return idx
        return len(daily) - 1

    start_index = index_on_or_after(_date(2021, 12, 1))
    end_index = index_on_or_after(_date(2024, 11, 24))
    print(f"Testing {daily[start_index]['timestamp']} .. window "
          f"[{start_index}:{end_index}]  ({end_index - start_index} days)\n")

    # Run the full engine, capturing the log
    state, log = run_backtest(daily, start_index, end_index, verbose=False)

    # --- Pull out TACTICAL's story specifically ---
    print("=== TACTICAL activity (entries and +7% exits) ===")
    tac_events = [row for row in log if row["action"] and "TAC" in row["action"]]
    if tac_events:
        for row in tac_events:
            # isolate just the TAC part of the action string
            parts = [p.strip() for p in row["action"].split("|") if "TAC" in p]
            print(f"  {row['date']}  ${row['price']:.4f}  | {' '.join(parts)}")
    else:
        print("  (no TACTICAL activity at all in this window)")

    tac_entries = sum(1 for r in tac_events if "TAC ENTRY" in r["action"])
    tac_exits = sum(1 for r in tac_events if "TAC EXIT" in r["action"])
    print(f"\n  TACTICAL entries: {tac_entries}   TACTICAL +7% flips completed: {tac_exits}")

    # --- CORE story for context ---
    print("\n=== CORE activity ===")
    core_events = [row for row in log if row["action"] and "CORE" in row["action"]]
    if core_events:
        for row in core_events:
            parts = [p.strip() for p in row["action"].split("|") if "CORE" in p]
            print(f"  {row['date']}  ${row['price']:.4f}  | {' '.join(parts)}")
    else:
        print("  (no CORE activity in this window)")

    # --- Bottom line ---
    print(f"\n=== Totals for Dec 2021 -> Nov 23 2024 ===")
    print(f"  Realized treasury (all booked profit): ${state['treasury']:.2f}")
    print(f"  CORE still holding at end?: {state['core_holding']}")
    if state['core_holding']:
        print(f"    holding {state['core_slices']} slices, avg entry ${ps.core_average_entry(state):.4f}")
    print(f"  TACTICAL still holding at end?: {state['tac_holding']}")
    if state['tac_holding']:
        print(f"    stuck since entry @ ${state['tac_entry_price']:.4f}")

    # How much of the window did TACTICAL spend FROZEN (holding, waiting)?
    tac_holding_days = sum(1 for r in log if r["core_holding"] is not None and r.get("action") is None and False)
    frozen_days = sum(1 for r in log if r["price"] and state and False)  # placeholder
    print("\n=== Question: did the one slice DO ITS JOB, or freeze? ===")