"""
================================================================================
PROJECT: Prv1311 — Dogs Lab (experimental research fleet)
FILE: dogs.py
================================================================================
A TEST BENCH, not a finished strategy. For each liquid asset it:
  1. pulls the HIGH and LOW of the 24h / 72h / weekly windows
  2. runs the existing triple-confirmation (RSI + absorption + VWAP) for a
     probability read (0-3 confirmations)
  3. checks the candidate trigger: price >= DOG_PULLBACK_PCT below ANY window high
  4. checks the REGIME gate (daily reverting) and OBI gate (book toxicity)
  5. FIRES only if confirmations >= threshold AND regime reverting AND book clean
  6. LOGS ITS REASONING on every candidate -- windows, signals, regime, book, verdict.

UNIVERSE: scans the whole liquid market (top N by 24h volume) PLUS the client's
WATCHLIST tokens forced in — but only ones Coinbase actually lists (checks the
live market map, tries /USD then /USDC, skips misspelled/missing silently).

The decision log is the point: a record to learn from. Isolated pool + ledger.
Exit at +3.5%, never-cut, own reserve. Same 20%-of-profit fee.
================================================================================
"""

import json
import time
import os
from config import (
    QUOTE, MIN_24H_USD_VOLUME, STABLE_EXCLUDES, DENYLIST, WATCHLIST,
    RIDER_FLOOR_BUFFER,
    DOG_POOL_USD, DOG_COUNT, DOG_CEILING, DOG_FLOOR, DOG_RESERVE_RIDERS,
    DOG_PULLBACK_PCT, DOG_TARGET_PCT, DOG_WINDOWS, DOG_MIN_CONFIRMATIONS,
    DOG_LEDGER_FILE,
)
from screener import (exchange, fetch_live_price, calculate_90_day_floor,
                      run_triple_confirmation)
from sync_supabase import push_dogs
from orderbook_imbalance import obi_gate
from regime import classify_regime

FEE_RATE = 0.20
MAX_DECISION_LOG = 200
USE_OBI_GATE = True
USE_REGIME_GATE = True


