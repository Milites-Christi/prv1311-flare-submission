"""
================================================================================
solo_rider.py — Solo-Rider: per-user, single-asset, single-position thin
wrapper over rider_team's real gate stack.
================================================================================
REWRITE, not a patch. The previous version of this file (and its now-deleted
sibling solo_rider_flare.py, which this file replaces and was renamed from)
booked an instant fictional round trip -- it checked an entry gate and
immediately simulated a take-profit hit, with no exit logic, no position
state, no waiting for price. It also predated the fee model, the catch-band,
the anomaly gate, the decision log, the shared candle cache, and the flow
gate. None of that logic survived into this version.

THIS FILE CONTAINS ZERO GATE LOGIC OF ITS OWN. Every gate -- exclusion,
already-held, capacity/cash-floor, price/maturity validation, the volatility
anomaly quarantine, the entry band (pullback + catch-band + floor buffer),
regime classification, order-book imbalance, order-flow confirmation -- is
rider_team.run_cycle()'s, imported and called, never copied. If a second
copy of any of that logic ever shows up in this file, that's a bug.

WHAT THE USER ACTUALLY GETS TO SET: asset choice and capital amount, capital
bounded to [MIN_CAPITAL_USD, MAX_CAPITAL_USD]. That is all. Take-profit and
every other threshold are system constants, unchanged from rider_team.py,
for the same reason they're fixed there -- scavengers already proved a
user-chosen target that never fills reads as "the system is broken," not as
user empowerment.

PRICE SOURCE: Coinbase, via screener.py's shared, rate-limited, cached
exchange handle -- the same one rider_team.py itself uses. Not Flare/FTSO.
The oracle-priced angle from the file this was built from was a hackathon
side-branch (see flare/rider_flare.py); it does not carry into this
production file. This is also why the file is named solo_rider.py, not
solo_rider_flare.py -- the old name stopped describing what it does the
moment the Flare pricing path was dropped.

CONCURRENCY: one process per user, not threads or a shared loop over many
users' positions -- matches how every other engine in this repo deploys,
and avoids rider_team.py's non-thread-local module globals
(_last_market_scan_set etc.) entirely, by construction rather than by
defense. This file provides the correctly-shaped per-user execution unit;
it does NOT include a launcher that spawns one process per active user --
that orchestration is a go-live decision, not a code problem, and is called
out explicitly in docs/GO_LIVE_AUTHORITY.md.

INTAKE: poll_orders() is the one piece kept deliberately from the file this
was built from -- a pending-request queue (Base44's SoloOrder entity) that
seeds a config row and marks it accepted/rejected is a reusable shape,
independent of whatever execution logic it feeds. Reshaped here to seed
only the two real dials (asset, capital) rather than the old
team/rider_id/tp_pct/mode fields, none of which survived this rewrite.
poll_orders() is intake only -- it never calls into rider_team, so it's
safe to run from a single shared process regardless of how many users
exist; run_for_user() is the part that needs process-per-user isolation.

Run one instance per user:
  cd C:\\Users\\Autry\\accum-flip\\Prv1311
  .\\venv\\Scripts\\Activate
  set SOLO_RIDER_USER_ID=<user id>
  python solo_rider.py
================================================================================
"""

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

import rider_team
from screener import exchange
from supabase_client import get_client, record_health

BASE44_APP_ID = "6a6ff41906f77798ee5f2c15"
BASE44_API_KEY = os.getenv("BASE44_API_KEY")
if not BASE44_API_KEY:
    raise RuntimeError(
        "BASE44_API_KEY is not set. Add it to .env in Prv1311/ (see .env.example)."
    )
BASE44_BASE = "https://app.base44.com/api/apps"

B44_HEADERS = {
    "api_key": BASE44_API_KEY,
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0 Safari/537.36",
    "Accept": "application/json",
}

QUOTES = {"USD", "USDC", "USDT"}

FLEET = 'solo_rider'
CONFIG_TABLE = "solo_rider_config"   # per-user dials: user_id, asset, capital, status
STATE_TABLE = "solo_rider_state"     # ledger blob per user, keyed on user_id (sync_supabase.push_ledger)
LEDGER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Service tick interval for run_service() -- intake, then one paper cycle
# per active user, every CYCLE_SEC. Independent of rider_team's own 15-
# minute sleep (used only by run_engine()'s infinite loop, which
# run_service() below deliberately does not call).
CYCLE_SEC = 60

