"""
trend_filter.py

The regime gate. One job: tell the engines whether the long-term trend is
UP (deploy) or DOWN (stand aside in cash).

A moving average is just the average close over the last N days, recomputed
each day. The 200-day MA is slow and smooth -- it ignores weekly wiggles and
tracks the big tide. Price above it = uptrend; below it = downtrend.

This is the DUMB baseline fix for the bear-freeze. We measure how much it
helps before considering anything smarter (scout / pattern-match / EKF).
"""

from datetime import timezone
MS_PER_DAY = 86400000

# --- Tunable ---
TREND_MA_DAYS = 200   # lookback for the long moving average


def trend_is_up(daily, i, ma_days=TREND_MA_DAYS):
    """
    Return True if today's price is at or above the trailing ma_days average
    (uptrend -> engines may deploy). False if below (downtrend -> stand aside).

    If there isn't enough history yet to form the average, default to True
    (don't block deployment on insufficient data -- fail open, since the
    early history is the 2019-2020 base, not a bear to protect against).
    """
    today_ts = daily[i]["timestamp"]
    start_ts = today_ts - ma_days * MS_PER_DAY

    closes = []
    j = i
    while j >= 0 and daily[j]["timestamp"] >= start_ts:
        closes.append(daily[j]["close"])
        j -= 1

    if len(closes) < ma_days // 2:   # not enough history -> fail open
        return True

    ma = sum(closes) / len(closes)
    return daily[i]["close"] >= ma