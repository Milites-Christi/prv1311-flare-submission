"""probe_coinbase.py -- throwaway. Does Coinbase give real live volume,
intraday candles, and any taker data? Public data, no keys needed."""
import ccxt

ex = ccxt.coinbase({'enableRateLimit': True})

print("Loading Coinbase markets...")
try:
    markets = ex.load_markets()
    usd_pairs = [m for m in markets if m.endswith('/USD')]
    usdt_pairs = [m for m in markets if m.endswith('/USDT')]
    print(f"  {len(usd_pairs)} /USD pairs, {len(usdt_pairs)} /USDT pairs\n")
except Exception as e:
    print(f"  market load error: {e}\n")

# Coinbase quotes mostly in USD, not USDT -- test both
for sym in ['XLM/USD', 'XRP/USD', 'ADA/USD', 'RENDER/USD']:
    print(f"=== {sym} ===")
    try:
        t = ex.fetch_ticker(sym)
        print(f"  last price  : {t.get('last')}")
        print(f"  quoteVolume : {t.get('quoteVolume')}")
        print(f"  baseVolume  : {t.get('baseVolume')}")
    except Exception as e:
        print(f"  ticker error: {e}")
    for tf in ['1h', '4h', '1d']:
        try:
            o = ex.fetch_ohlcv(sym, timeframe=tf, limit=3)
            if o:
                last = o[-1]
                print(f"  {tf}: {len(o)} candles, last close ${last[4]}, vol {last[5]}")
            else:
                print(f"  {tf}: no candles")
        except Exception as e:
            print(f"  {tf} error: {e}")
    print()

print("=" * 55)
print("Checking if Coinbase exposes taker data (trades)...")
try:
    trades = ex.fetch_trades('XLM/USD', limit=5)
    if trades:
        t0 = trades[0]
        print(f"  fetch_trades works: {len(trades)} recent trades")
        print(f"  sample -> side: {t0.get('side')}, amount: {t0.get('amount')}, price: {t0.get('price')}")
        print("  (taker buy/sell can be derived from trade 'side')")
    else:
        print("  no trades returned")
except Exception as e:
    print(f"  fetch_trades error: {e}")