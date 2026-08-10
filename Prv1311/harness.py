"""
================================================================================
PROJECT: Prv1311 — Quantitative Asset Accumulation Engine
FILE: harness.py  (MULTI-ASSET UNIVERSE SCANNER, Coinbase)
================================================================================
CORE + RIDER run continually on the top-N liquid Coinbase assets every 15-min
cycle.

CORE = PURE MECHANICAL 6-2-1-1 LADDER (no gate). Buys the moment price breaches
the 90-day floor, ladders down through the bleed to lower the average, never
cuts. This is the six-year-backtested thesis. The triple-confirmation is NOT
here -- it belongs to the discovery scanner (vets what ENTERS the basket), not
to CORE execution.

RIDER gated by THREE selectivity rules so it snipes instead of panic-buying:
  1. deeper pullback (-10%, not 4% noise)
  2. floor buffer (price >= floor*1.05 -- freefall is CORE's job, not Rider's)
  3. global cap (MAX_CONCURRENT_RIDERS -- hard exposure limit)
Stablecoins + denylist excluded everywhere. $2M/day liquidity floor at selection.
Data-maturity gate (in screener) bans unseasoned coins system-wide.
================================================================================
"""

import json
import time
import os
from config import (
    STARTING_CAPITAL_USD, LEDGER_FILE,
    LADDER_SIZES, RUNG_DROPS, SLICE_USD, ENTRY_BAND, QUOTE,
    MIN_24H_USD_VOLUME, STABLE_EXCLUDES, DENYLIST,
    MAX_CONCURRENT_RIDERS, RIDER_PULLBACK_PCT, RIDER_TARGET_PCT, RIDER_FLOOR_BUFFER,
)
from screener import (exchange, fetch_live_price, calculate_90_day_floor,
                      rolling_7_day_high)

NUM_RUNGS = len(LADDER_SIZES)


def is_excluded(base):
    """Stablecoin or denylisted -> never trade (CORE or RIDER)."""
    return base in STABLE_EXCLUDES or base in DENYLIST


def get_universe_markets(limit=50):
    """Top N liquid Coinbase /USD pairs by 24h USD volume, above the floor."""
    try:
        tickers = exchange.fetch_tickers()
        pairs = []
        for sym, data in tickers.items():
            if not sym.endswith(f'/{QUOTE}'):
                continue
            base = sym.split('/')[0]
            if is_excluded(base):
                continue
            last = data.get('last')
            base_vol = data.get('baseVolume')
            if last and base_vol:
                usd_vol = base_vol * last
            else:
                qv = data.get('quoteVolume')
                usd_vol = qv if qv else 0
            if usd_vol >= MIN_24H_USD_VOLUME:
                pairs.append((sym, usd_vol))
        pairs.sort(key=lambda x: x[1], reverse=True)
        universe = [p[0] for p in pairs][:limit]
        if universe:
            return universe
    except Exception as e:
        print(f"[!] universe fetch error: {e}")
    return ['BTC/USD', 'ETH/USD', 'SOL/USD', 'XRP/USD', 'ADA/USD', 'XLM/USD']


def fresh_asset_state():
    return {
        'core_asset': 0.0, 'core_invested': 0.0, 'core_average_entry': 0.0,
        'current_rung': 0, 'rung_1_price': 0.0,
        'rider_asset': 0.0, 'rider_entry_price': 0.0, 'rider_holding': False,
    }


def load_ledger(current_basket):
    if not os.path.exists('data'):
        os.makedirs('data')
    if os.path.exists(LEDGER_FILE):
        led = json.load(open(LEDGER_FILE))
        if 'assets' not in led:
            led['assets'] = {}
        for sym in current_basket:
            if sym not in led['assets']:
                led['assets'][sym] = fresh_asset_state()
        return led
    led = {
        'system_name': 'Prv1311',
        'USD_balance': STARTING_CAPITAL_USD,
        'treasury': 0.0,
        'assets': {sym: fresh_asset_state() for sym in current_basket},
        'trade_history': [],
    }
    save_ledger(led)
    return led


def save_ledger(led):
    with open(LEDGER_FILE, 'w') as f:
        json.dump(led, f, indent=4)


def log_trade(led, sym, engine, action, price, usd, units, rung=None):
    led['trade_history'].append({
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'symbol': sym, 'engine': engine, 'action': action, 'rung': rung,
        'price': price, 'amount_usd': usd, 'asset_amount': units,
    })


def count_open_riders(led):
    """Live count of assets currently holding a rider position (for the cap)."""
    return sum(1 for a in led['assets'].values() if a['rider_holding'])


# ---------------- CORE ----------------
def core_buy(led, sym, num_slices, price, target_rung):
    a = led['assets'][sym]
    usd = SLICE_USD * num_slices
    if usd > led['USD_balance']:
        usd = led['USD_balance']
    if usd <= 0:
        return
    units = usd / price
    led['USD_balance'] -= usd
    a['core_asset'] += units
    a['core_invested'] += usd
    a['current_rung'] = target_rung
    a['core_average_entry'] = a['core_invested'] / a['core_asset']
    log_trade(led, sym, 'CORE', 'BUY', price, usd, units, rung=target_rung)
    print(f"\n  [{sym} CORE] RUNG {target_rung} BUY {num_slices} slice(s) "
          f"(${usd:.2f}) @ ${price:.4f}  avg ${a['core_average_entry']:.4f}\n")


