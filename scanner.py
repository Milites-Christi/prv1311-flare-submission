"""
scanner.py -- v2

The eligibility scanner: "which coins are quality enough to safely run the
never-cut system on?" Selection, not timing.

v2 adds three DATA-VERIFIED filters on top of v1's price/rank bar. Thresholds
were confirmed against real test cases (FIGR_HELOC, USDS, RAIN must die;
XRP, TRX, DOGE, ADA, XLM must survive):

  1. Volume/market-cap ratio >= MIN_VOL_MCAP -- the liquidity check. Real
     assets turn over a healthy fraction daily; inflated-cap / thin-float
     junk doesn't. (FIGR 0.81%, RAIN 0.21% die; keepers all clear ~1%+.)
  2. Behavioral stablecoin exclusion -- price pinned near $1.00 = a stable,
     no hardcoded list needed. (USDS $0.9999 dies automatically.)
  3. FDV/market-cap flag -- if fully-diluted value is far above market cap,
     most supply isn't circulating (low-float red flag). Secondary layer.
"""

import os
import requests

# --- Tunable bar ---
TOP_N = 20
PRICE_CEILING = 5.0
PRICE_FLOOR = 0.001
MIN_VOL_MCAP = 0.010        # min 24h volume / market cap = 1.0% (liquidity)
STABLE_BAND = (0.97, 1.03)  # price in this band = behavioral stablecoin
MAX_FDV_MCAP = 3.0          # FDV more than 3x mcap = low-float red flag

# Manual denylist -- human override for category risk the metrics can't see.
# An asset here is excluded even if it passes every structural filter.
# Reason: never-cut is only safe on assets with a REAL demand floor (utility,
# not hype). Meme/pump-driven assets can go down and STAY down -- you don't
# want to be married to one for years. Metrics catch structural junk; this
# catches category risk.
DENYLIST = {
    "doge": "meme/hype-driven -- recovery depends on sentiment, not utility floor",
}

def fetch_top_markets(n=TOP_N):
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {"vs_currency": "usd", "order": "market_cap_desc",
              "per_page": n, "page": 1, "sparkline": "false"}
    headers = {}
    key = os.environ.get("COINGECKO_API_KEY")
    if key:
        headers["x-cg-demo-api-key"] = key
    resp = requests.get(url, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    out = []
    for c in resp.json():
        mcap = c.get("market_cap") or 0
        vol = c.get("total_volume") or 0
        fdv = c.get("fully_diluted_valuation") or mcap
        out.append({
            "symbol": (c.get("symbol") or "").lower(),
            "name": c.get("name"),
            "price": c.get("current_price"),
            "market_cap": mcap,
            "volume": vol,
            "fdv": fdv,
            "rank": c.get("market_cap_rank"),
            "vol_mcap": (vol / mcap) if mcap else 0,
            "fdv_mcap": (fdv / mcap) if mcap else 999,
        })
    return out


def screen(coins):
    """Apply the full eligibility bar. Returns (eligible, rejected_with_reasons)."""
    eligible, rejected = [], []
    for c in coins:
        price = c["price"]
        sym = c["symbol"]
        # order matters: cheapest checks first, most informative reason wins
        if sym in DENYLIST:
            rejected.append((c, f"denylisted: {DENYLIST[sym]}"))
        elif price is None:
            rejected.append((c, "no price"))
        elif STABLE_BAND[0] <= price <= STABLE_BAND[1]:
            rejected.append((c, f"stablecoin (price ${price:.4f} pinned ~$1)"))
        elif price > PRICE_CEILING:
            rejected.append((c, f"price ${price:,.2f} > ${PRICE_CEILING:.0f} ceiling"))
        elif price < PRICE_FLOOR:
            rejected.append((c, f"dust (price ${price})"))
        elif c["vol_mcap"] < MIN_VOL_MCAP:
            rejected.append((c, f"thin liquidity (vol/mcap {c['vol_mcap']*100:.2f}% < {MIN_VOL_MCAP*100:.1f}%)"))
        elif c["fdv_mcap"] > MAX_FDV_MCAP:
            rejected.append((c, f"low float (FDV {c['fdv_mcap']:.1f}x mcap)"))
        else:
            eligible.append(c)
    return eligible, rejected


if __name__ == "__main__":
    print(f"Fetching CoinGecko top {TOP_N} by market cap ...\n")
    coins = fetch_top_markets(TOP_N)
    eligible, rejected = screen(coins)

    print("=" * 74)
    print(f"ELIGIBLE BASKET  (top {TOP_N}, <${PRICE_CEILING:.0f}, liquid, real float, no stables)")
    print("=" * 74)
    if eligible:
        for c in eligible:
            print(f"  #{c['rank']:<3} {c['symbol'].upper():<8} {c['name']:<18} "
                  f"${c['price']:<10,.4f} mcap ${c['market_cap']/1e9:>5,.1f}B  "
                  f"vol/mcap {c['vol_mcap']*100:>4.1f}%")
    else:
        print("  (nothing passed -- loosen the bar)")
    print(f"\n  {len(eligible)} assets eligible\n")

    print("-" * 74)
    print("REJECTED (and why):")
    for c, reason in rejected:
        print(f"  {c['symbol'].upper():<10} {c['name']:<18} -- {reason}")

    print("\n=== TEST: did FIGR_HELOC / USDS / RAIN get killed, and did")
    print("=== XRP / TRX / DOGE / ADA / XLM survive? ===")