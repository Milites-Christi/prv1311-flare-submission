"""
================================================================================
PROJECT: Prv1311 — Weekly Spread (exit-target calculator, for CORE)
FILE: weekly_spread.py
================================================================================
Ported from Accum-Flip 90-Day Analysis "Calculate Average Price Spread."

WHAT IT DOES: buckets a price history into weekly (high-low) spreads and averages
them, then derives a primary EXIT TARGET offset = 80% of the average weekly
spread. Drives "avg_entry + 80% of typical weekly move" as a profit target.

THIS IS AN EXIT-TARGET CALC, NOT AN ENTRY GUARD. It's staged for CORE (the
mechanical 6-2-1-1 accumulator, not yet built/ported). Kept as a pure function
so it's ready to wire into CORE's exit logic when CORE lands. Not wired to any
live fleet today — nothing to wire it to yet.

NOTE: the "block buying something already up ~100% this week" idea (the COTI
overextension guard) is a SEPARATE, unbuilt entry-gate concept — do not confuse
it with this exit calc.

CONSTANTS: BUCKET_DAYS=7 (weekly; ~30 for larger capital), EXIT_TARGET_PCT_OF_SPREAD=0.8.
================================================================================
"""

BUCKET_DAYS = 7                  # bucket period; 7=weekly (default ~$20k), ~30=monthly for larger capital
EXIT_TARGET_PCT_OF_SPREAD = 0.8  # primary exit target = this fraction of avg weekly spread


def weekly_spread(price_points, bucket_days=BUCKET_DAYS,
                  exit_pct=EXIT_TARGET_PCT_OF_SPREAD):
    """PURE FUNCTION. price_points = list of {'timestamp': ms, 'value': price},
    oldest->newest. Returns avg_weekly_spread + primary_exit_target_offset.

    To get the actual exit price for a position:
        exit_price = avg_entry_price + result['primary_exit_target_offset']
    """
    out = {
        'avg_weekly_spread': None, 'bucket_count': 0,
        'bucket_days': bucket_days, 'exit_target_pct_of_spread': exit_pct,
        'primary_exit_target_offset': None,
    }
    if not price_points:
        return out

    bucket_ms = bucket_days * 86400000
    first_ts = price_points[0]['timestamp']
    buckets = {}
    for p in price_points:
        idx = int((p['timestamp'] - first_ts) // bucket_ms)
        val = p['value']
        if idx not in buckets:
            buckets[idx] = {'high': val, 'low': val}
        else:
            if val > buckets[idx]['high']:
                buckets[idx]['high'] = val
            if val < buckets[idx]['low']:
                buckets[idx]['low'] = val

    spreads = [b['high'] - b['low'] for b in buckets.values()]
    avg = sum(spreads) / len(spreads) if spreads else None

    out['avg_weekly_spread'] = round(avg, 8) if avg is not None else None
    out['bucket_count'] = len(spreads)
    out['primary_exit_target_offset'] = round(avg * exit_pct, 8) if avg is not None else None
    return out


def spread_from_ohlcv(ohlcv, bucket_days=BUCKET_DAYS, exit_pct=EXIT_TARGET_PCT_OF_SPREAD):
    """Convenience wrapper: takes ccxt daily OHLCV rows [ts,o,h,l,c,v] and builds
    the price_points list (using close) before calling weekly_spread."""
    pts = [{'timestamp': row[0], 'value': row[4]} for row in ohlcv] if ohlcv else []
    return weekly_spread(pts, bucket_days, exit_pct)


if __name__ == "__main__":
    # sanity: synthetic 3-week series
    import time
    now = int(time.time() * 1000)
    day = 86400000
    pts = []
    for d in range(21):
        base = 100 + (d % 7) * 2   # oscillates within each week
        pts.append({'timestamp': now - (21 - d) * day, 'value': base})
    print(weekly_spread(pts))