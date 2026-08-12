"""
================================================================================
PROJECT: Prv1311 — Rider Team (decoupled Rider engine)
FILE: rider_team.py
================================================================================
A TEAM of independent Riders, each deploying 1 bucket into 1 asset:
  - enter on a -10% pullback from the 7-day high (a real dip, not noise)
  - only above the floor buffer (freefall is CORE's job)
  - exit at +5%, bank profit, free the slot
Selectivity + safety:
  - up to (RIDER_COUNT - RESERVE_RIDERS) active; reserve buckets untouchable.
  - stablecoins / denylist / unseasoned coins (maturity gate) excluded.
  - OBI GATE: skips entries into toxic, one-sided order books.
  - REGIME GATE: fires on mean-reverting OR uptrending assets — a pullback in an
    uptrend is a prime flip setup. Skips only DOWNtrending coins (falling-knife
    guard). This is the fix for the all-day miss: the old gate demanded
    'reverting' and threw away every uptrend as if it were a crash.
  - FLOW GATE: order-flow veto — skips entries when recent tick delta is toxic
    or the book is dead. Veto-only, fails safe to cash.
  All gates BLOCK entries only — never sell, faithful to never-cut.

UNIVERSE: scans the whole liquid market (top N by 24h volume) PLUS the client's
WATCHLIST tokens forced in — but only ones Coinbase actually lists (checks the
live market map, tries /USD then /USDC, skips misspelled/missing silently).

FEE: 20% of each winning flip's profit -> fees_wallet; 80% -> treasury.
Runs decoupled from CORE, on its own ledger.
================================================================================
"""

import json
import time
import os
import sys
import uuid
import logging
from logging.handlers import RotatingFileHandler
from config import (
    QUOTE, MIN_24H_USD_VOLUME, STABLE_EXCLUDES, DENYLIST, WATCHLIST,
    RIDER_PULLBACK_PCT, RIDER_TARGET_PCT, RIDER_FLOOR_BUFFER,
    RIDER_POOL_USD, RIDER_COUNT, RIDER_CEILING, RIDER_FLOOR, RESERVE_RIDERS,
    RIDER_LEDGER_FILE, TAKER_FEE_PCT, MAKER_FEE_PCT, RIDER_EXEC_STYLE,
    RIDER_MAX_DROP_PCT,
)
from screener import (exchange, fetch_live_price, calculate_90_day_floor,
                      rolling_7_day_high, get_daily_ohlcv, clear_daily_cache)
from sync_supabase import push_ledger
from supabase_client import record_health
from orderbook_imbalance import obi_gate
from regime import classify_regime
from footprint_gate import check_flow
from rider_decision_log import (log_decision, log_cycle, flush_decisions,
                                get_candle_count, maturity_ok)
from anomaly_gate import check_anomaly

FEE_RATE = 0.20
EXCHANGE_FEE_PCT = TAKER_FEE_PCT if RIDER_EXEC_STYLE == 'taker' else MAKER_FEE_PCT
USE_OBI_GATE = True
USE_REGIME_GATE = True     # fire on reverting OR trending_up; block trending_down
USE_FLOW_GATE = True       # order-flow veto (footprint_gate)

# regimes the Rider is allowed to enter on. reverting = ranging (snap-back flip),
# trending_up = pullback-in-an-uptrend (prime flip). trending_down/flat blocked.
RIDER_OK_REGIMES = ('reverting', 'trending_up')

# Consecutive live-universe-fetch failures in a row (resets on any clean fetch).
# Reported on the rider_health row every time the stub kicks in -- unlike the
# footprint flow gate, this fires on EVERY occurrence, not after a streak: a
# 50+-symbol universe collapsing to 6 hardcoded coins is rare and severe
# enough to want to know about the instant it happens.
_consecutive_universe_fallback = 0

# Provenance for the decision log only -- which symbols in the last-built
# universe came from the real market scan (with a real, already-computed 24h
# volume) vs. were force-added from WATCHLIST without ever being liquidity-
# vetted. Read by run_cycle(); written only inside get_universe_markets().
_last_market_scan_set = set()
_last_market_scan_volumes = {}

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_FILE = os.path.join(LOG_DIR, "rider_team.log")