def compute_sizing():
    if DOG_COUNT < 1:
        raise ValueError(f"DOG_COUNT must be >= 1 (got {DOG_COUNT}).")
    raw = DOG_POOL_USD / DOG_COUNT
    if raw < DOG_FLOOR:
        max_slots = int(DOG_POOL_USD // DOG_FLOOR)
        raise ValueError(
            f"Pool ${DOG_POOL_USD:,.0f} / {DOG_COUNT} = ${raw:,.2f} per dog, "
            f"below the ${DOG_FLOOR:,.0f} floor. Max dogs here: {max_slots}."
        )
    bucket = min(raw, DOG_CEILING)
    reserve_slots = max(0, DOG_RESERVE_RIDERS)
    active_slots = DOG_COUNT - reserve_slots
    if active_slots < 1:
        raise ValueError(
            f"DOG_RESERVE_RIDERS ({DOG_RESERVE_RIDERS}) leaves no active slots."
        )
    return {
        'bucket': bucket,
        'active_slots': active_slots,
        'reserve_slots': reserve_slots,
        'reserve_usd': bucket * reserve_slots,
        'starting_capital': DOG_POOL_USD,
    }


SIZING = compute_sizing()
BUCKET_USD = SIZING['bucket']
MAX_ACTIVE = SIZING['active_slots']
RESERVE_USD = SIZING['reserve_usd']
STARTING_CAPITAL_USD = SIZING['starting_capital']


def is_excluded(base):
    return base in STABLE_EXCLUDES or base in DENYLIST


def daily_closes(symbol, limit=120):
    """Daily closes for the regime gate. None on failure."""
    pair = symbol if '/' in symbol else f"{symbol}/{QUOTE}"
    try:
        ohlcv = exchange.fetch_ohlcv(pair, timeframe='1d', limit=limit)
        return [row[4] for row in ohlcv] if ohlcv else None
    except Exception:
        return None


def get_universe_markets(limit=50):
    """Top N liquid Coinbase /USD pairs by 24h volume, PLUS the client's
    WATCHLIST tokens forced in — but only ones Coinbase actually lists. Checks
    the live market map, prefers /USD then /USDC, skips missing tickers silently.
    The entry gates still decide whether a watchlist token actually trades."""
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

    # --- force the client's watchlist tokens in (only ones Coinbase lists) ---
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


def window_high_low(symbol, days):
    """Return (high, low) of daily closes over the last `days`. None on fail."""
    pair = symbol if '/' in symbol else f"{symbol}/{QUOTE}"
    try:
        ohlcv = exchange.fetch_ohlcv(pair, timeframe='1d', limit=days + 2)
        if not ohlcv or len(ohlcv) < 2:
            return None, None
        closes = [row[4] for row in ohlcv[-days:]]
        return max(closes), min(closes)
    except Exception as e:
        print(f"[window Error] {pair} {days}d: {e}")
        return None, None


def fresh_state():
    return {
        'system': 'Prv1311-dogs-lab',
        'USD_balance': STARTING_CAPITAL_USD,
        'treasury': 0.0,
        'fees_wallet': 0.0,
        'riders': {},
        'trade_history': [],
        'decision_log': [],
    }


def load_state():
    if not os.path.exists('data'):
        os.makedirs('data')
    if os.path.exists(DOG_LEDGER_FILE):
        s = json.load(open(DOG_LEDGER_FILE))
        s.setdefault('fees_wallet', 0.0)
        s.setdefault('decision_log', [])
        return s
    s = fresh_state()
    save_state(s)
    return s


def save_state(s):
    with open(DOG_LEDGER_FILE, 'w') as f:
        json.dump(s, f, indent=4)


def log_trade(s, action, sym, price, usd, units, profit=None, fee=None):
    row = {'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
           'engine': 'DOG', 'action': action, 'asset': sym.split('/')[0],
           'price': price, 'amount_usd': usd, 'units': units}
    if profit is not None:
        row['profit'] = profit
    if fee is not None:
        row['fee'] = fee
    s['trade_history'].append(row)


def log_decision(s, sym, price, windows, confirmations, verdict, reason, obi=None, regime=None):
    """The research record: what the lab saw and decided, fired or not."""
    entry = {
        'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
        'asset': sym.split('/')[0],
        'price': price,
        'windows': windows,
        'confirmations': confirmations,
        'regime': regime,
        'obi': obi,
        'verdict': verdict,
        'reason': reason,
    }
    s['decision_log'].append(entry)
    if len(s['decision_log']) > MAX_DECISION_LOG:
        s['decision_log'] = s['decision_log'][-MAX_DECISION_LOG:]


def deployable_cash(s):
    return s['USD_balance'] - RESERVE_USD


def open_dog(s, sym, price):
    if deployable_cash(s) < BUCKET_USD:
        return False
    usd = BUCKET_USD
    units = usd / price
    s['USD_balance'] -= usd
    s['riders'][sym] = {'asset': sym, 'entry_price': price,
                        'units': units, 'usd_in': usd}
    log_trade(s, 'BUY', sym, price, usd, units)
    print(f"  [DOG BUY] {sym.split('/')[0]:<6} @ ${price:.4f}  target ${price*(1+DOG_TARGET_PCT/100):.4f}  "
          f"({len(s['riders'])}/{MAX_ACTIVE} active)")
    return True


def close_dog(s, sym, price):
    r = s['riders'][sym]
    proceeds = r['units'] * price
    profit = proceeds - r['usd_in']
    fee = profit * FEE_RATE if profit > 0 else 0.0
    net_profit = profit - fee
    s['USD_balance'] += proceeds
    s['treasury'] += net_profit
    s['fees_wallet'] += fee
    log_trade(s, 'SELL', sym, price, proceeds, r['units'], profit=net_profit, fee=fee)
    print(f"  [DOG SELL] {sym.split('/')[0]:<6} @ ${price:.4f}  +{DOG_TARGET_PCT:.1f}%  "
          f"profit ${net_profit:.2f} (fee ${fee:.2f})")
    del s['riders'][sym]


def run_cycle(s, universe):
    # ---- exits first ----
    for sym in list(s['riders'].keys()):
        r = s['riders'][sym]
        price = fetch_live_price(sym)
        if price is None:
            continue
        target = r['entry_price'] * (1 + DOG_TARGET_PCT / 100.0)
        if price >= target:
            close_dog(s, sym, price)

    # ---- evaluate candidates (and LOG the reasoning) ----
    for sym in universe:
        base = sym.split('/')[0]
        if is_excluded(base):
            continue
        if sym in s['riders']:
            continue

        price = fetch_live_price(sym)
        if price is None:
            continue

        floor = calculate_90_day_floor(sym)
        if floor is None:
            continue

        windows = {}
        max_drop = 0.0
        for d in DOG_WINDOWS:
            hi, lo = window_high_low(sym, d)
            if hi is None or hi <= 0:
                windows[f'{d}d'] = None
                continue
            drop_pct = (hi - price) / hi * 100.0
            windows[f'{d}d'] = {'high': hi, 'low': lo, 'drop_pct': round(drop_pct, 2)}
            max_drop = max(max_drop, drop_pct)

        # GATE-PARITY AUDIT (2026-08-12): one-sided trigger, no catch-band
        # ceiling. rider_team.py/scavengers.py both pair their pullback
        # trigger with an upper crash-filter (RIDER_MAX_DROP_PCT /
        # SCAV_MAX_DROP_PCT) so a genuine breakdown routes to CORE instead of
        # being caught here as a "dip". Dogs never got the matching ceiling —
        # confirmed gap, not a design choice. Fix deferred to after
        # 2026-08-14 (see docs/CHANGELOG.md "Gate-parity audit — decisions").
        is_candidate = max_drop >= DOG_PULLBACK_PCT
        if not is_candidate:
            continue

        above_floor_buffer = price >= floor * RIDER_FLOOR_BUFFER

        tc = run_triple_confirmation(sym, verbose=False)
        confirmations = sum([
            bool(tc['rsi'] and tc['rsi'].get('signal') == 'OVERSOLD'),
            bool(tc['absorption'] and tc['absorption'].get('signal') == 'ABSORPTION'),
            bool(tc['vwap'] and tc['vwap'].get('signal') == 'OVEREXT_LOWER'),
        ])

        # regime read (daily) — logged either way
        reg = 'no_data'
        if USE_REGIME_GATE:
            closes = daily_closes(sym)
            reg = classify_regime(closes)['regime'] if closes else 'no_data'

        # order-book read — logged either way
        gate = obi_gate(sym) if USE_OBI_GATE else {'allow': True, 'obi': None}
        obi_val = gate.get('obi')

        # decide
        if not above_floor_buffer:
            log_decision(s, sym, price, windows, confirmations,
                         'SKIPPED', f'below floor buffer (floor*{RIDER_FLOOR_BUFFER})', obi_val, reg)
            continue
        if confirmations < DOG_MIN_CONFIRMATIONS:
            log_decision(s, sym, price, windows, confirmations,
                         'SKIPPED', f'{confirmations}/{DOG_MIN_CONFIRMATIONS} confirmations', obi_val, reg)
            continue
        if USE_REGIME_GATE and reg != 'reverting':
            log_decision(s, sym, price, windows, confirmations,
                         'SKIPPED', f'regime not reverting ({reg})', obi_val, reg)
            continue
        if USE_OBI_GATE and not gate.get('allow', False):
            log_decision(s, sym, price, windows, confirmations,
                         'SKIPPED', f'toxic order book (obi={obi_val}, {gate.get("dominant_side")})', obi_val, reg)
            continue
        if len(s['riders']) >= MAX_ACTIVE or deployable_cash(s) < BUCKET_USD:
            log_decision(s, sym, price, windows, confirmations,
                         'SKIPPED', 'no free slot / reserve floor reached', obi_val, reg)
            continue

        # FIRE
        if open_dog(s, sym, price):
            log_decision(s, sym, price, windows, confirmations,
                         'FIRED', f'{confirmations}/{DOG_MIN_CONFIRMATIONS} conf, drop {max_drop:.1f}%, {reg}, book clean', obi_val, reg)

    save_state(s)


def run_engine():
    print("=" * 78)
    print("      PRV1311 — DOGS LAB (experimental research fleet)")
    print("=" * 78)
    print(f"Pool : ${STARTING_CAPITAL_USD:,.0f} split {DOG_COUNT} ways")
    print(f"Team : up to {MAX_ACTIVE} active dogs @ ${BUCKET_USD:,.2f} each")
    print(f"Reserve : {SIZING['reserve_slots']} slots (${RESERVE_USD:,.2f}) untouchable")
    print(f"Windows : {DOG_WINDOWS} days (high+low) | candidate if >= {DOG_PULLBACK_PCT:.0f}% below any high")
    gate = f">= {DOG_MIN_CONFIRMATIONS}/3 triple-confirmation"
    if USE_REGIME_GATE:
        gate += " + regime"
    if USE_OBI_GATE:
        gate += " + OBI"
    print(f"Gate : {gate} to FIRE | exit +{DOG_TARGET_PCT:.1f}%")
    print(f"Universe : market top-50 by volume + watchlist (Coinbase-listed only)")
    print(f"Fee : {FEE_RATE*100:.0f}% of each winning flip's profit -> fees wallet")
    print(f"Status : LIVE (Ctrl+C to stop)  — logging every candidate's reasoning\n")

    s = load_state()
    while True:
        try:
            print(f"\n[{time.strftime('%H:%M:%S')}] Fetching universe...")
            universe = get_universe_markets(limit=50)
            print(f"  {len(universe)} assets (market + watchlist)")
            run_cycle(s, universe)

            held_val = 0.0
            for sym, r in s['riders'].items():
                pr = fetch_live_price(sym)
                if pr:
                    held_val += r['units'] * pr
                    r['current_price'] = pr
                    r['current_value'] = r['units'] * pr
            total = s['USD_balance'] + held_val + s['treasury']
            save_state(s)
            push_dogs(s)

            active = [sym.split('/')[0] for sym in s['riders']]
            print("-" * 78)
            print(f"  Active dogs ({len(active)}/{MAX_ACTIVE}): {', '.join(active) if active else 'none'} "
                  f"| decisions logged: {len(s['decision_log'])}")
            print(f"[{time.strftime('%H:%M:%S')}] Total ${total:,.2f} | Cash ${s['USD_balance']:,.2f} "
                  f"| Reserve ${RESERVE_USD:,.2f} | Treasury ${s['treasury']:,.2f} | Fees ${s['fees_wallet']:,.2f}")
            print("-" * 78)

            time.sleep(15 * 60)

        except KeyboardInterrupt:
            print("\n[Dogs Lab] stopped safely.")
            break
        except Exception as e:
            print(f"\n[Dogs Lab Error] {e}")
            time.sleep(60)


if __name__ == "__main__":
    run_engine()