"""
================================================================================
PROJECT: Prv1311 — Anomaly Gate
FILE: anomaly_gate.py
================================================================================
Pure functions, no side effects, no network calls -- caller supplies daily
closes (oldest -> newest). This is the real fix for the HFT incident: a pure
%-pullback gate can't tell "routine 5% chop" apart from "profit-taking off a
blow-off pump" -- both look like a dip from a recent high. HFT ran ~3.5x in 5
days (Aug1 0.0087 -> Aug6 0.0302 close) then gave most of it back in a single
day. The catch-band (see rider_team.py/scavengers.py) catches the CRASH once
it's deep enough; this gate is meant to catch the PUMP itself, before any
dip off it ever looks tradeable.

MATH (v2 -- see BUG note below, this replaced a real defect):
    r7      = ln( max(closes over the last 7 days) / P_t-7 )  -- run-up, not
              current position. peak is the max of daily CLOSES in the most
              recent 7-day window (intraday highs are too wick-noisy); P_t-7
              is the close 7 days before that window started.
    sigma_d = stdev of the last 30 daily log returns  -- volatility scale
    sigma_7 = sigma_d * sqrt(7)                       -- scaled to a 7-day horizon
    Z       = r7 / sigma_7                            -- NO drift term. Subtracting
              a 30-day mean return would bias Z DOWNWARD exactly while a pump is
              still running (the mean itself gets dragged up by the same pump),
              which would blunt the one signal that matters most. Deliberately a
              raw ratio, not a textbook z-score.

VETO:    Z >= 2.5  OR  r7 >= 0.55
    The absolute r7 floor is load-bearing, not decorative: on a high-volatility
    name, sigma_7 inflates right along with the move, so Z alone can miss it.
    HFT's r7 (peak-based) is 1.24 (0.0087 -> 0.0302 in 6 days) -- the floor
    catches that regardless of what sigma says.

BUG FIXED HERE: the original v1 used r7 = ln(P_t / P_t-7) -- where the price
IS, not how far it RAN. That measures the wrong thing the instant a pump pops:
HFT's actual crash day had ln(0.0122/0.0087) = 0.34, under the veto floor, and
Z missed too (the violent daily returns from the crash itself inflate sigma_d).
Net effect of v1: the gate went quiet on a popped pump, exactly when entering
off the "dip" is worst. v2's peak-based r7 keeps the run visible in the window
even after price has already round-tripped back down.

CAUTION (logged, does not block): 0.18 <= r7 < 0.55

UNLOCK (informational -- see check_unlock): r7 < 0.15 for the last 5 consecutive
daily closes AND current price >= mean(last 30 closes). Uses the SAME peak-based
r7 as the main gate -- this has to, not just for consistency: the whole point of
requiring 5 CONSECUTIVE clean days (not just one) is that the quarantine has to
survive the unwind. A point-in-time r7 clears the moment price round-trips back
down, which is precisely the v1 bug above; the peak stays "in view" inside the
7-day window for days after the crash, so release only happens once the run has
genuinely rolled out of the window and stayed out. Not wired as a second gate --
exposed for inspection/backtesting. The main veto is recomputed fresh every
cycle from live data; there is no persisted "quarantine" state anywhere.
================================================================================
"""

import math
import statistics
from dataclasses import dataclass, field

Z_VETO_THRESHOLD = 2.5
R7_VETO_FLOOR = 0.55
CAUTION_LOW = 0.18
UNLOCK_R7_THRESHOLD = 0.15
UNLOCK_CONSECUTIVE_DAYS = 5
SIGMA_WINDOW = 30
R7_LOOKBACK_DAYS = 7
MIN_CLOSES_REQUIRED = SIGMA_WINDOW + 1  # need 30 returns -> 31 closes


@dataclass
class AnomalyDecision:
    veto: bool
    reason: str            # 'anomaly_veto' | 'caution' | 'clear' | 'insufficient_data'
    r7: float = None
    sigma_d: float = None
    sigma_7: float = None
    z: float = None
    detail: dict = field(default_factory=dict)

    def __bool__(self):
        return not self.veto