def _install_rotating_log(log_name='rider_team'):
    """Tee stdout/stderr into a rotating file, in addition to the console --
    same pattern as footprint_worker.py. Under Task Scheduler there's no
    console to watch; interactive runs keep seeing live output as before.
    log_name is parameterized so rider_flare.py (which calls run_engine()
    directly, not a copy of it) gets its own logs/rider_flare.log instead of
    two Windows services fighting over the same rider_team.log handle --
    RotatingFileHandler's rollover renames the file, which fails on Windows
    if a second process still has it open."""
    log_file = os.path.join(LOG_DIR, f"{log_name}.log")
    os.makedirs(LOG_DIR, exist_ok=True)
    logger = logging.getLogger(log_name)
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024,
                                  backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)

    class _Tee:
        def __init__(self, original, level):
            self.original = original
            self.level = level
            self._buf = ""

        def write(self, msg):
            if self.original:
                try:
                    self.original.write(msg)
                except Exception:
                    pass
            self._buf += msg
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                if line.strip():
                    logger.log(self.level, line)

        def flush(self):
            if self.original:
                try:
                    self.original.flush()
                except Exception:
                    pass

    sys.stdout = _Tee(sys.stdout, logging.INFO)
    sys.stderr = _Tee(sys.stderr, logging.ERROR)


