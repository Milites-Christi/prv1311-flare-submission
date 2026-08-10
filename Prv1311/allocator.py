"""
================================================================================
PROJECT: Prv1311 — Rotating Bucket Allocator (Coinbase live data)
FILE: allocator.py
================================================================================
12 buckets @ $1,666: 5 CORE (distinct assets, rotate on green exit), 5 DIP
(-20% below entry), 1 RIDER, 1 RESERVE. Hard-lock when saturated, shadow ledger
for opportunity cost, on-deck queue for overflow. Never-cut, never-orphan.
Consumes ranking.filter_and_rank(). Coinbase /USD pairs.
================================================================================
"""

import json
import os
import time
from config import STARTING_CAPITAL_USD, EXCHANGE_ID, QUOTE, NUM_CORE_BUCKETS, DIP_TRIGGER_PCT
from screener import fetch_live_price, calculate_90_day_floor, rolling_7_day_high
from ranking import filter_and_rank
from rsi_scanner import run_market_scan

BUCKET_USD = STARTING_CAPITAL_USD / 12
RIDER_PULLBACK_PCT = 4.0
RIDER_TARGET_PCT = 7.0
LEDGER_FILE = 'data/alloc_ledger.json'


def pair_of(sym):
    return f"{sym}/{QUOTE}"


def fresh_state():
    return {
        'system': 'Prv1311-allocator',
        'USD_balance': STARTING_CAPITAL_USD,
        'treasury': 0.0,
        'core_buckets': [
            {'id': i, 'status': 'FREE', 'asset': None, 'entry_price': 0.0,
             'units': 0.0, 'usd_in': 0.0, 'dip_filled': False}
            for i in range(NUM_CORE_BUCKETS)
        ],
        'rider': {'holding': False, 'asset': None, 'entry_price': 0.0, 'units': 0.0},
        'reserve_used': False,
        'on_deck_queue': [],
        'shadow_ledger': [],
        'trade_history': [],
    }


def load_state():
    if not os.path.exists('data'):
        os.makedirs('data')
    if os.path.exists(LEDGER_FILE):
        return json.load(open(LEDGER_FILE))
    s = fresh_state()
    save_state(s)
    return s


def save_state(s):
    with open(LEDGER_FILE, 'w') as f:
        json.dump(s, f, indent=4)


def log(s, kind, sym, detail):
    s['trade_history'].append({'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
                               'kind': kind, 'asset': sym, 'detail': detail})


def held_assets(s):
    return {b['asset'] for b in s['core_buckets'] if b['status'] == 'HOLDING'}


def free_core_buckets(s):
    return [b for b in s['core_buckets'] if b['status'] == 'FREE']


def core_exit_price(entry_price):
    return entry_price * 1.15   # +15% green exit


def open_core_bucket(s, bucket, sym, price):
    usd = min(BUCKET_USD, s['USD_balance'])
    if usd <= 0:
        return False
    units = usd / price
    s['USD_balance'] -= usd
    bucket.update({'status': 'HOLDING', 'asset': sym, 'entry_price': price,
                   'units': units, 'usd_in': usd, 'dip_filled': False})
    log(s, 'CORE_ENTRY', sym, f"bucket {bucket['id']} @ ${price:.4f} (${usd:.2f})")
    print(f"  [CORE ENTRY] bucket {bucket['id']} -> {sym} @ ${price:.4f} (${usd:.2f})")
    return True


def close_core_bucket(s, bucket, price):
    proceeds = bucket['units'] * price
    profit = proceeds - bucket['usd_in']
    s['USD_balance'] += proceeds
    s['treasury'] += profit
    sym = bucket['asset']
    log(s, 'CORE_EXIT', sym, f"bucket {bucket['id']} @ ${price:.4f} profit ${profit:.2f}")
    print(f"  [CORE EXIT] bucket {bucket['id']} {sym} @ ${price:.4f}  +${profit:.2f} -> FREED")
    bucket.update({'status': 'FREE', 'asset': None, 'entry_price': 0.0,
                   'units': 0.0, 'usd_in': 0.0, 'dip_filled': False})


def fill_dip(s, bucket, price):
    usd = min(BUCKET_USD, s['USD_balance'])
    if usd <= 0:
        return
    units = usd / price
    s['USD_balance'] -= usd
    bucket['units'] += units
    bucket['usd_in'] += usd
    bucket['entry_price'] = bucket['usd_in'] / bucket['units']
    bucket['dip_filled'] = True
    log(s, 'DIP_FILL', bucket['asset'], f"bucket {bucket['id']} @ ${price:.4f} new avg ${bucket['entry_price']:.4f}")
    print(f"  [DIP FILL] bucket {bucket['id']} {bucket['asset']} @ ${price:.4f}  new avg ${bucket['entry_price']:.4f}")


