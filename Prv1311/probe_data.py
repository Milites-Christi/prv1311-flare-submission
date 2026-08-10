"""
probe_data.py -- throwaway. Checks what Binance.US (via ccxt) gives us for
the three RSI-scanner inputs: multi-timeframe candles, and taker buy/sell data.
Delete after we read the results.
"""
import ccxt

ex = ccxt.binanceus({'enableRateLimit': True})
sym = 'XLM/USDT'

print("=" * 60)
print("1. Multi-timeframe candles (for RSI + Bollinger)")
print("=" * 60)
for tf in ['1h', '4h', '1d', '1w']:
    try:
        ohlcv = ex.fetch_ohlcv(sym, timeframe=tf, limit=30)
        print(f"  {tf:>3}: got {len(ohlcv)} candles  "
              f"(last close ${ohlcv[-1][4]:.4f})")
    except Exception as e:
        print(f"  {tf:>3}: FAILED -- {e}")

print("\n" + "=" * 60)
print("2. Taker buy/sell data -- the risky one")
print("=" * 60)

# Binance.US kline rows include taker-buy-base-volume as an extra field.
# Try the raw endpoint to see if the taker column comes through.
try:
    # ccxt's fetch_ohlcv trims to OHLCV; the taker data is in the raw kline.
    # Try the implicit/raw Binance kline call:
    raw = ex.publicGetKlines({'symbol': 'XLMUSDT', 'interval': '4h', 'limit': 1})
    print(f"  Raw kline columns returned: {len(raw[0])}")
    print(f"  Full row: {raw[0]}")
    # Binance kline format: [openTime, open, high, low, close, volume,
    #   closeTime, quoteVol, numTrades, takerBuyBaseVol, takerBuyQuoteVol, ignore]
    if len(raw[0]) >= 11:
        total_vol = float(raw[0][5])
        taker_buy_vol = float(raw[0][9])
        taker_sell_vol = total_vol - taker_buy_vol
        ratio = taker_buy_vol / taker_sell_vol if taker_sell_vol > 0 else 0
        print(f"\n  >>> TAKER DATA AVAILABLE <<<")
        print(f"  taker buy volume : {taker_buy_vol:.2f}")
        print(f"  taker sell volume: {taker_sell_vol:.2f}")
        print(f"  buy/sell ratio   : {ratio:.4f}")
    else:
        print("  taker columns NOT present -- will need fallback")
except Exception as e:
    print(f"  raw kline call FAILED -- {e}")

print("\n" + "=" * 60)
print("If section 1 shows candles for all 4 timeframes AND section 2 shows")
print("a buy/sell ratio -> we can port the FULL triple-confirmation scanner.")
print("=" * 60)