def _log_returns(closes):
    out = []
    for i in range(1, len(closes)):
        prev, cur = closes[i - 1], closes[i]
        if prev is None or cur is None or prev <= 0 or cur <= 0:
            continue
        out.append(math.log(cur / prev))
    return out


def _r7_peak_over_anchor(daily_closes, i):
    """r7 as of day index i: ln(max(closes[i-6..i]) / closes[i-7]) -- the run-up
    into and through the most recent 7-day window, not where price sits right
    now. None if there isn't 7 days of history before index i."""
    j = i - R7_LOOKBACK_DAYS
    if j < 0:
        return None
    anchor = daily_closes[j]
    window = daily_closes[j + 1: i + 1]
    if not anchor or anchor <= 0 or not window:
        return None
    peak = max(window)
    if not peak or peak <= 0:
        return None
    return math.log(peak / anchor)


def check_anomaly(daily_closes):
    """Pure function. daily_closes = list of closes, oldest -> newest.
    Returns an AnomalyDecision with every intermediate value populated so
    nothing about the call is a black box."""
    try:
        n = len(daily_closes)
        if n < MIN_CLOSES_REQUIRED:
            return AnomalyDecision(False, 'insufficient_data',
                                   detail={'have': n, 'need': MIN_CLOSES_REQUIRED})

        r7 = _r7_peak_over_anchor(daily_closes, n - 1)
        if r7 is None:
            return AnomalyDecision(False, 'insufficient_data',
                                   detail={'reason': 'non-positive price in window'})

        returns = _log_returns(daily_closes)
        last_30 = returns[-SIGMA_WINDOW:]
        if len(last_30) < SIGMA_WINDOW:
            return AnomalyDecision(False, 'insufficient_data',
                                   detail={'have_returns': len(last_30), 'need': SIGMA_WINDOW})
        sigma_d = statistics.stdev(last_30)
        sigma_7 = sigma_d * math.sqrt(R7_LOOKBACK_DAYS)
        z = (r7 / sigma_7) if sigma_7 > 0 else None

        detail = {'p_t7': daily_closes[n - 1 - R7_LOOKBACK_DAYS],
                  'peak_7d': max(daily_closes[n - R7_LOOKBACK_DAYS:])}

        if (z is not None and z >= Z_VETO_THRESHOLD) or r7 >= R7_VETO_FLOOR:
            return AnomalyDecision(True, 'anomaly_veto', r7, sigma_d, sigma_7, z, detail)
        if CAUTION_LOW <= r7 < R7_VETO_FLOOR:
            return AnomalyDecision(False, 'caution', r7, sigma_d, sigma_7, z, detail)
        return AnomalyDecision(False, 'clear', r7, sigma_d, sigma_7, z, detail)

    except Exception as e:  # noqa: BLE001 -- pure function, fail safe rather than raise
        return AnomalyDecision(False, 'insufficient_data', detail={'error': str(e)})


def check_unlock(daily_closes):
    """Pure function, informational only -- not wired as a second gate.
    True if the last UNLOCK_CONSECUTIVE_DAYS daily closes each show the SAME
    peak-based r7 below UNLOCK_R7_THRESHOLD AND current price is at/above the
    30-day mean (cooled down AND stabilized, not just mathematically rolled off
    the veto window while still falling). Must use the same r7 definition as
    check_anomaly -- see module docstring for why."""
    try:
        n = len(daily_closes)
        if n < SIGMA_WINDOW or n < R7_LOOKBACK_DAYS + UNLOCK_CONSECUTIVE_DAYS:
            return False
        for i in range(n - UNLOCK_CONSECUTIVE_DAYS, n):
            r7_i = _r7_peak_over_anchor(daily_closes, i)
            if r7_i is None or r7_i >= UNLOCK_R7_THRESHOLD:
                return False
        mean_30d = sum(daily_closes[-SIGMA_WINDOW:]) / SIGMA_WINDOW
        return daily_closes[-1] >= mean_30d
    except Exception:
        return False
