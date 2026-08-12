"""
================================================================================
PROJECT: Prv1311 — CORE (mechanical bear/crash accumulation engine)
FILE: core.py
================================================================================
The crash counterpart to Rider. Where Rider buys a shallow dip and flips fast
(bull/chop), CORE buys the BREACH and SCALES DOWN as the crash keeps falling,
then never-cuts until the blended position climbs back to its exit target.

Pure mechanical 6-2-1-1 ladder (NO triple-confirmation in front — that miswiring
starved CORE in the overnight capitulation; it belongs on Rider/Discovery).

THE LADDER (per position, weights confirmed vs 6-year backtest):
  LADDER_WEIGHTS = [6, 2, 1, 1]   -> rung 0 = 60% of the bucket, rung 1 = 20%,
                                      rungs 2 & 3 = 10% each (front-loaded, then
                                      reinforces deeper).
  RUNG_DROPS = [0, -0.20, -0.40, -0.60]  -> rung 0 at the breach, then add at
                                      -20%, -40%, -60% below the rung-0 entry.
  Never-cut: holds the whole laddered stack until the BLENDED average entry
  reaches the exit target (weekly_spread: avg_entry + 80% of avg weekly spread).

SELF-CALCULATING SIZING (the whole point — never touch a calculator):
  A client hands you any random post-fee amount ($7,812.43, $1,000.01, whatever).
  CORE derives everything from that number: bucket = POOL/COUNT clamped
  [FLOOR, CEILING], reserve held back (never 100% deployed), and each bucket
  auto-splits across the 4 rungs by LADDER_WEIGHTS. Refuses-and-explains if the
  pool can't fund even rung 0 above the floor.

BREACH TRIGGER: *** PLACEHOLDER — Clay is researching the real trigger. ***
  Currently CORE_BREACH = price below the 90-day floor (the most conservative
  "crash has started" signal already available). When the real security-feature
  trigger is ready, swap the body of core_breach() — it's the ONE place to change.

DESIGN NOTES (2026-08-12 gate-parity audit — reviewed and decided; do not
re-raise without re-reading this):
  - REGIME GATE DELIBERATELY ABSENT. classify_regime() reports 'reverting' or
    'trending_up'. CORE is the bear/crash engine: it exists specifically to
    enter breaches while price is trending_down. Wiring the regime gate here
    would reject every entry this fleet exists to make. Same class of
    deliberate carve-out as the flow gate guarding rung 0 only in rider_team.py.
  - RIDER_FLOOR_BUFFER IMPORT IS DEAD AND MUST STAY DEAD. core_breach() fires
    on price BELOW the 90-day floor; the floor buffer requires
    price >= floor * 1.05. The two conditions are mutually exclusive by
    construction — wiring it would stop CORE from ever firing. Scheduled for
    deletion, not for wiring.
  - ANOMALY GATE IS A GENUINE GAP. Wiring deferred to after 2026-08-14. The
    catch-band elsewhere in this system routes below-band crashes TO CORE, so
    an unwinding blow-off top (see HFT: ~3.5x pump then ~74% collapse) reaches
    CORE looking identical to a real breakdown, and the 6-2-1-1 ladder under
    never-cut would pin four rungs into it.

ISOLATED: own pool, ledger, core_state table, own page. Same 20%-of-profit fee.
================================================================================
"""

import json
import time
import os
from config import (
    QUOTE, MIN_24H_USD_VOLUME, STABLE_EXCLUDES, DENYLIST, WATCHLIST,
    RIDER_FLOOR_BUFFER,
    CORE_POOL_USD, CORE_COUNT, CORE_CEILING, CORE_FLOOR, CORE_RESERVE_BUCKETS,
    CORE_LEDGER_FILE,
)
from screener import (exchange, fetch_live_price, calculate_90_day_floor)
from sync_supabase import push_core
from orderbook_imbalance import obi_gate
from weekly_spread import weekly_spread
from footprint_gate import check_flow

FEE_RATE = 0.20
USE_OBI_GATE = True

# --- the ladder (confirmed vs 6-year backtest) ---
LADDER_WEIGHTS = [6, 2, 1, 1]              # rung 0..3 share of the bucket
RUNG_DROPS = [0.0, -0.20, -0.40, -0.60]    # price drop below rung-0 entry per rung
_WEIGHT_TOTAL = sum(LADDER_WEIGHTS)        # 10


