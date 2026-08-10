"""
================================================================================
PROJECT: Prv1311 — Markov Signal Engine (translated from War-Room Brain 2 v44)
FILE: markov.py
================================================================================
THE CROWN JEWEL, ported 1:1 from the JS transform (338+ runs, 99% success).
Liquidity-sweep + reclaim + RSI-divergence engine. ATR-buffered stops, 2:1 R:R,
session gating, time-of-day sentiment.

CONFLUENCE GATE (wired above the fire path): before a long actually deploys, the
confluence gate scores momentum/flow/on-chain confirmation. PASS fires; CAUTION
or HOLD logs the signal but does NOT deploy. The confluence result is recorded on
every fired-candidate signal_log entry so you can study when it helped/hurt.
On-chain components are honestly missing until a feed is wired (never faked).

ISOLATED: own hourly data layer (markov_screener), own ledger, own page.
Signal-only: computes BOTH long and short, LOGS both, FIRES longs only, and
exits at target OR stop (this engine DOES use a stop — its 2:1 R:R identity).
================================================================================
"""

import json
import time
import os
import math
from datetime import datetime, timezone
from config import QUOTE, MARKOV_POOL_USD, MARKOV_BUCKET_USD, MARKOV_LEDGER_FILE
from markov_screener import fetch_hourly
from sync_supabase import push_markov
from confluence_gate import confluence_gate

# --- spec constants (P) ---
P = {
    'lbL': 8, 'lbR': 8, 'abuf': 0.25, 'win': 200, 'freq': 8,
    'rlen': 14, 'ob': 70, 'os': 30, 'dL': 5, 'dR': 5,
    'rmin': 8, 'rmax': 80, 'alen': 14, 'sbuf': 0.5, 'rr': 2, 'risk_usd': 100,
}

MARKOV_SYMBOLS = ['BTC/USD', 'ETH/USD', 'SOL/USD']

FEE_RATE = 0.20
NAN = float('nan')

# Confluence gate: PASS fires, CAUTION/HOLD skips (but logs). Set False to bypass.
USE_CONFLUENCE_GATE = True


def isnan(x):
    return isinstance(x, float) and math.isnan(x)


# ---------------------------------------------------------------------------
# Indicators — 1:1 translations of the JS
# ---------------------------------------------------------------------------
def atr(H, L, CL, n, length):
    tr = [0.0] * n
    o = [NAN] * n
    for i in range(n):
        if i == 0:
            tr[i] = H[i] - L[i]
        else:
            tr[i] = max(H[i] - L[i], abs(H[i] - CL[i - 1]), abs(L[i] - CL[i - 1]))
    s = 0.0
    for i in range(n):
        if i < length:
            s += tr[i]
            if i == length - 1:
                o[i] = s / length
        else:
            o[i] = (o[i - 1] * (length - 1) + tr[i]) / length
    return o


def rsi(CL, n, length):
    o = [NAN] * n
    ag = 0.0
    al = 0.0
    for i in range(1, n):
        c = CL[i] - CL[i - 1]
        g = max(c, 0.0)
        ls = max(-c, 0.0)
        if i <= length:
            ag += g
            al += ls
            if i == length:
                ag /= length
                al /= length
                o[i] = 100 - 100 / (1 + (100 if al == 0 else ag / al))
        else:
            ag = (ag * (length - 1) + g) / length
            al = (al * (length - 1) + ls) / length
            o[i] = 100 - 100 / (1 + (100 if al == 0 else ag / al))
    return o


def pivot_high(src, n, l, r):
    o = [NAN] * n
    for i in range(l, n - r):
        v = src[i]
        ok = True
        for j in range(i - l, i + r + 1):
            if j != i and src[j] > v:
                ok = False
                break
        if ok:
            o[i + r] = v
    return o


def pivot_low(src, n, l, r):
    o = [NAN] * n
    for i in range(l, n - r):
        v = src[i]
        ok = True
        for j in range(i - l, i + r + 1):
            if j != i and src[j] < v:
                ok = False
                break
        if ok:
            o[i + r] = v
    return o


