"""
fetch_history.py

Piece 1 of the backtest: the data fetch.

Job: pull HOURLY candles for one asset from Binance.US, going back as far
as we ask, and hand them back in a clean, simple shape.

Why Binance.US: it's the one source we tested that has deep hourly history
(back to ~2019) for XLM/XRP/ALGO. CoinGecko only gives hourly for ~90 days;
Kraken caps at ~720 candles; Binance.com is geoblocked in the US.

This file does ONE thing and prints the result, so we can confirm the data
source actually works before building the backtest on top of it.
"""

import ccxt          # the library that talks to exchanges for us
import time          # for pacing our requests politely
from datetime import datetime, timezone


def fetch_hourly_candles(symbol="XLM/USDT", since_year=2019, max_batches=100):
    """
    Pulls hourly candles for `symbol` from Binance.US, starting at the
    beginning of `since_year`, and returns them as a list of dicts:
        {"timestamp": <ms>, "open": .., "high": .., "low": .., "close": .., "volume": ..}

    HOW PAGINATION WORKS (the important part):
    Binance.US only returns up to ~1000 candles per request. To get years
    of history, we ask in batches: fetch 1000, note the timestamp of the
    last one, then ask for the next 1000 starting from there. Repeat until
    we reach the present (or hit max_batches as a safety cap).
    """
    # Create the exchange object. This is our "connection" to Binance.US.
    exchange = ccxt.binanceus()

    timeframe = "1h"          # hourly candles
    limit = 1000              # max candles per request (Binance.US cap)

    # Convert "start of since_year" into a Unix timestamp in MILLISECONDS,
    # which is what ccxt expects for the `since` parameter.
    start_dt = datetime(since_year, 1, 1, tzinfo=timezone.utc)
    since = int(start_dt.timestamp() * 1000)

    all_candles = []          # we'll accumulate every batch here
    batch_count = 0

    while batch_count < max_batches:
        # Ask Binance.US for up to `limit` candles starting at `since`.
        # ccxt returns each candle as a list: [timestamp, open, high, low, close, volume]
        batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)

        # If we got nothing back, we've run out of history — stop.
        if not batch:
            break

        # Convert each raw [t,o,h,l,c,v] list into a labeled dict so the
        # rest of our code reads clearly (candle["close"] not candle[4]).
        for c in batch:
            all_candles.append({
                "timestamp": c[0],
                "open": c[1],
                "high": c[2],
                "low": c[3],
                "close": c[4],
                "volume": c[5],
            })

        batch_count += 1

        # Move `since` forward to just past the last candle we received,
        # so the next request picks up where this one ended.
        last_ts = batch[-1][0]
        since = last_ts + 1

        # If this batch was smaller than the max, there's no more data
        # after it — we've caught up to the present. Stop.
        if len(batch) < limit:
            break

        # Be polite to the exchange: pause briefly between requests so we
        # don't get rate-limited. rateLimit is in milliseconds.
        time.sleep(exchange.rateLimit / 1000)

    return all_candles


if __name__ == "__main__":
    # Test run: pull XLM hourly history and report what we got.
    print("Fetching XLM/USDT hourly candles from Binance.US ...")
    candles = fetch_hourly_candles(symbol="XLM/USDT", since_year=2019)

    print(f"Total candles fetched: {len(candles)}")

    if candles:
        first = candles[0]
        last = candles[-1]
        first_date = datetime.fromtimestamp(first["timestamp"] / 1000, tz=timezone.utc)
        last_date = datetime.fromtimestamp(last["timestamp"] / 1000, tz=timezone.utc)
        print(f"Earliest candle: {first_date}  close=${first['close']}")
        print(f"Latest candle:   {last_date}  close=${last['close']}")
        # rough span in days, just as a sanity check
        span_days = (last["timestamp"] - first["timestamp"]) / 86400000
        print(f"Span: {span_days:.0f} days (~{span_days/365:.1f} years)")
    else:
        print("No candles returned -- check the symbol or connection.")