# ---------------------------------------------------------------------------
# SELF-CALCULATING SIZING — works from ANY pool value, no manual input
# ---------------------------------------------------------------------------
def compute_sizing():
    """Derive bucket, active slots, reserve, and per-rung dollar splits from the
    CORE_* config knobs. Clamps bucket to [CORE_FLOOR, CORE_CEILING]. Raises
    ValueError with a plain reason if the pool can't fund one bucket."""
    if CORE_COUNT < 1:
        raise ValueError(f"CORE_COUNT must be >= 1 (got {CORE_COUNT}).")
    raw = CORE_POOL_USD / CORE_COUNT
    if raw < CORE_FLOOR:
        max_slots = int(CORE_POOL_USD // CORE_FLOOR)
        raise ValueError(
            f"Pool ${CORE_POOL_USD:,.2f} / {CORE_COUNT} = ${raw:,.2f} per bucket, "
            f"below the ${CORE_FLOOR:,.0f} floor. Max buckets at this pool: {max_slots}. "
            f"Lower CORE_COUNT or raise CORE_POOL_USD."
        )
    bucket = min(raw, CORE_CEILING)
    reserve_slots = max(0, CORE_RESERVE_BUCKETS)
    active_slots = CORE_COUNT - reserve_slots
    if active_slots < 1:
        raise ValueError(
            f"CORE_RESERVE_BUCKETS ({CORE_RESERVE_BUCKETS}) leaves no active slots "
            f"out of CORE_COUNT ({CORE_COUNT})."
        )
    # per-rung dollar split of ONE bucket, by ladder weights
    rung_usd = [round(bucket * w / _WEIGHT_TOTAL, 2) for w in LADDER_WEIGHTS]
    return {
        'bucket': bucket,
        'active_slots': active_slots,
        'reserve_slots': reserve_slots,
        'reserve_usd': bucket * reserve_slots,
        'rung_usd': rung_usd,
        'starting_capital': CORE_POOL_USD,
    }


SIZING = compute_sizing()
BUCKET_USD = SIZING['bucket']
MAX_ACTIVE = SIZING['active_slots']
RESERVE_USD = SIZING['reserve_usd']
RUNG_USD = SIZING['rung_usd']
STARTING_CAPITAL_USD = SIZING['starting_capital']


def is_excluded(base):
    return base in STABLE_EXCLUDES or base in DENYLIST


# ---------------------------------------------------------------------------
# BREACH TRIGGER — the ONE place to swap when the real trigger is researched
# ---------------------------------------------------------------------------
def core_breach(sym, price, floor):
    """PLACEHOLDER breach trigger: True when the crash has started.
    Currently: price has broken below the 90-day statistical floor.
    *** Swap this body when Clay's real security-feature trigger is ready. ***"""
    if floor is None:
        return False
    return price < floor


# ---------------------------------------------------------------------------
def daily_closes(symbol, limit=120):
    pair = symbol if '/' in symbol else f"{symbol}/{QUOTE}"
    try:
        ohlcv = exchange.fetch_ohlcv(pair, timeframe='1d', limit=limit)
        return ohlcv if ohlcv else None
    except Exception:
        return None


def get_universe_markets(limit=50):
    """Top N liquid Coinbase /USD pairs by 24h volume, PLUS the client's
    WATCHLIST tokens forced in (Coinbase-listed only, /USD then /USDC)."""
    universe = []
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
            usd_vol = (base_vol * last) if (last and base_vol) else (data.get('quoteVolume') or 0)
            if usd_vol >= MIN_24H_USD_VOLUME:
                pairs.append((sym, usd_vol))
        pairs.sort(key=lambda x: x[1], reverse=True)
        universe = [p[0] for p in pairs][:limit]
    except Exception as e:
        print(f"[!] universe fetch error: {e}")
        universe = ['BTC/USD', 'ETH/USD', 'SOL/USD', 'XRP/USD', 'ADA/USD', 'XLM/USD']

    try:
        markets = exchange.markets or exchange.load_markets()
    except Exception:
        markets = {}
    seen = set(universe)
    for t in WATCHLIST:
        base = t.upper()
        if is_excluded(base):
            continue
        wsym = None
        for q in (QUOTE, 'USDC'):
            cand = f"{base}/{q}"
            if cand in markets:
                wsym = cand
                break
        if wsym and wsym not in seen:
            universe.append(wsym)
            seen.add(wsym)
    return universe


def fresh_state():
    return {
        'system': 'Prv1311-core',
        'USD_balance': STARTING_CAPITAL_USD,
        'treasury': 0.0,
        'fees_wallet': 0.0,
        'positions': {},          # laddered stacks, keyed by symbol
        'trade_history': [],
    }


def load_state():
    if not os.path.exists('data'):
        os.makedirs('data')
    if os.path.exists(CORE_LEDGER_FILE):
        s = json.load(open(CORE_LEDGER_FILE))
        s.setdefault('fees_wallet', 0.0)
        s.setdefault('positions', {})
        return s
    s = fresh_state()
    save_state(s)
    return s


def save_state(s):
    with open(CORE_LEDGER_FILE, 'w') as f:
        json.dump(s, f, indent=4)


def log(s, action, sym, price, usd, units, rung=None, profit=None, fee=None):
    row = {'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
           'engine': 'CORE', 'action': action, 'asset': sym.split('/')[0],
           'price': price, 'amount_usd': usd, 'units': units}
    if rung is not None:
        row['rung'] = rung
    if profit is not None:
        row['profit'] = profit
    if fee is not None:
        row['fee'] = fee
    s['trade_history'].append(row)


def deployable_cash(s):
    return s['USD_balance'] - RESERVE_USD


def position_blended(p):
    """Blended average entry + total units + total usd_in across all filled rungs."""
    total_units = sum(r['units'] for r in p['rungs'])
    total_usd = sum(r['usd_in'] for r in p['rungs'])
    avg_entry = (total_usd / total_units) if total_units > 0 else 0.0
    return avg_entry, total_units, total_usd


def deploy_rung(s, sym, price, rung_idx):
    """Deploy one ladder rung into sym at the current price."""
    usd = RUNG_USD[rung_idx]
    if usd <= 0:
        return False
    if deployable_cash(s) < usd:
        return False
    units = usd / price
    s['USD_balance'] -= usd
    if sym not in s['positions']:
        s['positions'][sym] = {'asset': sym, 'rung0_entry': price, 'rungs': [],
                               'next_rung': 0}
    p = s['positions'][sym]
    p['rungs'].append({'rung': rung_idx, 'entry_price': price,
                       'units': units, 'usd_in': usd})
    p['next_rung'] = rung_idx + 1
    log(s, 'BUY', sym, price, usd, units, rung=rung_idx)
    print(f"  [CORE BUY] {sym.split('/')[0]:<6} rung {rung_idx} @ ${price:.4f}  "
          f"${usd:,.2f} ({len(p['rungs'])}/4 rungs filled)")
    return True


def exit_target_for(sym, avg_entry):
    """weekly_spread exit: avg_entry + 80% of the avg weekly spread. Falls back
    to a simple +7% if spread data is thin."""
    ohlcv = daily_closes(sym, limit=95)
    if ohlcv:
        pts = [{'timestamp': row[0], 'value': row[4]} for row in ohlcv]
        ws = weekly_spread(pts)
        offset = ws.get('primary_exit_target_offset')
        if offset:
            return avg_entry + offset
    return avg_entry * 1.07


def close_position(s, sym, price):
    p = s['positions'][sym]
    avg_entry, total_units, total_usd = position_blended(p)
    proceeds = total_units * price
    profit = proceeds - total_usd
    fee = profit * FEE_RATE if profit > 0 else 0.0
    net = profit - fee
    s['USD_balance'] += proceeds
    s['treasury'] += net
    s['fees_wallet'] += fee
    log(s, 'SELL', sym, price, proceeds, total_units, profit=net, fee=fee)
    print(f"  [CORE EXIT] {sym.split('/')[0]:<6} @ ${price:.4f}  avg ${avg_entry:.4f}  "
          f"({len(p['rungs'])} rungs)  profit ${net:.2f} (fee ${fee:.2f})")
    del s['positions'][sym]


def run_cycle(s, universe):
    # ---- 1. manage open ladders: add deeper rungs, or exit at target ----
    for sym in list(s['positions'].keys()):
        price = fetch_live_price(sym)
        if price is None:
            continue
        p = s['positions'][sym]
        avg_entry, total_units, _ = position_blended(p)

        # exit check first (never-cut: only sells at/above target)
        target = exit_target_for(sym, avg_entry)
        if price >= target:
            close_position(s, sym, price)
            continue

        # add the next rung if price has dropped to its trigger level
        nxt = p['next_rung']
        if nxt < len(RUNG_DROPS):
            rung_trigger = p['rung0_entry'] * (1 + RUNG_DROPS[nxt])
            if price <= rung_trigger:
                deploy_rung(s, sym, price, nxt)

    # ---- 2. look for new breaches (deploy rung 0) ----
    for sym in universe:
        base = sym.split('/')[0]
        if is_excluded(base):
            continue
        if sym in s['positions']:
            continue
        if len(s['positions']) >= MAX_ACTIVE:
            break
        if deployable_cash(s) < RUNG_USD[0]:
            break

        price = fetch_live_price(sym)
        if price is None:
            continue
        floor = calculate_90_day_floor(sym)      # maturity gate lives here
        if floor is None:
            continue

        if not core_breach(sym, price, floor):
            continue

        # OBI: don't ladder into a toxic book
        if USE_OBI_GATE:
            gate = obi_gate(sym)
            if not gate.get('allow', False):
                print(f"  [CORE SKIP] {base:<6} OBI gate blocked "
                      f"(obi={gate.get('obi')}, {gate.get('dominant_side')})")
                continue

        # Order-flow veto: don't START a ladder into a falling knife or a
        # dead book. Gates rung 0 ONLY — deeper rungs are meant to fire in
        # ugly flow (that's the scale-down). Fails safe to cash.
        flow = check_flow(f"{base}-USD")
        if flow.veto:
            print(f"  [CORE SKIP] {base:<6} flow veto ({flow.reason})")
            continue

        deploy_rung(s, sym, price, 0)

    save_state(s)


def run_engine():
    print("=" * 78)
    print("      PRV1311 — CORE (mechanical bear/crash engine)")
    print("=" * 78)
    print(f"Pool : ${STARTING_CAPITAL_USD:,.2f} split {CORE_COUNT} ways")
    print(f"Team : up to {MAX_ACTIVE} active ladders @ ${BUCKET_USD:,.2f}/bucket")
    print(f"Reserve : {SIZING['reserve_slots']} slots (${RESERVE_USD:,.2f}) untouchable")
    print(f"Ladder : weights {LADDER_WEIGHTS} -> rungs ${RUNG_USD} at drops {[int(d*100) for d in RUNG_DROPS]}%")
    print(f"Rule : buys the breach, scales down, NEVER cuts | exit = weekly-spread target")
    print(f"Trigger : {'below 90-day floor (PLACEHOLDER — real trigger TBD)'}")
    print(f"Gate : OBI{' ON' if USE_OBI_GATE else ' OFF'} | Universe : market + watchlist")
    print(f"Fee : {FEE_RATE*100:.0f}% of each winning exit's profit -> fees wallet")
    print(f"Status : LIVE (Ctrl+C to stop)\n")

    s = load_state()
    while True:
        try:
            print(f"\n[{time.strftime('%H:%M:%S')}] Fetching universe...")
            universe = get_universe_markets(limit=50)
            print(f"  {len(universe)} assets (market + watchlist)")
            run_cycle(s, universe)

            held = 0.0
            for sym, p in s['positions'].items():
                pr = fetch_live_price(sym)
                if pr:
                    _, total_units, _ = position_blended(p)
                    p['current_price'] = pr
                    p['current_value'] = total_units * pr
                    held += total_units * pr
            total = s['USD_balance'] + held + s['treasury']
            save_state(s)
            push_core(s)

            active = [sym.split('/')[0] for sym in s['positions']]
            print("-" * 78)
            print(f"  Active ladders ({len(active)}/{MAX_ACTIVE}): {', '.join(active) if active else 'none'}")
            print(f"[{time.strftime('%H:%M:%S')}] Total ${total:,.2f} | Cash ${s['USD_balance']:,.2f} "
                  f"| Reserve ${RESERVE_USD:,.2f} | Treasury ${s['treasury']:,.2f} | Fees ${s['fees_wallet']:,.2f}")
            print("-" * 78)

            time.sleep(20 * 60)

        except KeyboardInterrupt:
            print("\n[CORE] stopped safely.")
            break
        except Exception as e:
            print(f"\n[CORE Error] {e}")
            time.sleep(60)


if __name__ == "__main__":
    run_engine()