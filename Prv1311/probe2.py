"""probe2.py -- throwaway. Diagnose the liquidity + taker data on real coins."""
import ccxt
ex = ccxt.binanceus({'enableRateLimit': True})

for sym in ['RENDER/USDT', 'STORJ/USDT', 'EGLD/USDT', 'XLM/USDT', 'ETC/USDT']:
    print(f"\n=== {sym} ===")
    try:
        t = ex.fetch_ticker(sym)
        print(f"  last price   : {t.get('last')}")
        print(f"  quoteVolume  : {t.get('quoteVolume')}")
        print(f"  baseVolume   : {t.get('baseVolume')}")
        # what does the raw 24h ticker say?
        raw = ex.publicGetTicker24hr({'symbol': sym.replace('/', '')})
        print(f"  raw quoteVolume: {raw.get('quoteVolume')}")
        print(f"  raw volume     : {raw.get('volume')}")
    except Exception as e:
        print(f"  ticker error: {e}")

    # taker check on a few timeframes
    for tf in ['4h', '1h', '1d']:
        try:
            k = ex.publicGetKlines({'symbol': sym.replace('/',''), 'interval': tf, 'limit': 1})
            row = k[0]
            total, buy = float(row[5]), float(row[9])
            sell = total - buy
            print(f"  {tf}: total_vol={total:.1f} taker_buy={buy:.1f} taker_sell={sell:.1f}")
        except Exception as e:
            print(f"  {tf} kline error: {e}")