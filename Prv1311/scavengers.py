"""
================================================================================
PROJECT: Prv1311 — Scavengers (isolated velocity engine)
FILE: scavengers.py
================================================================================
A SMALL, SEPARATE team of high-velocity riders. Same institutional mechanics as
the main Rider Team, but tuned to scavenge routine market chop:
  - enter on a -5% pullback from the 3-DAY high (shallower + shorter window)
  - only above the floor buffer (freefall is CORE's job)
  - exit at +5%, bank profit, free the slot
  - never-cut, own reserve, own pool -- FULLY ISOLATED from the main team.
  - OBI GATE: skips entries into toxic, one-sided order books.
  - REGIME GATE: only fires on DAILY mean-reverting assets (skips trenders).
  Both gates BLOCK entries only — never sell, faithful to never-cut.

UNIVERSE: scans the whole liquid market (top N by 24h volume) PLUS the client's
WATCHLIST tokens forced in — but only ones Coinbase actually lists (checks the
live market map, tries /USD then /USDC, skips misspelled/missing silently).

This does NOT touch rider_team.py, its pool, or its ledger. Separate everything.

FEE: same 20%-of-profit-on-winning-flips as the main team.
================================================================================
"""

import json
import time
import os
import uuid
from config import (
    QUOTE, MIN_24H_USD_VOLUME, STABLE_EXCLUDES, DENYLIST, WATCHLIST,
    RIDER_FLOOR_BUFFER,
    SCAV_POOL_USD, SCAV_COUNT, SCAV_CEILING, SCAV_FLOOR, SCAV_RESERVE_RIDERS,
    SCAV_PULLBACK_PCT, SCAV_LOOKBACK_DAYS, SCAV_TARGET_PCT, SCAV_LEDGER_FILE,
    TAKER_FEE_PCT, MAKER_FEE_PCT, SCAV_EXEC_STYLE, SCAV_MAX_DROP_PCT,
)
from screener import (exchange, fetch_live_price, calculate_90_day_floor,
                      rolling_7_day_high, get_daily_ohlcv, clear_daily_cache)
from sync_supabase import push_scavengers
from orderbook_imbalance import obi_gate
from regime import classify_regime
from anomaly_gate import check_anomaly
from footprint_gate import check_flow
from rider_decision_log import (log_decision, log_cycle, flush_decisions,
                                get_candle_count, maturity_ok)

FEE_RATE = 0.20
EXCHANGE_FEE_PCT = TAKER_FEE_PCT if SCAV_EXEC_STYLE == 'taker' else MAKER_FEE_PCT
USE_OBI_GATE = True
USE_REGIME_GATE = True     # only fire on DAILY-reverting assets
# Flow gate coverage reality: footprint_worker.py covers 20 symbols; Scav scans
# ~76. Most candidates that reach this gate will come back BLIND_NO_DATA --
# that's honest reporting of real coverage, not a broken gate. See footprint_gate.py.
USE_FLOW_GATE = True

# Provenance for the decision log only -- which symbols in the last-built universe
# came from the real market scan (with a real, already-computed 24h volume) vs.
# were force-added from WATCHLIST without ever being liquidity-vetted. Read by
# run_cycle(); written only inside get_universe_markets(). Same pattern as
# rider_team.py.
_last_market_scan_set = set()
_last_market_scan_volumes = {}


