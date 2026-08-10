"""
================================================================================
PROJECT: Prv1311 — EWMA Volatility (the shared volatility helper)
FILE: ewma_volatility.py
================================================================================
THIS IS A PIECE — a shared volatility function engines import. Ported from
War-Room Grid-BOT "Process Returns" (proven on live BTC).

WHAT IT DOES: EWMA (exponentially-weighted) volatility of a returns series.
Recent returns weighted more heavily. Returns ewma_vol and a suggested spacing/
risk distance (= vol) that other pieces use to size dynamically instead of with
hardcoded percentages.

WHO USES IT (plug points):
  - bot_sizing: feed suggested_spacing_pct as max_adverse_pct so a never-cut
    fleet's synthetic risk distance scales with the asset's real volatility
    (calm asset -> tight, choppy asset -> wide) instead of a flat 10%.
  - EWMA buy-zone: same vol drives band width.
  - future grid engine: rung spacing.

Preserves the seeded-variance approach (seed from first min(5,n) returns, not 0).
CONSTANTS: lambda=0.94 (overridable), min sample n>=5.

Includes returns_from_prices() so callers with a price series don't re-derive it.
================================================================================
"""

import math

DEFAULT_LAMBDA = 0.94


def returns_from_prices(prices):
    """Convenience: simple returns from a price series (oldest->newest)."""
    return [(prices[i] - prices[i - 1]) / prices[i - 1]
            for i in range(1, len(prices)) if prices[i - 1] != 0]


def ewma_volatility(returns, lam=DEFAULT_LAMBDA):
    """PURE FUNCTION. returns = list of simple returns.
    Returns {ewma_vol, suggested_spacing_pct, sample_size}.
    suggested_spacing_pct is the vol expressed as a fraction (e.g. 0.037 = 3.7%),
    usable directly as max_adverse_pct or band width."""
    r = returns if isinstance(returns, list) else []
    n = len(r)
    if n < 5:
        return {'ewma_vol': 0.0, 'suggested_spacing_pct': 0.0, 'sample_size': n}

    # seeded variance from first min(5,n) returns (not 0)
    seed_n = min(5, n)
    s_mean = sum(r[:seed_n]) / seed_n
    v = sum((r[i] - s_mean) ** 2 for i in range(seed_n)) / seed_n

    # EWMA recursion over full series
    for x in r:
        v = lam * v + (1 - lam) * x * x
    vol = math.sqrt(v)

    return {'ewma_vol': round(vol, 8),
            'suggested_spacing_pct': round(vol, 8),
            'sample_size': n}


if __name__ == "__main__":
    # sanity: a calm series vs a choppy one
    calm = [0.001, -0.001, 0.0005, -0.0008, 0.0012, -0.0003, 0.0007, -0.0009]
    choppy = [0.05, -0.06, 0.04, -0.07, 0.08, -0.05, 0.06, -0.09]
    print("calm   :", ewma_volatility(calm))
    print("choppy :", ewma_volatility(choppy))
    print("from prices:", ewma_volatility(returns_from_prices([100, 101, 99, 102, 98, 103])))