# ---------------------------------------------------------------------------
# Core signal computation — 1:1 with the JS main loop
# ---------------------------------------------------------------------------
def compute_signal(candles):
    n = len(candles)
    base = {
        'signal': 0, 'direction': 'none', 'entry_price': None, 'stop_price': None,
        'target_price': None, 'size': None, 'session': None,
        'tod_hour_utc': None, 'tod_candles_sampled': n if n else None,
        'tod_bull_pct': None, 'tod_bear_pct': None, 'tod_sentiment': None,
    }
    if n < 100:
        return base

    H = [c['h'] for c in candles]
    L = [c['l'] for c in candles]
    CL = [c['c'] for c in candles]

    aR = atr(H, L, CL, n, P['alen'])
    aB = atr(H, L, CL, n, 14)
    osc = rsi(CL, n, P['rlen'])
    phP = pivot_high(H, n, P['lbL'], P['lbR'])
    plP = pivot_low(L, n, P['lbL'], P['lbR'])
    phO = pivot_high(osc, n, P['dL'], P['dR'])
    plO = pivot_low(osc, n, P['dL'], P['dR'])

    lsh = lsl = sdB = suB = lsB = NAN
    lpO = lpL = pBar = lhO = lhH = hBar = bdB = beB = NAN
    liveSig = 0
    liveSwept = NAN

    for i in range(1, n):
        if not isnan(phP[i]):
            lsh = phP[i]
        if not isnan(plP[i]):
            lsl = plP[i]

        if not isnan(lsh):
            u = lsh + P['abuf'] * aB[i]
            if CL[i - 1] <= u and CL[i] > u:
                suB = i
        if not isnan(lsl):
            dn = lsl - P['abuf'] * aB[i]
            if CL[i - 1] >= dn and CL[i] < dn:
                sdB = i

        rsd = (not isnan(sdB)) and (i - sdB) <= P['win']
        rsu = (not isnan(suB)) and (i - suB) <= P['win']
        rcU = (not isnan(lsl)) and CL[i - 1] <= lsl and CL[i] > lsl
        rcD = (not isnan(lsh)) and CL[i - 1] >= lsh and CL[i] < lsh

        if not isnan(plO[i]):
            s = i - P['dR']
            if not isnan(pBar):
                g = i - pBar
                if g >= P['rmin'] and g <= P['rmax']:
                    if osc[s] > lpO and L[s] < lpL and (lpO <= P['os'] or osc[s] <= P['os']):
                        bdB = i
            lpO = osc[s]
            lpL = L[s]
            pBar = i

        if not isnan(phO[i]):
            s = i - P['dR']
            if not isnan(hBar):
                g = i - hBar
                if g >= P['rmin'] and g <= P['rmax']:
                    if osc[s] < lhO and H[s] > lhH and (lhO >= P['ob'] or osc[s] >= P['ob']):
                        beB = i
            lhO = osc[s]
            lhH = H[s]
            hBar = i

        bdR = (not isnan(bdB)) and (i - bdB) <= P['win']
        beR = (not isnan(beB)) and (i - beB) <= P['win']
        lR = rsd and rcU and bdR
        sR = rsu and rcD and beR
        cf = isnan(lsB) or (i - lsB) >= P['freq']

        if lR and cf:
            lsB = i
            if i == n - 1:
                liveSig = 1
                liveSwept = lsl
        elif sR and cf:
            lsB = i
            if i == n - 1:
                liveSig = -1
                liveSwept = lsh

    # --- session + time-of-day sentiment ---
    now_hour = datetime.now(timezone.utc).hour
    in_london = 7 <= now_hour < 16
    in_ny = 13 <= now_hour < 21
    in_overlap = 13 <= now_hour < 16
    in_late_us = 21 <= now_hour < 23
    in_asian = 0 <= now_hour < 7
    active_session = ('london_ny_overlap' if in_overlap else 'london' if in_london
                      else 'new_york' if in_ny else 'late_us' if in_late_us
                      else 'asian' if in_asian else 'off_hours')
    high_liquidity = in_london or in_ny or in_late_us

    tod = [c for c in candles
           if c['ts'] > 0 and datetime.fromtimestamp(
               c['ts'] / 1000 if c['ts'] >= 1e12 else c['ts'], timezone.utc).hour == now_hour]
    tod_bull = sum(1 for c in tod if c['c'] > c['o'])
    tod_bear = sum(1 for c in tod if c['c'] < c['o'])
    tod_total = tod_bull + tod_bear
    tod_bull_pct = round(tod_bull / tod_total * 100) if tod_total else None
    tod_bear_pct = round(tod_bear / tod_total * 100) if tod_total else None
    sent_thresh = 55 if in_overlap else 58 if in_late_us else 60
    if tod_total:
        tod_sentiment = ('bullish' if tod_bull_pct >= sent_thresh
                         else 'bearish' if tod_bear_pct >= sent_thresh else 'neutral')
    else:
        tod_sentiment = 'insufficient_data'

    result = dict(base)
    result.update({
        'session': active_session, 'tod_hour_utc': now_hour,
        'tod_candles_sampled': tod_total, 'tod_bull_pct': tod_bull_pct,
        'tod_bear_pct': tod_bear_pct, 'tod_sentiment': tod_sentiment,
    })

    if not high_liquidity or not liveSig:
        return result

    last = n - 1
    entry = CL[last]
    if liveSig == 1:
        stop = liveSwept - P['sbuf'] * aR[last]
        target = entry + P['rr'] * (entry - stop)
    else:
        stop = liveSwept + P['sbuf'] * aR[last]
        target = entry - P['rr'] * (stop - entry)

    stop_dist = abs(entry - stop)
    if not math.isfinite(stop_dist) or stop_dist <= 0:
        return result

    size = round(P['risk_usd'] / stop_dist, 6)
    result.update({
        'signal': liveSig,
        'direction': 'long' if liveSig == 1 else 'short',
        'entry_price': round(entry, 2),
        'stop_price': round(stop, 2),
        'target_price': round(target, 2),
        'size': size,
    })
    return result