# ============================================================================
# PAPER TRADING ONLY. No real order-execution code exists in this codebase.
# open_rider()/close_rider(), called inside rider_team.run_cycle() below,
# are pure in-memory arithmetic against a JSON ledger -- there is no
# exchange.create_order() call anywhere in this file or in rider_team.py.
# Every log line and every health-row status this file writes says PAPER
# explicitly, on purpose -- this is not a detail to infer from context.
# ============================================================================

# Capital dial bounds -- the only two user-settable inputs are asset and
# capital; this is the range capital is allowed to take. Flagged for the
# record: "self-adjusting on %" was mentioned as the eventual direction but
# not specified precisely enough to build -- this is the flat min/max
# version only. Percent-of-something self-adjustment is a separate,
# not-yet-scoped feature, not implemented here.
MIN_CAPITAL_USD = 25.0
MAX_CAPITAL_USD = 1000.0


def normalize_symbol(raw):
    """Accept 'SOL', 'SOL-USD', 'SOL/USD' -> return 'SOL/USD'. Return None
    if the base is empty or itself a quote currency (junk order). Pure
    string logic, no gate content -- kept from the previous version as-is."""
    if not raw:
        return None
    s = str(raw).upper().replace("-", "/").strip()
    if "/" in s:
        base, _, quote = s.partition("/")
        quote = quote or "USD"
    else:
        base, quote = s, "USD"
    if not base or base in QUOTES:
        return None
    if quote not in QUOTES:
        quote = "USD"
    return f"{base}/{quote}"


def _asset_is_listed(symbol):
    """Routed through screener.exchange (the shared, locked, cached handle
    rider_team.py itself uses) rather than a standalone ccxt client -- the
    rate-limit lock and candle cache exist for a reason, and intake
    shouldn't bypass them just because it's a lighter-weight check."""
    try:
        markets = exchange.markets or exchange.load_markets()
    except Exception:
        return False
    return symbol in markets


def _ledger_file_for(user_id):
    os.makedirs(LEDGER_DIR, exist_ok=True)
    return os.path.join(LEDGER_DIR, f"solo_rider_{user_id}_ledger.json")


# ---------------------------------------------------------------------------
# CONFIG  (Supabase is the single source of truth for what each user wants)
# ---------------------------------------------------------------------------
def get_config(user_id):
    r = (get_client().table(CONFIG_TABLE)
         .select("*").eq("user_id", user_id).execute())
    return r.data[0] if r.data else None


def upsert_config(user_id, asset, capital, status):
    """Fixed 'updated_at': 'now()' bug from the previous version -- that
    sent the literal string "now()" to what the DB expects as a real
    timestamp. A timestamptz column doesn't parse that as SQL's now();
    it's just an unparseable string. Sending an actual Python datetime is
    the fix here, not a column-type change."""
    get_client().table(CONFIG_TABLE).upsert({
        "user_id": user_id,
        "asset": asset,
        "capital": float(capital),
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }, on_conflict="user_id").execute()


# ---------------------------------------------------------------------------
# INTAKE  (poll pending Base44 SoloOrders, seed config, mark accepted/rejected)
# ---------------------------------------------------------------------------
def poll_orders():
    """Pull pending SoloOrders, seed them as per-user config, mark accepted
    or rejected. Intake only -- never calls into rider_team, so this is
    safe to run from one shared process regardless of how many users exist,
    unlike run_for_user() below."""
    url = f"{BASE44_BASE}/{BASE44_APP_ID}/entities/SoloOrder"
    try:
        resp = requests.get(url, headers=B44_HEADERS,
                            params={"status": "pending"}, timeout=15)
        resp.raise_for_status()
        orders = resp.json() or []
    except Exception as e:
        print(f"[poll] SoloOrder fetch failed: {e}")
        return

    for o in orders:
        user_id = o.get("created_by_id")
        if not user_id:
            record_health("solo_rider_intake", "MISSING_USER_ID", {
                "order_id": o.get("id"),
                "asset": o.get("asset"),
                "capital": o.get("capital"),
            }, 0)
            print(f"[intake][PAPER] skipped order {o.get('id')} -- "
                  f"no created_by_id on the SoloOrder record, refusing to fall "
                  f"back to a placeholder user_id.")
            continue
        asset = normalize_symbol(o.get("asset"))
        capital = o.get("capital", 0)
        try:
            capital = float(capital)
        except (TypeError, ValueError):
            capital = 0

        valid = (
            asset and _asset_is_listed(asset)
            and MIN_CAPITAL_USD <= capital <= MAX_CAPITAL_USD
        )
        if not valid:
            try:
                requests.put(f"{url}/{o['id']}", headers=B44_HEADERS,
                             json={"status": "rejected"}, timeout=15)
            except Exception:
                pass
            print(f"[intake][PAPER] rejected order {o.get('id')} asset={o.get('asset')!r} "
                  f"capital={o.get('capital')!r} "
                  f"(must be a listed asset and ${MIN_CAPITAL_USD:.0f}-${MAX_CAPITAL_USD:.0f})")
            continue

        upsert_config(user_id, asset, capital, "active")
        try:
            requests.put(f"{url}/{o['id']}", headers=B44_HEADERS,
                         json={"status": "accepted"}, timeout=15)
        except Exception as e:
            print(f"[poll] could not mark order {o.get('id')} accepted: {e}")
        print(f"[intake][PAPER] seeded solo_rider user={user_id} asset={asset} capital={capital}")


