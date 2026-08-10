"""
================================================================================
PROJECT: Prv1311 — Range Percentiles (statistical range floor/top)
FILE: range_percentiles.py
================================================================================
Ported from Accum-Flip 90-Day Analysis "Calculate Price Range Statistics."

WHAT IT DOES: from a price history, computes the STATISTICAL range —
    range_floor = 5th percentile of closes  (genuinely cheap, not a spike low)
    range_top   = 95th percentile of closes (genuinely expensive, not a spike high)
Using percentiles instead of min/max means one outlier wick doesn't distort the
range. Where current price sits between these tells you how cheap/expensive it is
right now relative to its own normal range.

FROM THE SOURCE (drives, for later):
    range_floor -> reserve-deployment trigger (buy the genuinely-cheap 5th pct)
    range_top   -> opportunistic exit (sell into the 95th pct)

WHO USES IT (plug point): a fleet can gate entries on "is price near its
statistical floor?" via in_lower_range() — buy genuinely cheap, not just dipped.
Stacks with the regime + OBI gates. CORE will use range_floor as its reserve
trigger and range_top as an exit when it's built.

PURE FUNCTIONS: range_percentiles(prices) + in_lower_range(price, floor, top).
CONSTANTS: RANGE_FLOOR_PERCENTILE=5, RANGE_TOP_PERCENTILE=95.
================================================================================
"""

RANGE_FLOOR_PERCENTILE = 5    # 5th pct of closes -> cheap / reserve-deploy trigger
RANGE_TOP_PERCENTILE = 95     # 95th pct of closes -> expensive / opportunistic exit

# how close to the floor still counts as "in the lower range" for the entry gate.
# 0.20 = within the bottom 20% of the floor->top band. Operator-adjustable.
LOWER_RANGE_FRACTION = 0.20


def _percentile(sorted_vals, pct):
    """Linear-interpolated percentile. sorted_vals must be ascending."""
    n = len(sorted_vals)
    if n == 0:
        return None
    idx = (pct / 100.0) * (n - 1)
    lower = int(idx)
    upper = lower + 1 if idx > lower else lower
    if lower == upper:
        return sorted_vals[lower]
    frac = idx - lower
    return sorted_vals[lower] + (sorted_vals[upper] - sorted_vals[lower]) * frac


def range_percentiles(prices):
    """PURE FUNCTION. prices = list of closes (any order).
    Returns {range_floor, range_top, data_points}."""
    out = {'range_floor': None, 'range_top': None, 'data_points': len(prices)}
    if not prices:
        return out
    s = sorted(prices)
    out['range_floor'] = _percentile(s, RANGE_FLOOR_PERCENTILE)
    out['range_top'] = _percentile(s, RANGE_TOP_PERCENTILE)
    return out


def in_lower_range(price, range_floor, range_top, fraction=LOWER_RANGE_FRACTION):
    """PURE FUNCTION. True if price sits in the bottom `fraction` of the
    floor->top band — i.e. genuinely cheap within its statistical range.
    Fleets call this as an entry-quality gate. Fails closed (False) on bad data.

    threshold = floor + fraction * (top - floor)
    e.g. floor=$1, top=$2, fraction=0.20 -> threshold=$1.20; price<=1.20 passes."""
    if range_floor is None or range_top is None:
        return False
    if range_top <= range_floor:
        return False
    threshold = range_floor + fraction * (range_top - range_floor)
    return price <= threshold


if __name__ == "__main__":
    # synthetic 100-day series oscillating 100..200 with a couple of spikes
    closes = [100 + (i % 50) for i in range(100)]
    closes[7] = 500    # outlier high — percentile should ignore it
    closes[42] = 10    # outlier low
    r = range_percentiles(closes)
    print("range:", r)
    for p in [r['range_floor'], (r['range_floor']+r['range_top'])/2, r['range_top']]:
        print(f"  price {p:.2f} -> in_lower_range={in_lower_range(p, r['range_floor'], r['range_top'])}")