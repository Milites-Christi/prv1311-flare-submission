"""
pipeline.py

THE CONNECTION: scanner (eligibility) -> portfolio (backtest).

Flow:
  1. Scanner picks the eligible basket (live, quality-screened).
  2. For each eligible asset, check if it has Binance.US history we can
     actually backtest (verify-don't-trust -- an asset can be "eligible"
     but lack tradeable data).
  3. Backtest the proven system on the assets that pass BOTH gates.
  4. Report which made it, which were dropped and why.

This is pipeline Stage 2 (eligibility) feeding Stage 3 (construction),
the way an institutional pipeline actually flows.
"""

from scanner import fetch_top_markets, screen
from fetch_history import fetch_hourly_candles
from backtest import candles_to_daily


# map a scanner symbol (e.g. "xrp") to the Binance.US pair we backtest
def to_binance_pair(symbol):
    return f"{symbol.upper()}/USDT"


def build_tradeable_basket(min_candles=800):
    """
    Run the scanner, then keep only eligible assets that ALSO have enough
    Binance.US history to backtest. Returns (tradeable, dropped_with_reasons).
    """
    print("Step 1: scanning for eligible assets ...\n")
    coins = fetch_top_markets()
    eligible, _rejected = screen(coins)

    print(f"  Scanner eligible: {', '.join(c['symbol'].upper() for c in eligible)}\n")
    print("Step 2: checking which eligible assets have backtestable history ...\n")

    tradeable = []
    dropped = []

    for c in eligible:
        pair = to_binance_pair(c["symbol"])
        try:
            candles = fetch_hourly_candles(symbol=pair, since_year=2019)
            daily = candles_to_daily(candles)
            n = len(daily)
            if n >= min_candles:
                tradeable.append({"symbol": c["symbol"].upper(), "pair": pair,
                                  "daily": daily, "days": n})
                print(f"  {c['symbol'].upper():<6} OK  ({n} daily candles)")
            else:
                dropped.append((c["symbol"].upper(), f"only {n} candles (< {min_candles})"))
                print(f"  {c['symbol'].upper():<6} thin ({n} candles, need {min_candles})")
        except Exception as e:
            dropped.append((c["symbol"].upper(), f"no Binance.US data ({type(e).__name__})"))
            print(f"  {c['symbol'].upper():<6} NO DATA on Binance.US")

    return tradeable, dropped


if __name__ == "__main__":
    tradeable, dropped = build_tradeable_basket()

    print("\n" + "=" * 68)
    print("FINAL TRADEABLE BASKET (passed BOTH gates: eligible + has data)")
    print("=" * 68)
    if tradeable:
        for t in tradeable:
            print(f"  {t['symbol']:<6} {t['pair']:<12} {t['days']} daily candles")
    else:
        print("  (none passed both gates)")

    print(f"\n  {len(tradeable)} assets ready to backtest\n")

    if dropped:
        print("-" * 68)
        print("Eligible but NOT backtestable (dropped, and why):")
        for sym, reason in dropped:
            print(f"  {sym:<6} -- {reason}")

    print("\n=== This tradeable basket is what the portfolio backtests. The scanner")
    print("=== chose the roster; the data check confirmed who can actually play. ===")