def enqueue_or_shadow(s, candidate):
    sym = candidate['symbol']
    if sym in held_assets(s):
        return
    if any(q['symbol'] == sym for q in s['on_deck_queue']):
        return
    s['on_deck_queue'].append({'symbol': sym, 'score': candidate['score'],
                               'seen_price': candidate['price']})
    s['on_deck_queue'].sort(key=lambda x: x['score'], reverse=True)
    s['shadow_ledger'].append({'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
                               'symbol': sym, 'score': candidate['score'],
                               'would_be_entry': candidate['price'],
                               'reason': 'hard-lock: 5 core buckets saturated'})
    print(f"  [HARD-LOCK] {sym} rejected (buckets full) -> queue + shadow")


def pull_from_queue(s, bucket):
    while s['on_deck_queue']:
        nxt = s['on_deck_queue'].pop(0)
        if nxt['symbol'] in held_assets(s):
            continue
        price = fetch_live_price(pair_of(nxt['symbol']))
        if price is None:
            continue
        print(f"  [QUEUE->DEPLOY] {nxt['symbol']} -> freed bucket {bucket['id']}")
        open_core_bucket(s, bucket, nxt['symbol'], price)
        return True
    return False


def run_cycle(s, ranked):
    for b in s['core_buckets']:
        if b['status'] != 'HOLDING':
            continue
        price = fetch_live_price(pair_of(b['asset']))
        if price is None:
            continue
        if price >= core_exit_price(b['entry_price']):
            close_core_bucket(s, b, price)
            pull_from_queue(s, b)
        elif not b['dip_filled'] and price <= b['entry_price'] * (1 - DIP_TRIGGER_PCT):
            fill_dip(s, b, price)

    held = held_assets(s)
    frees = free_core_buckets(s)
    for cand in ranked:
        sym = cand['symbol']
        if sym in held:
            continue
        if frees:
            b = frees.pop(0)
            price = fetch_live_price(pair_of(sym))
            if price is None:
                continue
            if open_core_bucket(s, b, sym, price):
                held.add(sym)
        else:
            enqueue_or_shadow(s, cand)

    run_rider(s, ranked)
    save_state(s)


def run_rider(s, ranked):
    r = s['rider']
    if not r['holding']:
        if not ranked:
            return
        top = ranked[0]['symbol']
        pair = pair_of(top)
        price = fetch_live_price(pair)
        high7 = rolling_7_day_high(pair)
        floor = calculate_90_day_floor(pair)
        if price and high7 and floor:
            if price <= high7 * (1 - RIDER_PULLBACK_PCT / 100.0) and price >= floor:
                usd = min(BUCKET_USD, s['USD_balance'])
                if usd > 0:
                    r.update({'holding': True, 'asset': top,
                              'entry_price': price, 'units': usd / price})
                    s['USD_balance'] -= usd
                    log(s, 'RIDER_ENTRY', top, f"@ ${price:.4f}")
                    print(f"  [RIDER ENTRY] {top} @ ${price:.4f}")
    else:
        pair = pair_of(r['asset'])
        price = fetch_live_price(pair)
        if price and price >= r['entry_price'] * (1 + RIDER_TARGET_PCT / 100.0):
            proceeds = r['units'] * price
            profit = proceeds - r['units'] * r['entry_price']
            s['USD_balance'] += proceeds
            s['treasury'] += profit
            log(s, 'RIDER_EXIT', r['asset'], f"@ ${price:.4f} +${profit:.2f}")
            print(f"  [RIDER EXIT] {r['asset']} @ ${price:.4f}  +${profit:.2f}")
            r.update({'holding': False, 'asset': None, 'entry_price': 0.0, 'units': 0.0})


def run_engine():
    print("=" * 66)
    print("        PRV1311 — ROTATING BUCKET ALLOCATOR")
    print("=" * 66)
    print(f"Exchange : {EXCHANGE_ID}")
    print(f"Buckets  : {NUM_CORE_BUCKETS} core + 5 dip + 1 rider + 1 reserve = 12 @ ${BUCKET_USD:.0f}")
    print(f"Status   : LIVE (Ctrl+C to stop)\n")

    s = load_state()
    while True:
        try:
            print(f"\n[{time.strftime('%H:%M:%S')}] --- discovery + rank ---")
            fires = run_market_scan(verbose_progress=False)
            ranked, rejected = filter_and_rank(fires)
            print(f"  {len(ranked)} ranked, {len(rejected)} rejected")
            run_cycle(s, ranked)

            held = held_assets(s)
            total = s['USD_balance'] + s['treasury']
            for b in s['core_buckets']:
                if b['status'] == 'HOLDING':
                    pr = fetch_live_price(pair_of(b['asset']))
                    if pr:
                        total += b['units'] * pr
            if s['rider']['holding']:
                pr = fetch_live_price(pair_of(s['rider']['asset']))
                if pr:
                    total += s['rider']['units'] * pr
            print(f"  Held: {held or '{}'} | Queue: {len(s['on_deck_queue'])} | "
                  f"Cash: ${s['USD_balance']:,.0f} | Total: ${total:,.0f}")
            print("  (sleeping 15 min until next scan)")
            time.sleep(15 * 60)

        except KeyboardInterrupt:
            print("\n[Allocator] stopped safely.")
            break
        except Exception as e:
            print(f"\n[Allocator Error] {e}")
            time.sleep(60)


if __name__ == "__main__":
    run_engine()