# ---------------------------------------------------------------------------
# EXECUTION  (one user, one process, one position -- delegates entirely to
# rider_team's real gate stack; nothing here decides whether a trade fires)
# ---------------------------------------------------------------------------
def run_for_user(user_id):
    cfg = get_config(user_id)
    if not cfg or cfg.get("status") != "active":
        print(f"[solo_rider] user {user_id} has no active config -- nothing to run.")
        return

    asset = cfg["asset"]
    capital = float(cfg["capital"])

    # Defense in depth: re-check the bound here too, not just at intake --
    # a config row could in principle be edited directly in Supabase,
    # bypassing poll_orders() entirely. Refuse to run rather than trade
    # with an out-of-bounds capital.
    if not (MIN_CAPITAL_USD <= capital <= MAX_CAPITAL_USD):
        print(f"[solo_rider][PAPER] user {user_id} capital ${capital:,.2f} outside "
              f"[${MIN_CAPITAL_USD:.0f}, ${MAX_CAPITAL_USD:.0f}] -- refusing to run.")
        return

    # One user, one position: rider_count=1, that whole allocation is the
    # bucket, no reserve slot held back. RIDER_TARGET_PCT and every other
    # gate threshold are untouched system constants, exactly as in the
    # shared team engine -- this sizing call changes ONLY how much capital
    # this one user has, not what the engine is allowed to do with it.
    sizing = rider_team.compute_sizing(
        pool_usd=capital, rider_count=1, rider_ceiling=capital,
        rider_floor=0, reserve_riders=0,
    )

    print(f"[solo_rider][PAPER] starting user={user_id} asset={asset} capital=${capital:,.2f}")
    rider_team.run_engine(
        fleet=FLEET,
        ledger_file=_ledger_file_for(user_id),
        health_component='solo_rider_engine',
        state_table=STATE_TABLE,
        universe_fn=lambda: [asset],
        log_name=f'solo_rider_{user_id}',
        sizing=sizing,
        user_id=user_id,
    )