# ---------------------------------------------------------------------------
# Paper-trading wrapper — fires LONGS only, logs BOTH
# ---------------------------------------------------------------------------
def fresh_state():
    return {
        'system': 'Prv1311-markov',
        'USD_balance': MARKOV_POOL_USD,
        'treasury': 0.0,
        'fees_wallet': 0.0,
        'positions': {},
        'signal_log': [],
        'trade_history': [],
    }


def load_state():
    if not os.path.exists('data'):
        os.makedirs('data')
    if os.path.exists(MARKOV_LEDGER_FILE):
        s = json.load(open(MARKOV_LEDGER_FILE))
        s.setdefault('fees_wallet', 0.0)
        s.setdefault('signal_log', [])
        s.setdefault('positions', {})
        return s
    s = fresh_state()
    save_state(s)
    return s


def save_state(s):
    with open(MARKOV_LEDGER_FILE, 'w') as f:
        json.dump(s, f, indent=4)


def log_signal(s, sym, sig, confluence=None):
    entry = {'ts': time.strftime('%Y-%m-%d %H:%M:%S'), 'asset': sym.split('/')[0]}
    entry.update(sig)
    if confluence is not None:
        entry['confluence_score'] = confluence.get('confluence_score')
        entry['confluence_decision'] = confluence.get('decision')
        entry['confluence_gate'] = confluence.get('gate')
    s['signal_log'].append(entry)
    if len(s['signal_log']) > 300:
        s['signal_log'] = s['signal_log'][-300:]


def open_long(s, sym, sig):
    if s['USD_balance'] < MARKOV_BUCKET_USD:
        return
    usd = MARKOV_BUCKET_USD
    entry = sig['entry_price']
    units = usd / entry
    s['USD_balance'] -= usd
    s['positions'][sym] = {
        'asset': sym, 'entry_price': entry, 'units': units, 'usd_in': usd,
        'stop_price': sig['stop_price'], 'target_price': sig['target_price'],
    }
    s['trade_history'].append({
        'ts': time.strftime('%Y-%m-%d %H:%M:%S'), 'engine': 'MARKOV',
        'action': 'BUY', 'asset': sym.split('/')[0], 'price': entry,
        'amount_usd': usd, 'units': units,
    })
    print(f"  [MARKOV LONG] {sym.split('/')[0]:<5} entry ${entry:.2f}  stop ${sig['stop_price']:.2f}  target ${sig['target_price']:.2f}")