# ---------------- RIDER ----------------
def rider_buy(led, sym, price):
    a = led['assets'][sym]
    usd = SLICE_USD
    if usd > led['USD_balance']:
        return
    units = usd / price
    led['USD_balance'] -= usd
    a['rider_asset'] = units
    a['rider_entry_price'] = price
    a['rider_holding'] = True
    log_trade(led, sym, 'RIDER', 'BUY', price, usd, units)
    print(f"  [{sym} RIDER] BUY (${usd:.2f}) @ ${price:.4f}  target ${price*(1+RIDER_TARGET_PCT/100):.4f}")


def rider_sell(led, sym, price):
    a = led['assets'][sym]
    units = a['rider_asset']
    proceeds = units * price
    profit = proceeds - units * a['rider_entry_price']
    led['USD_balance'] += proceeds
    led['treasury'] += profit
    log_trade(led, sym, 'RIDER', 'SELL', price, proceeds, units)
    print(f"  [{sym} RIDER] SELL @ ${price:.4f}  +{RIDER_TARGET_PCT:.0f}%  profit ${profit:.2f}")
    a['rider_asset'] = 0.0
    a['rider_entry_price'] = 0.0
    a['rider_holding'] = False


def process_asset_capped(led, sym, cap_reached):
    """
    One CORE+RIDER tick for one asset.
      - CORE: pure 6-2-1-1 ladder, no gate. Rung 1 on floor breach; rungs 2/3/4
        ladder down as it bleeds. Always runs.
      - RIDER: three gates; a NEW rider entry is skipped if the cap is reached,
        but rider EXITS always run.
    Returns a status str (if holding) or None.
    """
    base = sym.split('/')[0]
    if is_excluded(base):
        return None
    price = fetch_live_price(sym)
    if price is None:
        return None
    floor = calculate_90_day_floor(sym)
    if floor is None:
        return None                          # maturity gate / no floor -> skip
    high7 = rolling_7_day_high(sym)
    a = led['assets'][sym]

    # ---- CORE ladder (PURE 6-2-1-1, no gate) ----
    rung = a['current_rung']
    if rung == 0:
        if price <= floor * ENTRY_BAND:
            a['rung_1_price'] = price
            core_buy(led, sym, LADDER_SIZES[0], price, 1)
            save_ledger(led)
    elif rung < NUM_RUNGS:
        trigger = a['rung_1_price'] * (1 - RUNG_DROPS[rung])
        if price <= trigger:
            core_buy(led, sym, LADDER_SIZES[rung], price, rung + 1)
            save_ledger(led)

    # ---- RIDER (three gates) ----
    if high7 and floor:
        if a['rider_holding']:
            # exits always allowed
            target = a['rider_entry_price'] * (1 + RIDER_TARGET_PCT / 100.0)
            if price >= target:
                rider_sell(led, sym, price)
                save_ledger(led)
        else:
            # new entry: pullback + floor buffer + cap
            pullback_trigger = high7 * (1 - RIDER_PULLBACK_PCT / 100.0)
            above_floor_buffer = price >= floor * RIDER_FLOOR_BUFFER
            if price <= pullback_trigger and above_floor_buffer and not cap_reached:
                rider_buy(led, sym, price)
                save_ledger(led)

    # status line
    if a['current_rung'] > 0 or a['rider_holding']:
        tag = sym.split('/')[0]
        return (f"{tag} ${price:.4f} R{a['current_rung']}/{NUM_RUNGS} "
                f"{'RIDE' if a['rider_holding'] else '·'}")
    return None


def run_engine():
    print("=" * 80)
    print("      PRV1311 — INSTITUTIONAL UNIVERSE SCANNER (15-min cycle)")
    print("=" * 80)
    print(f"Ladder : {LADDER_SIZES} @ drops {RUNG_DROPS}  (PURE, no gate)")
    print(f"Liquidity floor : ${MIN_24H_USD_VOLUME:,.0f}/day")
    print(f"Rider gates : -{RIDER_PULLBACK_PCT:.0f}% pullback | floor*{RIDER_FLOOR_BUFFER} buffer | "
          f"max {MAX_CONCURRENT_RIDERS} concurrent")
    print(f"Status : ENGINE LIVE (Ctrl+C to stop)\n")

    while True:
        try:
            print(f"\n[{time.strftime('%H:%M:%S')}] Fetching market universe (top liquid /USD)...")
            current_basket = get_universe_markets(limit=50)
            print(f"  {len(current_basket)} assets in basket")
            led = load_ledger(current_basket)

            active = []
            for sym in current_basket:
                base = sym.split('/')[0]
                if is_excluded(base):
                    continue
                riders_open = count_open_riders(led)
                cap_reached = riders_open >= MAX_CONCURRENT_RIDERS
                status = process_asset_capped(led, sym, cap_reached)
                if status:
                    active.append(status)

            # valuation across everything ever held
            asset_val = 0.0
            for sym, a in led['assets'].items():
                if a['core_asset'] > 0 or a['rider_asset'] > 0:
                    pr = fetch_live_price(sym)
                    if pr:
                        asset_val += (a['core_asset'] + a['rider_asset']) * pr
            total_value = led['USD_balance'] + asset_val + led['treasury']
            save_ledger(led)

            print("-" * 80)
            if active:
                print(" | ".join(active))
            else:
                print("  (no active holdings yet -- engines watching)")
            print(f"[{time.strftime('%H:%M:%S')}] Portfolio: ${total_value:,.2f} "
                  f"| Cash ${led['USD_balance']:,.2f} | Open riders: {count_open_riders(led)}/{MAX_CONCURRENT_RIDERS}")
            print("-" * 80)

            time.sleep(15 * 60)

        except KeyboardInterrupt:
            print("\n[Prv1311] Engine stopped safely by user.")
            break
        except Exception as e:
            print(f"\n[Prv1311 Error] {e}")
            time.sleep(60)


if __name__ == "__main__":
    run_engine()