def compute_sizing(pool_usd=RIDER_POOL_USD, rider_count=RIDER_COUNT,
                    rider_ceiling=RIDER_CEILING, rider_floor=RIDER_FLOOR,
                    reserve_riders=RESERVE_RIDERS):
    """Additive: called with no args, reproduces the exact module-global
    sizing this file has always used (defaults are the same config.py
    globals). A caller building a per-user sizing (e.g. one Solo-Rider
    with their own deposit, one active slot, no reserve) passes explicit
    values instead -- see USER SCOPE in the module docstring."""
    if rider_count < 1:
        raise ValueError(f"rider_count must be >= 1 (got {rider_count}).")
    raw = pool_usd / rider_count
    if raw < rider_floor:
        max_slots = int(pool_usd // rider_floor)
        raise ValueError(
            f"Pool ${pool_usd:,.0f} / {rider_count} riders = "
            f"${raw:,.2f} per rider, below the ${rider_floor:,.0f} floor. "
            f"Max riders at this pool: {max_slots}. Lower rider_count or raise pool_usd."
        )
    bucket = min(raw, rider_ceiling)
    reserve_slots = max(0, reserve_riders)
    active_slots = rider_count - reserve_slots
    if active_slots < 1:
        raise ValueError(
            f"reserve_riders ({reserve_riders}) leaves no active slots out of "
            f"rider_count ({rider_count}). Lower reserve_riders."
        )
    reserve_usd = bucket * reserve_slots
    return {
        'bucket': bucket, 'active_slots': active_slots,
        'reserve_slots': reserve_slots, 'reserve_usd': reserve_usd,
        'starting_capital': pool_usd,
    }


SIZING = compute_sizing()
BUCKET_USD = SIZING['bucket']
MAX_ACTIVE_RIDERS = SIZING['active_slots']
RESERVE_USD = SIZING['reserve_usd']
STARTING_CAPITAL_USD = SIZING['starting_capital']


def is_excluded(base):
    return base in STABLE_EXCLUDES or base in DENYLIST


def daily_closes(symbol, limit=120, ohlcv_fn=None):
    """Daily closes for the regime gate + anomaly gate. None on failure.
    Routed through screener's shared per-cycle cache (Task E) -- same values,
    fewer round trips. ohlcv_fn: optional override, same shape as
    get_daily_ohlcv(symbol, limit) -- see calculate_90_day_floor's docstring
    in screener.py for why this exists."""
    try:
        fetch = ohlcv_fn or get_daily_ohlcv
        ohlcv = fetch(symbol, limit)
        return [row[4] for row in ohlcv] if ohlcv else None
    except Exception:
        return None


def get_universe_markets(limit=50):
    """Top N liquid Coinbase /USD pairs by 24h volume, PLUS the client's
    WATCHLIST tokens forced in — but only ones Coinbase actually lists. Checks
    the live market map, prefers /USD then /USDC, skips missing tickers silently.
    The entry gates still decide whether a watchlist token actually trades."""
    global _consecutive_universe_fallback, _last_market_scan_set, _last_market_scan_volumes
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
        _consecutive_universe_fallback = 0
    except Exception as e:
        _consecutive_universe_fallback += 1
        print("=" * 70)
        print(f"[UNIVERSE FALLBACK] live market fetch failed -- {e} -- "
              f"degrading to 6-symbol hardcoded stub "
              f"(consecutive: {_consecutive_universe_fallback})")
        print("=" * 70)
        record_health("universe_fetch", "DEGRADED",
                      {"error": str(e)}, _consecutive_universe_fallback)
        universe = ['BTC/USD', 'ETH/USD', 'SOL/USD', 'XRP/USD', 'ADA/USD', 'XLM/USD']
        # Fallback-stub symbols were never actually liquidity-screened -- no
        # real volume figure exists for them. source still reads 'market_scan'
        # (they replaced the scan, weren't watchlist-forced), but volume_24h/
        # liquidity_ok correctly stay null: the screen didn't run, so there's
        # nothing honest to report there.
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


def fresh_state(sizing=SIZING):
    return {
        'system': 'Prv1311-rider-team',
        'USD_balance': sizing['starting_capital'],
        'treasury': 0.0, 'fees_wallet': 0.0,
        'riders': {}, 'trade_history': [],
    }


def load_state(ledger_file=None, sizing=SIZING):
    lf = ledger_file or RIDER_LEDGER_FILE
    if not os.path.exists('data'):
        os.makedirs('data')
    if os.path.exists(lf):
        s = json.load(open(lf))
        s.setdefault('fees_wallet', 0.0)
        return s
    s = fresh_state(sizing)
    save_state(s, lf)
    return s


def save_state(s, ledger_file=None):
    lf = ledger_file or RIDER_LEDGER_FILE
    with open(lf, 'w') as f:
        json.dump(s, f, indent=4)


def log(s, action, sym, price, usd, units, profit=None, fee=None,
        exchange_fee=None, fee_model=None):
    row = {'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
           'engine': 'RIDER', 'action': action, 'asset': sym.split('/')[0],
           'price': price, 'amount_usd': usd, 'units': units}
    if profit is not None:
        row['profit'] = profit
    if fee is not None:
        row['fee'] = fee
    if exchange_fee is not None:
        row['exchange_fee'] = exchange_fee
    if fee_model is not None:
        row['fee_model'] = fee_model
    s['trade_history'].append(row)


def deployable_cash(s, sizing=SIZING):
    return s['USD_balance'] - sizing['reserve_usd']


def open_rider(s, sym, price, sizing=SIZING):
    if deployable_cash(s, sizing) < sizing['bucket']:
        return False
    usd = sizing['bucket']
    # Exchange fee comes out of what you receive, not extra cash on top -- cash
    # spent stays exactly the bucket size (unchanged check above), fewer units acquired.
    entry_exchange_fee = usd * (EXCHANGE_FEE_PCT / 100.0)
    units = (usd - entry_exchange_fee) / price
    s['USD_balance'] -= usd
    s['riders'][sym] = {'asset': sym, 'entry_price': price,
                        'units': units, 'usd_in': usd,
                        'entry_exchange_fee': entry_exchange_fee}
    log(s, 'BUY', sym, price, usd, units,
        exchange_fee=entry_exchange_fee, fee_model='v2')
    print(f"  [RIDER BUY] {sym.split('/')[0]:<6} @ ${price:.4f}  target ${price*(1+RIDER_TARGET_PCT/100):.4f}  "
          f"({len(s['riders'])}/{sizing['active_slots']} active)")
    return True


def close_rider(s, sym, price):
    r = s['riders'][sym]
    gross_proceeds = r['units'] * price
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
    log(s, 'SELL', sym, price, proceeds, r['units'], profit=net_profit, fee=fee,
        exchange_fee=exit_exchange_fee, fee_model='v2')
    print(f"  [RIDER SELL] {sym.split('/')[0]:<6} @ ${price:.4f}  +{RIDER_TARGET_PCT:.0f}%  "
          f"profit ${net_profit:.2f} (fee ${fee:.2f}, exchange fee ${exit_exchange_fee:.2f})")
    del s['riders'][sym]


def run_cycle(s, universe, price_fn=fetch_live_price, fleet='rider', ledger_file=None,
              sizing=SIZING, user_id=None, ohlcv_fn=None, clear_cache_fn=clear_daily_cache):
    # ohlcv_fn/clear_cache_fn: optional override for the daily-candle source
    # feeding the anomaly gate, the floor, the 7-day high, and the regime
    # gate below -- same shape as get_daily_ohlcv/clear_daily_cache. Every
    # existing caller passes neither and gets byte-identical Coinbase
    # behavior; rider_flare.py injects flare.coingecko_adapter's versions
    # instead. Nothing about the comparisons below (pullback_ok, floor
    # buffer, regime membership, anomaly veto) changes either way -- only
    # where their inputs come from.
    clear_cache_fn()  # explicit, not TTL -- a stale daily series carried
                      # across cycles is worse than an extra call (Task E)
    cycle_id = str(uuid.uuid4())
    symbols_evaluated = 0
    halt_reason = None
    halt_at_symbol = None

    # ---- 1. exits first ----
    for sym in list(s['riders'].keys()):
        r = s['riders'][sym]
        price = price_fn(sym)
        if price is None:
            continue
        target = r['entry_price'] * (1 + RIDER_TARGET_PCT / 100.0)
        if price >= target:
            close_rider(s, sym, price)

    # ---- 2. entries ----
    for sym in universe:
        base = sym.split('/')[0]

        if len(s['riders']) >= sizing['active_slots']:
            halt_reason = 'team_full'
            halt_at_symbol = sym
            break
        if deployable_cash(s, sizing) < sizing['bucket']:
            halt_reason = 'cash_floor'
            halt_at_symbol = sym
            break

        symbols_evaluated += 1
        vol = _last_market_scan_volumes.get(sym)
        record = {
            'cycle_id': cycle_id, 'symbol': sym, 'fleet': fleet,
            'source': 'market_scan' if sym in _last_market_scan_set else 'watchlist',
            'price': None, 'rolling_7d_high': None, 'pullback_pct': None,
            'floor_value': None, 'floor_buffer_ok': None,
            'candle_count': None, 'maturity_ok': None,
            'volume_24h': vol, 'liquidity_ok': (vol >= MIN_24H_USD_VOLUME) if vol is not None else None,
            'regime_label': None, 'regime_ok': None,
            'obi_value': None, 'obi_ok': None,
            'flow_verdict': None, 'flow_reason': None,
            'fired': False, 'block_reason': None,
        }
        # user_id is a USER SCOPE axis, orthogonal to fleet: fleet says which
        # strategy, user_id says whose. Only added to the outgoing row when a
        # caller actually supplies one -- existing callers (fleet='rider',
        # 'scav', 'rider_flare') never pass user_id, so their rows keep the
        # exact same shape as before this change. Requires a user_id column
        # on rider_decisions before any caller relies on it; not yet added
        # here (schema change, not a code change) -- see the report.
        if user_id is not None:
            record['user_id'] = user_id

        if is_excluded(base):
            record['block_reason'] = 'excluded'
            log_decision(record)
            continue
        if sym in s['riders']:
            record['block_reason'] = 'already_held'
            log_decision(record)
            continue

        price = price_fn(sym)
        record['price'] = price
        if price is None:
            record['block_reason'] = 'price_fetch_failed'
            log_decision(record)
            continue

        # --- ANOMALY GATE: quarantine a name coming off a blow-off pump, before
        # any further evaluation -- a dip off an artificially-pumped high isn't a
        # real dip. Must run BEFORE the pullback/catch-band check by design. ---
        anomaly = check_anomaly(daily_closes(sym, limit=60, ohlcv_fn=ohlcv_fn))
        if anomaly.veto:
            print(f"  [RIDER SKIP] {base:<6} anomaly veto (r7={anomaly.r7:.3f}, z={anomaly.z})")
            record['block_reason'] = 'anomaly_veto'
            log_decision(record)
            continue

        floor = calculate_90_day_floor(sym, ohlcv_fn=ohlcv_fn)
        record['floor_value'] = floor
        cc = get_candle_count(sym)
        record['candle_count'] = cc
        record['maturity_ok'] = maturity_ok(cc)
        if floor is None:
            record['block_reason'] = 'floor_fetch_failed'
            log_decision(record)
            continue

        high7 = rolling_7_day_high(sym, ohlcv_fn=ohlcv_fn)
        record['rolling_7d_high'] = high7
        if high7 is None:
            record['block_reason'] = 'high7_fetch_failed'
            log_decision(record)
            continue

        pullback_trigger = high7 * (1 - RIDER_PULLBACK_PCT / 100.0)
        # Catch-band floor: below this is a real breakdown, not a dip -- that's
        # CORE's mechanical ladder's job, not a rejected opportunity for Rider.
        catch_floor = high7 * (1 - RIDER_MAX_DROP_PCT / 100.0)
        above_floor_buffer = price >= floor * RIDER_FLOOR_BUFFER
        pullback_ok = price <= pullback_trigger
        in_catch_band = price >= catch_floor
        record['pullback_pct'] = (high7 - price) / high7 * 100.0
        record['floor_buffer_ok'] = above_floor_buffer
        if not (pullback_ok and in_catch_band and above_floor_buffer):
            if not pullback_ok:
                record['block_reason'] = 'pullback_insufficient'
            elif not in_catch_band:
                record['block_reason'] = 'below_catch_band'
            else:
                record['block_reason'] = 'floor_buffer_fail'
            log_decision(record)
            continue

        # --- REGIME GATE: enter on reverting OR trending_up; block downtrends ---
        # A -10% pullback inside an UPtrend is a prime flip setup, not a crash.
        # The floor buffer above is the falling-knife guard; trending_down is
        # still skipped here as a second line of defense.
        if USE_REGIME_GATE:
            closes = daily_closes(sym, ohlcv_fn=ohlcv_fn)
            reg = classify_regime(closes)['regime'] if closes else 'no_data'
            record['regime_label'] = reg
            record['regime_ok'] = reg in RIDER_OK_REGIMES
            if reg not in RIDER_OK_REGIMES:
                print(f"  [RIDER SKIP] {base:<6} regime gate ({reg})")
                record['block_reason'] = 'regime_gate_blocked'
                log_decision(record)
                continue

        # --- OBI GATE: don't fire into a toxic, one-sided order book ---
        if USE_OBI_GATE:
            gate = obi_gate(sym)
            record['obi_value'] = gate.get('obi')
            record['obi_ok'] = gate.get('allow', False)
            if not gate.get('allow', False):
                print(f"  [RIDER SKIP] {base:<6} OBI gate blocked "
                      f"(obi={gate.get('obi')}, {gate.get('dominant_side')})")
                record['block_reason'] = 'obi_gate_blocked'
                log_decision(record)
                continue

        # --- FLOW GATE: order-flow veto — skip toxic delta / dead book ---
        if USE_FLOW_GATE:
            flow = check_flow(f"{base}-USD")
            record['flow_verdict'] = flow.verdict
            record['flow_reason'] = flow.reason
            if flow.veto:
                print(f"  [RIDER SKIP] {base:<6} flow veto ({flow.reason})")
                record['block_reason'] = 'flow_veto'
                log_decision(record)
                continue

        fired = open_rider(s, sym, price, sizing)
        record['fired'] = fired
        if not fired:
            record['block_reason'] = 'open_rider_returned_false'
        log_decision(record)

    cycle_row = {
        'cycle_id': cycle_id,
        'fleet': fleet,
        'universe_size': len(universe),
        'symbols_evaluated': symbols_evaluated,
        'halt_reason': halt_reason,
        'halt_at_symbol': halt_at_symbol,
        'deployable_cash': deployable_cash(s, sizing),
        'bucket_usd': sizing['bucket'],
        'riders_open': len(s['riders']),
    }
    if user_id is not None:
        cycle_row['user_id'] = user_id
    log_cycle(cycle_row)
    flush_decisions()

    save_state(s, ledger_file)


def run_engine(price_fn=fetch_live_price, fleet='rider', ledger_file=None,
               health_component='rider_engine', state_table='rider_state',
               universe_fn=None, log_name='rider_team', sizing=SIZING, user_id=None,
               state_row_key=None, state_key_column=None,
               ohlcv_fn=None, clear_cache_fn=clear_daily_cache):
    _install_rotating_log(log_name)

    # rider_health has no user_id column -- component is already a free-text
    # label (rider_engine / divergence_recorder / rider_flare_engine), so a
    # per-user suffix reuses that existing pattern instead of requiring a
    # schema change. Unchanged when user_id is None (every current caller).
    effective_health_component = (
        f"{health_component}_{user_id}" if user_id is not None else health_component
    )

    # push_ledger's upsert key: explicit state_row_key/state_key_column win
    # if given; otherwise default to user-scoped keying when user_id is
    # present, or push_ledger's own id=1 default when it isn't (every
    # existing caller) -- byte-identical to before this parameter existed.
    effective_row_key = state_row_key if state_row_key is not None else (
        user_id if user_id is not None else 1
    )
    effective_key_column = state_key_column if state_key_column is not None else (
        'user_id' if user_id is not None else 'id'
    )

    env_loaded = bool(os.getenv("SUPABASE_URL")) and bool(os.getenv("SUPABASE_KEY"))
    rider_count = sizing['active_slots'] + sizing['reserve_slots']
    record_health(effective_health_component, "STARTUP", {
        "env_loaded": env_loaded,
        "pool_usd": sizing['starting_capital'],
        "rider_count": rider_count,
        "max_active_riders": sizing['active_slots'],
        "bucket_usd": sizing['bucket'],
        "user_id": user_id,
        "gates": {"regime": USE_REGIME_GATE, "obi": USE_OBI_GATE, "flow": USE_FLOW_GATE},
    }, 0)

    print("=" * 78)
    print("      PRV1311 — RIDER TEAM (decoupled)")
    print("=" * 78)
    print(f"Pool : ${sizing['starting_capital']:,.0f} split {rider_count} ways")
    print(f"Team : up to {sizing['active_slots']} active riders @ ${sizing['bucket']:,.2f} each")
    print(f"Reserve : {sizing['reserve_slots']} slots (${sizing['reserve_usd']:,.2f}) untouchable -- never fully deployed")
    gates = "maturity + liquidity"
    if USE_REGIME_GATE:
        gates += " | regime(reverting/up)"
    if USE_OBI_GATE:
        gates += " | OBI"
    if USE_FLOW_GATE:
        gates += " | flow"
    print(f"Gates : -{RIDER_PULLBACK_PCT:.0f}% pullback | floor*{RIDER_FLOOR_BUFFER} buffer | {gates}")
    if universe_fn is None:
        print(f"Universe : market top-50 by volume + watchlist (Coinbase-listed only)")
    else:
        print(f"Universe : fixed, caller-supplied")
    print(f"Fee : {FEE_RATE*100:.0f}% of each winning flip's profit -> fees wallet")
    print(f"Status : LIVE (Ctrl+C to stop)\n")

    s = load_state(ledger_file, sizing)
    while True:
        try:
            print(f"\n[{time.strftime('%H:%M:%S')}] Fetching universe...")
            universe = universe_fn() if universe_fn else get_universe_markets(limit=50)
            print(f"  {len(universe)} assets (market + watchlist)")
            run_cycle(s, universe, price_fn=price_fn, fleet=fleet, ledger_file=ledger_file, sizing=sizing,
                     user_id=user_id, ohlcv_fn=ohlcv_fn, clear_cache_fn=clear_cache_fn)

            held_val = 0.0
            for sym, r in s['riders'].items():
                pr = price_fn(sym)
                if pr:
                    held_val += r['units'] * pr
                    r['current_price'] = pr
                    r['current_value'] = r['units'] * pr
            total = s['USD_balance'] + held_val + s['treasury']
            save_state(s, ledger_file)
            push_ledger(s, table=state_table, row_key=effective_row_key, key_column=effective_key_column)

            active = [sym.split('/')[0] for sym in s['riders']]
            print("-" * 78)
            print(f"  Active riders ({len(active)}/{sizing['active_slots']}): {', '.join(active) if active else 'none'}")
            print(f"[{time.strftime('%H:%M:%S')}] Total ${total:,.2f} | Cash ${s['USD_balance']:,.2f} "
                  f"| Reserve ${sizing['reserve_usd']:,.2f} protected | Treasury ${s['treasury']:,.2f} "
                  f"| Fees ${s['fees_wallet']:,.2f}")
            print("-" * 78)

            time.sleep(15 * 60)

        except KeyboardInterrupt:
            print("\n[Rider Team] stopped safely.")
            break
        except Exception as e:
            print(f"\n[Rider Team Error] {e}")
            time.sleep(60)


if __name__ == "__main__":
    run_engine()