def manage_position(s, sym, price):
    p = s['positions'][sym]
    hit_target = price >= p['target_price']
    hit_stop = price <= p['stop_price']
    if not (hit_target or hit_stop):
        return
    proceeds = p['units'] * price
    profit = proceeds - p['usd_in']
    fee = profit * FEE_RATE if profit > 0 else 0.0
    net = profit - fee
    s['USD_balance'] += proceeds
    s['treasury'] += net
    s['fees_wallet'] += fee
    reason = 'TARGET' if hit_target else 'STOP'
    s['trade_history'].append({
        'ts': time.strftime('%Y-%m-%d %H:%M:%S'), 'engine': 'MARKOV',
        'action': 'SELL', 'asset': sym.split('/')[0], 'price': round(price, 2),
        'amount_usd': round(proceeds, 2), 'units': p['units'],
        'profit': round(net, 2), 'fee': round(fee, 2), 'exit_reason': reason,
    })
    print(f"  [MARKOV EXIT] {sym.split('/')[0]:<5} @ ${price:.2f} ({reason})  profit ${net:.2f} (fee ${fee:.2f})")
    del s['positions'][sym]


def run_cycle(s):
    for sym in MARKOV_SYMBOLS:
        candles = fetch_hourly(sym, limit=2500)
        if not candles:
            continue
        price = candles[-1]['c']

        if sym in s['positions']:
            manage_position(s, sym, price)

        sig = compute_signal(candles)

        # --- CONFLUENCE GATE: score a fired long before it deploys ---
        confluence = None
        if sig['signal'] == 1 and USE_CONFLUENCE_GATE:
            confluence = confluence_gate(sym, 'long')

        log_signal(s, sym, sig, confluence)   # log EVERY signal (+ confluence if scored)

        # fire LONGS only, and only if confluence PASSES
        if sig['signal'] == 1 and sym not in s['positions']:
            if not USE_CONFLUENCE_GATE:
                open_long(s, sym, sig)
            elif confluence and confluence.get('gate') == 'PASS':
                open_long(s, sym, sig)
            else:
                dec = confluence.get('decision') if confluence else 'no_score'
                sc = confluence.get('confluence_score') if confluence else '-'
                print(f"  [MARKOV SKIP] {sym.split('/')[0]:<5} confluence {dec} (score {sc}) — logged, not fired")

    save_state(s)


def run_engine():
    print("=" * 78)
    print("      PRV1311 — MARKOV SIGNAL ENGINE (crown jewel, ported)")
    print("=" * 78)
    print(f"Scans : {', '.join(MARKOV_SYMBOLS)}  (1h candles)")
    print(f"Logic : liquidity sweep + reclaim + RSI divergence | 2:1 R:R | session-gated")
    print(f"Mode  : signal-only — computes long+short, LOGS both, FIRES longs only")
    print(f"Gate  : confluence gate above fire path — PASS fires, CAUTION/HOLD logs-only" if USE_CONFLUENCE_GATE else "Gate  : confluence gate OFF")
    print(f"Pool  : ${MARKOV_POOL_USD:,.0f} @ ${MARKOV_BUCKET_USD:,.0f}/position")
    print(f"Status: LIVE (Ctrl+C to stop)\n")

    s = load_state()
    while True:
        try:
            print(f"\n[{time.strftime('%H:%M:%S')}] Markov scan...")
            run_cycle(s)

            held = 0.0
            for sym, p in s['positions'].items():
                c = fetch_hourly(sym, limit=3)
                if c:
                    pr = c[-1]['c']
                    p['current_price'] = pr
                    p['current_value'] = p['units'] * pr
                    held += p['units'] * pr
            total = s['USD_balance'] + held + s['treasury']
            save_state(s)
            push_markov(s)

            longs = list(s['positions'].keys())
            recent = s['signal_log'][-len(MARKOV_SYMBOLS):]
            sig_summary = ', '.join(f"{r['asset']}:{r['direction']}" for r in recent)
            print("-" * 78)
            print(f"  Signals: {sig_summary}")
            print(f"  Open longs: {', '.join(x.split('/')[0] for x in longs) if longs else 'none'}")
            print(f"[{time.strftime('%H:%M:%S')}] Total ${total:,.2f} | Cash ${s['USD_balance']:,.2f} "
                  f"| Treasury ${s['treasury']:,.2f} | Fees ${s['fees_wallet']:,.2f}")
            print("-" * 78)

            time.sleep(15 * 60)

        except KeyboardInterrupt:
            print("\n[Markov] stopped safely.")
            break
        except Exception as e:
            print(f"\n[Markov Error] {e}")
            time.sleep(60)


if __name__ == "__main__":
    run_engine()