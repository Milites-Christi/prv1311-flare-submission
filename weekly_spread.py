from range_percentiles import fetch_xlm_prices
"""
weekly_spread.py

Ported from B3OS extracted/flip-weekly-spread.js
Purpose: split the last 90 days into 7-day buckets, find each week's
high-low spread, average them, then calculate 80% of that average.
That 80%-of-spread number becomes the "take profit" exit target,
measured as an offset added to your average entry price.
"""

def analyze_weekly_spread(prices, bucket_days=7):
    """
    prices: same {"value": ..., "timestamp": ...} list as before.
    bucket_days: how many days per bucket (7 = weekly).

    Groups prices into weekly buckets, finds each week's high and low,
    then averages the (high - low) spread across all weeks.
    """
    EXIT_TARGET_PCT_OF_SPREAD = 0.8
    bucket_ms = bucket_days * 86400000  # convert days -> milliseconds

    first_ts = prices[0]["timestamp"]
    buckets = {}  # will hold: { bucket_number: {"high": x, "low": y} }

    for p in prices:
        # which bucket does this price point belong to?
        # e.g. day 0-6 -> bucket 0, day 7-13 -> bucket 1, etc.
        bucket_idx = int((p["timestamp"] - first_ts) / bucket_ms)

        if bucket_idx not in buckets:
            buckets[bucket_idx] = {"high": p["value"], "low": p["value"]}
        else:
            if p["value"] > buckets[bucket_idx]["high"]:
                buckets[bucket_idx]["high"] = p["value"]
            if p["value"] < buckets[bucket_idx]["low"]:
                buckets[bucket_idx]["low"] = p["value"]

    spreads = [b["high"] - b["low"] for b in buckets.values()]
    avg_spread = sum(spreads) / len(spreads) if spreads else None

    return {
        "avg_weekly_spread": avg_spread,
        "bucket_count": len(spreads),
        "primary_exit_target_offset": avg_spread * EXIT_TARGET_PCT_OF_SPREAD if avg_spread is not None else None
    }


if __name__ == "__main__":
    prices = fetch_xlm_prices(days=90)
    result = analyze_weekly_spread(prices)
    print(result)