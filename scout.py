"""
scout.py

The floor-SLOPE regime scout for CORE (v2).

v1 compared floor-now to floor-180-days-ago -- a two-point endpoint check
that lagged badly (missed the 2022 bear) and false-triggered on recoveries
(blocked the entire 2024 bull). Both failures came from comparing two
far-apart points that can straddle a bottom and misread direction.

v2 measures the floor's SLOPE over a shorter recent window: sample the
90-day range_floor at several recent points and check whether it's
CONSISTENTLY DECLINING right now. That catches a *developing* bear early
without mistaking a recovery-off-lows for decay.

  Floor sloping DOWN steadily -> range is decaying (bear forming) -> HOLD
  Floor flat / rising / choppy -> healthy or recovering -> DEPLOY
"""

from backtest import window_ending_at, analyze_window

MS_PER_DAY = 86400000

# --- Tunable knobs ---
SCOUT_SAMPLE_DAYS = 60        # measure the floor's direction over the last ~60 days
SCOUT_SAMPLES = 4            # how many points to sample across that window
SCOUT_DECLINE_PCT = 0.10     # total decline across the window to count as "falling"


def _floor_at(daily, i):
    """The 90-day range_floor as of index i (or None)."""
    res = analyze_window(window_ending_at(daily, i))
    if res is None:
        return None
    return res["range_floor"]


def floor_is_falling(daily, i):
    """
    Return True if the range_floor has been CONSISTENTLY DECLINING over the
    last SCOUT_SAMPLE_DAYS (a real downtrend of the structure), else False.

    Method: sample the floor at SCOUT_SAMPLES evenly-spaced points across the
    recent window. Require BOTH:
      (a) each sample is <= the previous one (monotonic decline, no bounce), and
      (b) the total drop from oldest to newest exceeds SCOUT_DECLINE_PCT.
    Requiring monotonic decline is what stops false-triggers on a V-shaped
    recovery (which drops then rises -- not monotonic -> not flagged).

    Fails open (returns False) if there isn't enough history to sample.
    """
    step_days = SCOUT_SAMPLE_DAYS / (SCOUT_SAMPLES - 1)

    # collect floor samples from oldest to newest
    samples = []
    for k in range(SCOUT_SAMPLES):
        days_back = SCOUT_SAMPLE_DAYS - k * step_days
        target_ts = daily[i]["timestamp"] - days_back * MS_PER_DAY
        # find the index at/after that timestamp
        j = i
        while j >= 0 and daily[j]["timestamp"] > target_ts:
            j -= 1
        if j < 400:                 # not enough history -> fail open
            return False
        f = _floor_at(daily, j)
        if f is None or f <= 0:
            return False
        samples.append(f)

    # (a) monotonic decline: every step down (allow tiny noise via <= with margin)
    monotonic_down = all(samples[k] <= samples[k-1] * 1.005 for k in range(1, len(samples)))
    if not monotonic_down:
        return False

    # (b) total decline exceeds threshold
    total_decline = (samples[0] - samples[-1]) / samples[0]
    return total_decline > SCOUT_DECLINE_PCT