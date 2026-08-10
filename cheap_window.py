"""
cheap_window.py

Ported from B3OS extracted/flip-cheap-window.js
Purpose: find the recurring hour-of-day (UTC) where XLM's daily low
tends to happen. If price consistently bottoms out in the same few
hours each day, that's the "cheap window" - the best time to buy.

Also reports:
  - a consistency percentage (what % of days had their low inside the window)
  - a calendar note (does the low-hour shift between early-month and
    late-month? -- reporting only, does not change the main window)
"""

from datetime import datetime, timezone
from range_percentiles import fetch_xlm_prices

# --- Tunable constants (adjust these to change behavior) ---
WINDOW_MIN_HOURS = 5              # shortest cheap-window length to try
WINDOW_MAX_HOURS = 7             # longest cheap-window length to try
MIN_DAYS_FOR_CALENDAR_SPLIT = 60  # need at least this many days before
                                  # attempting the early/late-month comparison


def find_daily_lows(prices):
    """
    Goes through every price point and figures out, for each calendar day,
    what the lowest price was, what UTC hour it happened at, and what day
    of the month it was.

    Returns a list of dicts: [{"hour": 0-23, "day_of_month": 1-31}, ...]
    -- one entry per day.
    """
    daily_lows = {}  # key: "year-month-day", value: {"low", "hour", "day_of_month"}

    for p in prices:
        # convert the raw millisecond timestamp into a real date/time,
        # explicitly in UTC (matches the JS version's getUTCHours() etc.)
        dt = datetime.fromtimestamp(p["timestamp"] / 1000, tz=timezone.utc)
        day_key = f"{dt.year}-{dt.month}-{dt.day}"

        if day_key not in daily_lows:
            daily_lows[day_key] = {"low": p["value"], "hour": dt.hour, "day_of_month": dt.day}
        elif p["value"] < daily_lows[day_key]["low"]:
            daily_lows[day_key]["low"] = p["value"]
            daily_lows[day_key]["hour"] = dt.hour

    # return the per-day info we need downstream (hour + day-of-month)
    return [{"hour": d["hour"], "day_of_month": d["day_of_month"]} for d in daily_lows.values()]


def find_best_window(hours):
    """
    hours: a list of hour-of-day values (0-23), one per day.

    Tries every window length (5, 6, 7 hours) starting at every possible
    hour (0-23), counts how many days' lows fall inside each, and returns
    whichever window captures the most.

    Windows wrap around midnight correctly (e.g. start 22, length 5 covers
    22, 23, 0, 1, 2).
    """
    # hour_counts[h] = how many days had their low at hour h
    hour_counts = [0] * 24
    for h in hours:
        hour_counts[h] += 1

    best_start = None
    best_length = None
    best_count = -1

    for win_len in range(WINDOW_MIN_HOURS, WINDOW_MAX_HOURS + 1):
        for start in range(24):
            count = 0
            for offset in range(win_len):
                hour = (start + offset) % 24  # % wraps around past midnight
                count += hour_counts[hour]

            if count > best_count:
                best_count = count
                best_start = start
                best_length = win_len

    return {"start": best_start, "length": best_length, "count": best_count}


def most_common_low_hour(day_records):
    """
    Helper for the calendar split. Given a list of day records, returns the
    single hour (0-23) that showed up most often as a daily-low hour.
    Returns None if the list is empty.
    """
    if not day_records:
        return None
    hour_counts = [0] * 24
    for d in day_records:
        hour_counts[d["hour"]] += 1
    # index of the highest count = the most common low-hour
    return hour_counts.index(max(hour_counts))


def analyze_calendar_split(daily_records):
    """
    Reporting only. Compares the most common low-hour in the EARLY part of
    the month (days 1-10) against the LATE part (days 22-29), to see whether
    the cheap window shifts by part-of-month.

    Does NOT change the main window -- it just flags a possible shift so a
    human can decide whether month-timing matters for this asset.
    """
    total_days = len(daily_records)
    if total_days < MIN_DAYS_FOR_CALENDAR_SPLIT:
        return "insufficient data to assess calendar effect"

    early = [d for d in daily_records if 1 <= d["day_of_month"] <= 10]
    late = [d for d in daily_records if 22 <= d["day_of_month"] <= 29]

    if not early or not late:
        return "not enough early-month or late-month days to compare"

    early_hour = most_common_low_hour(early)
    late_hour = most_common_low_hour(late)

    shift = " (no shift observed)" if early_hour == late_hour else " (possible shift observed)"
    return (f"early-month best low hour (UTC): {early_hour}, "
            f"late-month best low hour (UTC): {late_hour}{shift}")


def analyze_cheap_window(prices):
    """
    Ties everything together: finds each day's low hour, finds the best
    window, calculates consistency %, and runs the calendar split check.
    """
    daily_records = find_daily_lows(prices)
    hours = [d["hour"] for d in daily_records]
    window = find_best_window(hours)

    total_days = len(hours)
    consistency_pct = round((window["count"] / total_days) * 100, 2) if total_days > 0 else None

    calendar_note = analyze_calendar_split(daily_records)

    return {
        "cheap_window_start_utc_hour": window["start"],
        "cheap_window_end_utc_hour": (window["start"] + window["length"]) % 24,
        "cheap_window_length_hours": window["length"],
        "cheap_window_consistency_pct": consistency_pct,
        "sample_days": total_days,
        "calendar_note": calendar_note
    }


if __name__ == "__main__":
    prices = fetch_xlm_prices(days=90)
    result = analyze_cheap_window(prices)
    print(result)