def compute_sizing():
    """Derive bucket, active slots, reserve from the SCAV_* config knobs."""
    if SCAV_COUNT < 1:
        raise ValueError(f"SCAV_COUNT must be >= 1 (got {SCAV_COUNT}).")
    raw = SCAV_POOL_USD / SCAV_COUNT
    if raw < SCAV_FLOOR:
        max_slots = int(SCAV_POOL_USD // SCAV_FLOOR)
        raise ValueError(
            f"Pool ${SCAV_POOL_USD:,.0f} / {SCAV_COUNT} = ${raw:,.2f} per scavenger, "
            f"below the ${SCAV_FLOOR:,.0f} floor. Max scavengers here: {max_slots}. "
            f"Lower SCAV_COUNT or raise SCAV_POOL_USD."
        )
    bucket = min(raw, SCAV_CEILING)
    reserve_slots = max(0, SCAV_RESERVE_RIDERS)
    active_slots = SCAV_COUNT - reserve_slots
    if active_slots < 1:
        raise ValueError(
            f"SCAV_RESERVE_RIDERS ({SCAV_RESERVE_RIDERS}) leaves no active slots "
            f"out of SCAV_COUNT ({SCAV_COUNT})."
        )
    return {
        'bucket': bucket,
        'active_slots': active_slots,
        'reserve_slots': reserve_slots,
        'reserve_usd': bucket * reserve_slots,
        'starting_capital': SCAV_POOL_USD,
    }


SIZING = compute_sizing()
BUCKET_USD = SIZING['bucket']
MAX_ACTIVE = SIZING['active_slots']
RESERVE_USD = SIZING['reserve_usd']
STARTING_CAPITAL_USD = SIZING['starting_capital']


def is_excluded(base):
    return base in STABLE_EXCLUDES or base in DENYLIST


def daily_closes(symbol, limit=120):
    """Daily closes for the regime gate + anomaly gate. None on failure.
    Routed through screener's shared per-cycle cache (Task E) -- same values,
    fewer round trips."""
    try:
        ohlcv = get_daily_ohlcv(symbol, limit)
        return [row[4] for row in ohlcv] if ohlcv else None
    except Exception:
        return None


def get_universe_markets(limit=50):
    """Top N liquid Coinbase /USD pairs by 24h volume, PLUS the client's
    WATCHLIST tokens forced in — but only ones Coinbase actually lists. Checks
    the live market map, prefers /USD then /USDC, skips missing tickers silently.
    The entry gates still decide whether a watchlist token actually trades."""
    global _last_market_scan_set, _last_market_scan_volumes
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
        _last_market_scan_set = set(universe)
        _last_market_scan_volumes = dict(pairs[:limit])
    except Exception as e:
        print(f"[!] universe fetch error: {e}")
        universe = ['BTC/USD', 'ETH/USD', 'SOL/USD', 'XRP/USD', 'ADA/USD', 'XLM/USD']
        # Fallback-stub symbols were never actually liquidity-screened -- see
        # rider_team.py's identical comment. volume_24h/liquidity_ok correctly
        # stay null for these.
        _last_market_scan_set = set(universe)
        _last_market_scan_volumes = {}

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


def fresh_state():
    return {
        'system': 'Prv1311-scavengers',
        'USD_balance': STARTING_CAPITAL_USD,
        'treasury': 0.0,
        'fees_wallet': 0.0,
        'riders': {},
        'trade_history': [],
    }


def load_state():
    if not os.path.exists('data'):
        os.makedirs('data')
    if os.path.exists(SCAV_LEDGER_FILE):
        s = json.load(open(SCAV_LEDGER_FILE))
        s.setdefault('fees_wallet', 0.0)
        return s
    s = fresh_state()
    save_state(s)
    return s


def save_state(s):
    with open(SCAV_LEDGER_FILE, 'w') as f:
        json.dump(s, f, indent=4)


def log(s, action, sym, price, usd, units, profit=None, fee=None,
        exchange_fee=None, fee_model=None, limit_price=None, polled_price=None):
    row = {'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
           'engine': 'SCAVENGER', 'action': action, 'asset': sym.split('/')[0],
           'price': price, 'amount_usd': usd, 'units': units}
    if profit is not None:
        row['profit'] = profit
    if fee is not None:
        row['fee'] = fee
    if exchange_fee is not None:
        row['exchange_fee'] = exchange_fee
    if fee_model is not None:
        row['fee_model'] = fee_model
    if limit_price is not None:
        row['limit_price'] = limit_price
    if polled_price is not None:
        row['polled_price'] = polled_price
    s['trade_history'].append(row)


def deployable_cash(s):
    return s['USD_balance'] - RESERVE_USD


def open_scav(s, sym, fill_price, polled_price):
    """fill_price is the resting limit price (pullback_trigger) -- what a real
    maker order would have actually filled at. polled_price is the live ticker
    read that confirmed the trigger crossed; recorded only to measure the gap."""
    if deployable_cash(s) < BUCKET_USD:
        return False
    usd = BUCKET_USD
    # Exchange fee comes out of what you receive, not extra cash on top -- cash
    # spent stays exactly BUCKET_USD (unchanged check above), fewer units acquired.
    entry_exchange_fee = usd * (EXCHANGE_FEE_PCT / 100.0)
    units = (usd - entry_exchange_fee) / fill_price
    s['USD_balance'] -= usd
    s['riders'][sym] = {'asset': sym, 'entry_price': fill_price,
                        'units': units, 'usd_in': usd,
                        'entry_exchange_fee': entry_exchange_fee}
    log(s, 'BUY', sym, fill_price, usd, units,
        exchange_fee=entry_exchange_fee, fee_model='v2',
        limit_price=fill_price, polled_price=polled_price)
    print(f"  [SCAV BUY] {sym.split('/')[0]:<6} limit ${fill_price:.4f} (polled ${polled_price:.4f})  "
          f"target ${fill_price*(1+SCAV_TARGET_PCT/100):.4f}  ({len(s['riders'])}/{MAX_ACTIVE} active)")
    return True


def close_scav(s, sym, fill_price, polled_price):
    """fill_price is the resting limit price (entry * (1+SCAV_TARGET_PCT/100)) --
    what a real maker order would have actually filled at. polled_price is the
    live ticker read that confirmed the target crossed; recorded for the gap."""
    r = s['riders'][sym]
    gross_proceeds = r['units'] * fill_price
    exit_exchange_fee = gross_proceeds * (EXCHANGE_FEE_PCT / 100.0)
    proceeds = gross_proceeds - exit_exchange_fee    # cash actually received, net of exchange's cut
    profit = proceeds - r['usd_in']                  # exchange-fee-net profit (entry fee already
                                                      # baked in via reduced units at open) --
                                                      # THIS is what the 20% platform split runs on
    fee = profit * FEE_RATE if profit > 0 else 0.0
    net_profit = profit - fee
    s['USD_balance'] += proceeds
    s['treasury'] += net_profit
    s['fees_wallet'] += fee
    log(s, 'SELL', sym, fill_price, proceeds, r['units'], profit=net_profit, fee=fee,
        exchange_fee=exit_exchange_fee, fee_model='v2',
        limit_price=fill_price, polled_price=polled_price)
    print(f"  [SCAV SELL] {sym.split('/')[0]:<6} limit ${fill_price:.4f} (polled ${polled_price:.4f})  "
          f"+{SCAV_TARGET_PCT:.0f}%  profit ${net_profit:.2f} (fee ${fee:.2f}, exchange fee ${exit_exchange_fee:.2f})")
    del s['riders'][sym]


def run_cycle(s, universe):
    clear_daily_cache()  # explicit, not TTL -- a stale daily series carried
                         # across cycles is worse than an extra call (Task E)
    cycle_id = str(uuid.uuid4())
    symbols_evaluated = 0
    halt_reason = None
    halt_at_symbol = None

    # exits first
    for sym in list(s['riders'].keys()):
        r = s['riders'][sym]
        price = fetch_live_price(sym)
        if price is None:
            continue
        target = r['entry_price'] * (1 + SCAV_TARGET_PCT / 100.0)
        if price >= target:
            close_scav(s, sym, target, price)

    # entries: 5% pullback from the SHORTER (3-day) high
    for sym in universe:
        base = sym.split('/')[0]

        if len(s['riders']) >= MAX_ACTIVE:
            halt_reason = 'team_full'
            halt_at_symbol = sym
            break
        if deployable_cash(s) < BUCKET_USD:
            halt_reason = 'cash_floor'
            halt_at_symbol = sym
            break

        symbols_evaluated += 1
        vol = _last_market_scan_volumes.get(sym)
        record = {
            'cycle_id': cycle_id, 'symbol': sym, 'fleet': 'scav',
            'source': 'market_scan' if sym in _last_market_scan_set else 'watchlist',
            'price': None,
            # rolling_7d_high column holds Scav's 3-day high (SCAV_LOOKBACK_DAYS) --
            # same column, fleet-dependent lookback, distinguished by the fleet field.
            'rolling_7d_high': None, 'pullback_pct': None,
            'floor_value': None, 'floor_buffer_ok': None,
            'candle_count': None, 'maturity_ok': None,
            'volume_24h': vol, 'liquidity_ok': (vol >= MIN_24H_USD_VOLUME) if vol is not None else None,
            'regime_label': None, 'regime_ok': None,
            'obi_value': None, 'obi_ok': None,
            'flow_verdict': None, 'flow_reason': None,
            'fired': False, 'block_reason': None,
            'limit_price': None, 'polled_price': None,
        }

        if is_excluded(base):
            record['block_reason'] = 'excluded'
            log_decision(record)
            continue
        if sym in s['riders']:
            record['block_reason'] = 'already_held'
            log_decision(record)
            continue

        price = fetch_live_price(sym)
        record['price'] = price
        if price is None:
            record['block_reason'] = 'price_fetch_failed'
            log_decision(record)
            continue

        # --- ANOMALY GATE: quarantine a name coming off a blow-off pump, before
        # any further evaluation -- a dip off an artificially-pumped high isn't a
        # real dip. Must run BEFORE the pullback/catch-band check by design. ---
        anomaly = check_anomaly(daily_closes(sym, limit=60))
        if anomaly.veto:
            print(f"  [SCAV SKIP] {base:<6} anomaly veto (r7={anomaly.r7:.3f}, z={anomaly.z})")
            record['block_reason'] = 'anomaly_veto'
            log_decision(record)
            continue

        floor = calculate_90_day_floor(sym)          # maturity gate still applies
        record['floor_value'] = floor
        cc = get_candle_count(sym)
        record['candle_count'] = cc
        record['maturity_ok'] = maturity_ok(cc)
        if floor is None:
            record['block_reason'] = 'floor_fetch_failed'
            log_decision(record)
            continue

        high_short = rolling_7_day_high(sym, lookback_days=SCAV_LOOKBACK_DAYS)
        record['rolling_7d_high'] = high_short
        if high_short is None:
            record['block_reason'] = 'high7_fetch_failed'
            log_decision(record)
            continue

        pullback_trigger = high_short * (1 - SCAV_PULLBACK_PCT / 100.0)
        # Catch-band floor: below this is a real breakdown, not chop -- that's
        # CORE's mechanical ladder's job, not a rejected opportunity for Scav.
        catch_floor = high_short * (1 - SCAV_MAX_DROP_PCT / 100.0)
        above_floor_buffer = price >= floor * RIDER_FLOOR_BUFFER
        pullback_ok = price <= pullback_trigger
        in_catch_band = price >= catch_floor
        record['pullback_pct'] = (high_short - price) / high_short * 100.0
        record['floor_buffer_ok'] = above_floor_buffer
        # Known from here on regardless of whether this candidate ultimately
        # fires -- what price it WOULD fill at if it does.
        record['limit_price'] = pullback_trigger
        record['polled_price'] = price
        if not (pullback_ok and in_catch_band and above_floor_buffer):
            if not pullback_ok:
                record['block_reason'] = 'pullback_insufficient'
            elif not in_catch_band:
                pct_below_high = record['pullback_pct']
                print(f"  [SCAV SKIP] {base:<6} below_catch_band "
                      f"(pct_below_high={pct_below_high:.1f}%, max_drop={SCAV_MAX_DROP_PCT:.0f}%)")
                record['block_reason'] = 'below_catch_band'
            else:
                record['block_reason'] = 'floor_buffer_fail'
            log_decision(record)
            continue

        # --- REGIME GATE: accumulation needs rangers, skip daily-trenders ---
        if USE_REGIME_GATE:
            closes = daily_closes(sym)
            reg = classify_regime(closes)['regime'] if closes else 'no_data'
            record['regime_label'] = reg
            record['regime_ok'] = (reg == 'reverting')
            if reg != 'reverting':
                print(f"  [SCAV SKIP] {base:<6} regime gate ({reg})")
                record['block_reason'] = 'regime_gate_blocked'
                log_decision(record)
                continue

        # --- OBI GATE: don't fire into a toxic, one-sided order book ---
        if USE_OBI_GATE:
            gate = obi_gate(sym)
            record['obi_value'] = gate.get('obi')
            record['obi_ok'] = gate.get('allow', False)
            if not gate.get('allow', False):
                print(f"  [SCAV SKIP] {base:<6} OBI gate blocked "
                      f"(obi={gate.get('obi')}, {gate.get('dominant_side')})")
                record['block_reason'] = 'obi_gate_blocked'
                log_decision(record)
                continue

        # --- FLOW GATE: order-flow veto -- skip toxic delta / dead book.
        # Coverage reality: footprint_worker.py covers 20 symbols, Scav scans
        # ~76 -- most candidates that reach here will read BLIND_NO_DATA. That's
        # honest reporting of real coverage, not a broken gate. ---
        if USE_FLOW_GATE:
            flow = check_flow(f"{base}-USD")
            record['flow_verdict'] = flow.verdict
            record['flow_reason'] = flow.reason
            if flow.veto:
                print(f"  [SCAV SKIP] {base:<6} flow veto ({flow.reason})")
                record['block_reason'] = 'flow_veto'
                log_decision(record)
                continue

        fired = open_scav(s, sym, pullback_trigger, price)
        record['fired'] = fired
        if not fired:
            record['block_reason'] = 'open_scav_returned_false'
        log_decision(record)

    log_cycle({
        'cycle_id': cycle_id, 'fleet': 'scav',
        'universe_size': len(universe),
        'symbols_evaluated': symbols_evaluated,
        'halt_reason': halt_reason,
        'halt_at_symbol': halt_at_symbol,
        'deployable_cash': deployable_cash(s),
        'bucket_usd': BUCKET_USD,
        'riders_open': len(s['riders']),
    })
    flush_decisions()

    save_state(s)


def run_engine():
    print("=" * 78)
    print("      PRV1311 — SCAVENGERS (isolated velocity engine)")
    print("=" * 78)
    print(f"Pool : ${STARTING_CAPITAL_USD:,.0f} split {SCAV_COUNT} ways")
    print(f"Team : up to {MAX_ACTIVE} active scavengers @ ${BUCKET_USD:,.2f} each")
    print(f"Reserve : {SIZING['reserve_slots']} slots (${RESERVE_USD:,.2f}) untouchable")
    gates = f"-{SCAV_PULLBACK_PCT:.0f}% pullback from {SCAV_LOOKBACK_DAYS}-day high | floor*{RIDER_FLOOR_BUFFER} buffer"
    if USE_REGIME_GATE:
        gates += " | regime"
    if USE_OBI_GATE:
        gates += " | OBI"
    print(f"Gates : {gates}")
    print(f"Universe : market top-50 by volume + watchlist (Coinbase-listed only)")
    print(f"Fee : {FEE_RATE*100:.0f}% of each winning flip's profit -> fees wallet")
    print(f"Status : LIVE (Ctrl+C to stop)\n")

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
            push_scavengers(s)

            active = [sym.split('/')[0] for sym in s['riders']]
            print("-" * 78)
            print(f"  Active scavengers ({len(active)}/{MAX_ACTIVE}): {', '.join(active) if active else 'none'}")
            print(f"[{time.strftime('%H:%M:%S')}] Total ${total:,.2f} | Cash ${s['USD_balance']:,.2f} "
                  f"| Reserve ${RESERVE_USD:,.2f} | Treasury ${s['treasury']:,.2f} | Fees ${s['fees_wallet']:,.2f}")
            print("-" * 78)

            time.sleep(15 * 60)

        except KeyboardInterrupt:
            print("\n[Scavengers] stopped safely.")
            break
        except Exception as e:
            print(f"\n[Scavengers Error] {e}")
            time.sleep(60)


if __name__ == "__main__":
    run_engine()