# ---------------------------------------------------------------------------
# SERVICE  (the Windows-service entrypoint -- one process, one shared cycle,
# looping sequentially over every currently-active user. A deliberate
# simplification for this phase, not the final production shape: true
# one-process-per-user (see run_for_user() above and docs/GO_LIVE_AUTHORITY.md)
# needs a launcher that spawns/manages one OS process per user, which does
# not exist yet. Sequential looping in one process is safe here specifically
# because Solo-Rider's universe_fn is always a fixed single asset -- it never
# calls rider_team.get_universe_markets(), so it never touches that
# function's non-thread-local globals (_last_market_scan_set etc.), the
# exact hazard "one process per user, not threads" was chosen to avoid.
# ---------------------------------------------------------------------------
def _run_cycle_for_user(cfg):
    """One paper cycle for one user. Calls rider_team's already-public
    load_state/run_cycle/save_state/push_ledger directly rather than
    run_engine() -- run_engine() owns an infinite loop for exactly one
    caller, which doesn't fit a service ticking many users in turn. Zero
    gate logic here: run_cycle() is rider_team's, unmodified, imported."""
    user_id = cfg["user_id"]
    asset = cfg["asset"]
    capital = float(cfg["capital"])

    if not (MIN_CAPITAL_USD <= capital <= MAX_CAPITAL_USD):
        print(f"[solo_rider][PAPER] user={user_id} capital ${capital:,.2f} "
              f"outside [${MIN_CAPITAL_USD:.0f}, ${MAX_CAPITAL_USD:.0f}] -- skipping this cycle.")
        return

    sizing = rider_team.compute_sizing(
        pool_usd=capital, rider_count=1, rider_ceiling=capital,
        rider_floor=0, reserve_riders=0,
    )
    ledger_file = _ledger_file_for(user_id)
    s = rider_team.load_state(ledger_file, sizing)

    rider_team.run_cycle(
        s, [asset], price_fn=rider_team.fetch_live_price, fleet=FLEET,
        ledger_file=ledger_file, sizing=sizing, user_id=user_id,
    )

    # mark-to-market held units -- mirrors run_engine()'s own loop body.
    # fetch_live_price is a READ (screener.py's live ticker), never a
    # trade -- this is paper mark-to-market, not an order.
    held_val = 0.0
    for sym, r in s["riders"].items():
        pr = rider_team.fetch_live_price(sym)
        if pr:
            held_val += r["units"] * pr
            r["current_price"] = pr
            r["current_value"] = r["units"] * pr
    total = s["USD_balance"] + held_val + s["treasury"]

    rider_team.save_state(s, ledger_file)
    rider_team.push_ledger(s, table=STATE_TABLE, row_key=user_id, key_column="user_id")

    # Per-cycle health row, explicitly labeled PAPER, with user_id both in
    # the component label AND in detail -- so the site can tell which
    # user's engine is alive and when it last reported in, and never
    # confuse a paper cycle for a real fill.
    record_health(f"solo_rider_engine_{user_id}", "CYCLE_PAPER", {
        "mode": "paper",
        "user_id": user_id,
        "asset": asset,
        "capital": capital,
        "riders_open": len(s["riders"]),
        "total_value": total,
    }, 0)

    print(f"[solo_rider][PAPER] user={user_id} asset={asset} cycle done, "
          f"riders_open={len(s['riders'])}, total=${total:,.2f}")


def run_service():
    """PAPER TRADING ONLY -- see the module-level banner above. Runs
    forever: poll intake, then one paper cycle for every currently-active
    user, in turn, then sleep. This is what gets registered as the
    PRV1311-SoloRider Windows service."""
    rider_team._install_rotating_log("solo_rider")

    env_loaded = bool(os.getenv("SUPABASE_URL")) and bool(os.getenv("SUPABASE_KEY"))
    record_health("solo_rider_service", "STARTUP_PAPER", {
        "mode": "paper",
        "env_loaded": env_loaded,
        "min_capital": MIN_CAPITAL_USD,
        "max_capital": MAX_CAPITAL_USD,
        "cycle_sec": CYCLE_SEC,
    }, 0)

    print("=" * 78)
    print("      PRV1311 -- SOLO RIDER SERVICE   ***PAPER TRADING ONLY***")
    print("=" * 78)
    print("No real order-execution code exists in this codebase. Every fill")
    print("below is simulated in-memory against a JSON ledger. There is no")
    print("exchange.create_order() call anywhere in this file or in rider_team.py.")
    print(f"Capital dial bounds: ${MIN_CAPITAL_USD:.0f}-${MAX_CAPITAL_USD:.0f}")
    print(f"Cycle: every {CYCLE_SEC}s (intake, then one pass over active users)")
    print("Status: LIVE -- PAPER (Ctrl+C to stop)\n")

    while True:
        try:
            poll_orders()
            configs = (get_client().table(CONFIG_TABLE)
                       .select("*").eq("status", "active").execute()).data or []
            print(f"[{time.strftime('%H:%M:%S')}] [PAPER] {len(configs)} active user(s)")
            for cfg in configs:
                try:
                    _run_cycle_for_user(cfg)
                except Exception as e:
                    print(f"[solo_rider][PAPER] user={cfg.get('user_id')} cycle error: {e}")
            time.sleep(CYCLE_SEC)
        except KeyboardInterrupt:
            print("\n[Solo Rider Service] stopped safely.")
            break
        except Exception as e:
            print(f"\n[Solo Rider Service Error] {e}")
            time.sleep(60)


if __name__ == "__main__":
    import sys
    target_user_id = os.getenv("SOLO_RIDER_USER_ID") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if target_user_id:
        # Dedicated single-user process -- the original one-process-per-user
        # shape, still available, unchanged.
        run_for_user(target_user_id)
    else:
        # No user_id given -- run as the shared multi-user service. This is
        # the default now because it's what PRV1311-SoloRider (the Windows
        # service) actually runs.
        run_service()
