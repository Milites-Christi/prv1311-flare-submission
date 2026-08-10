"""
range_percentiles.py

Ported from B3OS extracted/flip-range-percentiles.js
Purpose: find the 90-day price range using the 5th and 95th percentile
of closing prices. This tells us:
  - range_floor  -> price level that triggers the dip-reserve buy
  - range_top    -> price level that triggers an opportunistic exit
"""

import os
import math
import requests

# --- Tunable constants (adjust these to change behavior) ---
RANGE_FLOOR_PERCENTILE = 5    # 5th percentile of closes -> dip-reserve trigger
RANGE_TOP_PERCENTILE = 95     # 95th percentile of closes -> opportunistic exit


def percentile(sorted_values, pct):
    """
    Finds the value at a given percentile in an already-sorted list.
    Example: percentile(data, 5) finds the 5th percentile.

    If the percentile falls 'between' two data points, it blends them
    proportionally (linear interpolation) -- mirrors the JS version exactly.
    """
    if not sorted_values:
        return None

    idx = (pct / 100) * (len(sorted_values) - 1)
    lower = math.floor(idx)   # round down to nearest index
    upper = math.ceil(idx)    # round up to nearest index

    # if the percentile lands exactly on a data point, just return it
    if lower == upper:
        return sorted_values[lower]

    # otherwise blend the two surrounding points proportionally
    frac = idx - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * frac


def analyze_range(prices):
    """
    prices: a list of dicts, each shaped like {"value": 0.123, "timestamp": 1234567890000}
    (timestamp is in milliseconds, same as the JS version)

    Returns: range_floor, range_top, sample_days, and data_points.
    """
    closes = sorted(p["value"] for p in prices)

    range_floor = percentile(closes, RANGE_FLOOR_PERCENTILE)
    range_top = percentile(closes, RANGE_TOP_PERCENTILE)

    sample_days = None
    if len(prices) > 1:
        first_ts = prices[0]["timestamp"]
        last_ts = prices[-1]["timestamp"]
        sample_days = round((last_ts - first_ts) / 86400000, 2)

    return {
        "range_floor": range_floor,
        "range_top": range_top,
        "sample_days": sample_days,
        "data_points": len(prices)
    }


def fetch_xlm_prices(days=90):
    """
    Calls CoinGecko's API to get hourly XLM (Stellar) prices for the
    last `days` days. Returns a list of {"value": ..., "timestamp": ...}
    dicts, matching the shape the JS version expected.

    This function is the SHARED price fetch -- other files import it from
    here rather than duplicating it.
    """
    # Read the API key from the environment. Fail with a clear message if
    # it isn't set (verify-don't-trust: don't assume the key is there).
    api_key = os.environ.get("COINGECKO_API_KEY")
    if not api_key:
        raise SystemExit(
            "COINGECKO_API_KEY not set. Set it with:\n"
            "  setx COINGECKO_API_KEY your_key_here\n"
            "then open a NEW terminal so it takes effect."
        )

    url = "https://api.coingecko.com/api/v3/coins/stellar/market_chart"
    params = {
        "vs_currency": "usd",
        "days": days,
        "x_cg_demo_api_key": api_key
    }

    response = requests.get(url, params=params)
    response.raise_for_status()  # crashes loudly if the request failed
    data = response.json()

    # CoinGecko returns prices as [timestamp, price] pairs -- convert them
    # into the {"value": ..., "timestamp": ...} shape our functions expect.
    prices = [{"value": p[1], "timestamp": p[0]} for p in data["prices"]]
    return prices


if __name__ == "__main__":
    prices = fetch_xlm_prices(days=90)
    result = analyze_range(prices)
    print(result)