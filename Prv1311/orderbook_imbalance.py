"""
================================================================================
PROJECT: Prv1311 — Order-Book Imbalance (OBI) — REAL live gate
FILE: orderbook_imbalance.py
================================================================================
Ported from War-Room Grid-BOT "Parse Order Book" — but with the ORIGINAL'S FATAL
FLAW FIXED. The source computed OBI but was NEVER wired to a real order book and
never actually blocked a trade — a DEAD/FAKE gate. Its own header warned: "Do
NOT inherit a fake safety gate." So this version wires it to REAL Coinbase depth
via ccxt fetch_order_book, and the gate returns a genuine block/allow decision.

WHAT OBI IS: over the top N book levels,
    obi = total_bid_size / (total_bid_size + total_ask_size)
    obi ~0.5  = balanced book
    obi >0.75 = wall of bids  (possible spoofed pump / thin real supply)
    obi <0.25 = wall of asks  (dump pressure)
    toxic_flow = obi > 0.75 or obi < 0.25  -> book too one-sided to trust

REAL GATE: obi_gate(symbol) fetches live depth and returns allow=False when flow
is toxic. Fleets can call it before firing: if not obi_gate(sym)['allow']: skip.
Fails SAFE — if depth can't be fetched, allow=False (don't trade blind).

CONSTANTS: LEVELS=15 (overridable); toxic if obi>0.75 or obi<0.25.
================================================================================
"""

import json
import time
import os
from config import QUOTE, WATCHLIST, OBI_LEDGER_FILE
from screener import exchange
from sync_supabase import push_obi

LEVELS = 15
TOXIC_HIGH = 0.75
TOXIC_LOW = 0.25


def compute_obi(bids, asks, levels=LEVELS):
    """PURE FUNCTION. bids/asks = lists of [price, size] (ccxt order-book format).
    Returns {obi, toxic_flow, dominant_side}. Mirrors the source math."""
    if not bids or not asks:
        return {'obi': 0.5, 'toxic_flow': True, 'dominant_side': 'unknown'}

    bid_sum = 0.0
    for i, lvl in enumerate(bids):
        if i >= levels:
            break
        sz = float(lvl[1]) if len(lvl) > 1 else 0.0
        if sz > 0:
            bid_sum += sz

    ask_sum = 0.0
    for i, lvl in enumerate(asks):
        if i >= levels:
            break
        sz = float(lvl[1]) if len(lvl) > 1 else 0.0
        if sz > 0:
            ask_sum += sz

    total = bid_sum + ask_sum
    if total <= 0:
        return {'obi': 0.5, 'toxic_flow': True, 'dominant_side': 'unknown'}

    obi = bid_sum / total
    toxic = obi > TOXIC_HIGH or obi < TOXIC_LOW
    side = 'bid' if obi >= 0.5 else 'ask'
    return {'obi': round(obi, 4), 'toxic_flow': toxic, 'dominant_side': side,
            'bid_size': round(bid_sum, 4), 'ask_size': round(ask_sum, 4)}


def obi_gate(symbol, levels=LEVELS):
    """REAL GATE. Fetches LIVE Coinbase depth and returns a genuine decision:
      {allow, obi, toxic_flow, dominant_side, ...}
    allow=False when flow is toxic OR depth can't be fetched (fails SAFE).
    Fleets call: if not obi_gate(sym)['allow']: skip the entry."""
    pair = symbol if '/' in symbol else f"{symbol}/{QUOTE}"
    try:
        ob = exchange.fetch_order_book(pair, limit=max(levels, 20))
        bids = ob.get('bids') or []
        asks = ob.get('asks') or []
    except Exception as e:
        # fail SAFE — don't trade on a book we couldn't read
        return {'allow': False, 'obi': None, 'toxic_flow': True,
                'dominant_side': 'unknown', 'error': str(e)}

    r = compute_obi(bids, asks, levels)
    r['allow'] = not r['toxic_flow']
    return r


# ---------------------------------------------------------------------------
# STANDALONE LAB — live OBI across the watchlist
# ---------------------------------------------------------------------------
LAB_SYMBOLS = [f"{t}/{QUOTE}" for t in WATCHLIST[:20]]


def run_cycle():
    rows = []
    for sym in LAB_SYMBOLS:
        r = obi_gate(sym)
        if r.get('error'):
            continue
        r['asset'] = sym.split('/')[0]
        rows.append(r)
        flag = 'TOXIC' if r['toxic_flow'] else 'ok'
        print(f"  {r['asset']:<6} OBI {r['obi']}  {r['dominant_side']:<4} [{flag}]  allow={r['allow']}")

    state = {'system': 'Prv1311-obi',
             'rows': rows,
             'updated': time.strftime('%Y-%m-%d %H:%M:%S')}
    with open(OBI_LEDGER_FILE, 'w') as f:
        json.dump(state, f, indent=4)
    push_obi(state)
    return state


def run_engine():
    print("=" * 78)
    print("      PRV1311 — ORDER-BOOK IMBALANCE (real live gate)")
    print("=" * 78)
    print(f"Scans : {len(LAB_SYMBOLS)} watchlist assets — LIVE Coinbase depth ({LEVELS} levels)")
    print(f"Logic : obi = bid/(bid+ask) | toxic if >{TOXIC_HIGH} or <{TOXIC_LOW} | fails SAFE")
    print(f"Gate  : obi_gate(sym)['allow'] — REAL block/allow, wired to live depth")
    print(f"Status: LIVE (Ctrl+C to stop)\n")
    if not os.path.exists('data'):
        os.makedirs('data')
    while True:
        try:
            print(f"\n[{time.strftime('%H:%M:%S')}] OBI scan (live depth)...")
            state = run_cycle()
            toxic = [r['asset'] for r in state['rows'] if r['toxic_flow']]
            print("-" * 78)
            print(f"  Toxic flow / blocked ({len(toxic)}): {', '.join(toxic) if toxic else 'none'}")
            print("-" * 78)
            time.sleep(5 * 60)   # depth moves fast; scan often
        except KeyboardInterrupt:
            print("\n[OBI Lab] stopped safely.")
            break
        except Exception as e:
            print(f"\n[OBI Lab Error] {e}")
            time.sleep(60)


if __name__ == "__main__":
    run_engine()