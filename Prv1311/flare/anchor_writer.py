"""
================================================================================
flare/anchor_writer.py — unattended recurring writer for DivergenceAnchor on
Flare mainnet (Flare hackathon, post-submission-deadline log)
================================================================================
Runs as a Windows service (PRV1311-AnchorWriter), same shape as the other
five: STARTUP health row, then an infinite loop -- one cycle every
CYCLE_SEC, one recordDivergence call per symbol in ANCHOR_SYMBOLS per cycle,
sleep, repeat. Ctrl+C-safe for interactive runs, RestartCount 999 under Task
Scheduler for unattended ones.

SAFETY MODEL -- this is the one thing that had to change to make this file
possible. flare/deploy_anchor.py's interactive CLI path gates every mainnet
send behind a blocking typed-confirmation prompt (_confirm_mainnet_send);
that cannot exist here -- an unattended service has no console to type into,
and blocking on input() forever is just a hung service. It is NOT replaced
with nothing. It is replaced with four mechanical ceilings, checked BEFORE
every send, refused loudly (record_health + print) rather than silently, and
never bypassed:

  1. Chain ID must be exactly 14 (Flare mainnet) or every send this cycle is
     refused. Same class of guard as flare/deploy_anchor.py's _connect().
  2. Wallet balance must be at least MIN_WALLET_BALANCE FLR, checked at
     startup and before every cycle. Below it, the cycle is refused, not the
     whole service killed -- balance can recover (someone tops it up) without
     needing a restart.
  3. Total FLR spent today (summed fresh from anchor_log, not an in-memory
     counter -- survives a service restart without losing track) must stay
     under MAX_FLR_PER_DAY. Checked before EACH call, not just once per
     cycle, since five calls happen per cycle and gas prices can move
     between them.
  4. At most MAX_CALLS_PER_RUN sends per cycle -- one per symbol in
     ANCHOR_SYMBOLS, hard capped even if that list ever grows without this
     constant being updated too.

Every refusal -- chain id, balance, daily cap, and a per-symbol "no recent
decision row" skip -- writes its own rider_health row with a distinct status
and prints loudly. Never fails silent; that was the entire point of this
design.

decisionHash is computed exactly as it is in the interactive path: the most
recent rider_flare decision row for that symbol, canonicalized
(flare/decision_hash.py) and hashed, never a placeholder. If a symbol has no
recent rider_flare row, that symbol is skipped for this cycle and the reason
is logged -- never substituted.

PAPER TRADING NOTE: this file only ever calls recordDivergence() on
DivergenceAnchor, a read-and-anchor contract with no custody, no token
transfers, and no owner. It is not, and does not become, order-execution
code. The distinction from the rest of this codebase's "PAPER ONLY" rule
still holds -- rider_team.py/solo_rider.py never place real orders anywhere;
this file spends real FLR on gas for on-chain commitments, which is a
different, bounded, mechanically-capped thing.

Run standalone (uses the same mechanical ceilings either way):
  cd C:\\Users\\Autry\\accum-flip\\Prv1311
  .\\venv\\Scripts\\Activate
  python -m flare.anchor_writer
Module form, not `python flare/anchor_writer.py` by path -- see the import
guard below for why that specific invocation silently fails.
================================================================================
"""

import os
import sys
import time
from datetime import datetime, timezone, time as dtime

# Everything below this line can fail before any logger/health-row exists --
# this exact class of failure took the service down silently on its first
# real deployment: registered by file path instead of `-m flare.anchor_writer`,
# which put flare/ (not Prv1311/) on sys.path, so `import rider_team` and
# `from supabase_client import ...` failed at import time, before record_health
# was even importable. Task Scheduler just showed LastTaskResult=1 with no
# log line anywhere -- the health-row architecture never got a chance to run.
# Exit 78 specifically (distinct from any error raised once the service is
# actually up) so that failure mode is legible from the exit code alone, with
# no log file to read.
try:
    from web3 import Web3

    import rider_team
    from supabase_client import get_client, record_health
    from flare.deploy_anchor import (
        NETWORKS, _compile, _connect, _account, record_divergence,
    )
    from flare.decision_hash import fetch_latest_decision_row
except Exception as e:
    print(f"[anchor_writer] STARTUP FAILED before logging existed -- "
          f"{type(e).__name__}: {e}", file=sys.stderr)
    print("[anchor_writer] Likely cause: wrong invocation. Must run as "
          "'python -m flare.anchor_writer' with Prv1311/ as the working "
          "directory -- not 'python flare/anchor_writer.py' by path, which "
          "puts flare/ on sys.path instead of the repo root.", file=sys.stderr)
    sys.exit(78)

ANCHOR_SYMBOLS = ["OP/USD", "FLR/USD", "ARB/USD", "BTC/USD", "ETH/USD"]
CYCLE_SEC = 8 * 60 * 60  # every 8 hours (raised from 12h -- more on-chain
                          # samples before Aug 21 judging; see MAX_FLR_PER_DAY)

MAX_CALLS_PER_RUN = 5       # one per symbol in ANCHOR_SYMBOLS, hard stop
MAX_FLR_PER_DAY = 1.8       # 3 cycles/day x 5 calls x ~0.0926 FLR ~= 1.39;
                             # 1.8 gives headroom for gas spikes without
                             # effectively disabling the cap
                             # (was temporarily raised to 3.0 for the 2026-08-11
                             # 5/5 manual verification cycle; reverted immediately
                             # after -- see docs/CHANGELOG.md)
MIN_WALLET_BALANCE = 2.0    # hard floor -- refuse to start a cycle, or send,
                             # below this (was 5.0; deliberately spending
                             # this wallet down toward empty before judging)

ANCHOR_LOG_TABLE = "anchor_log"

_client = get_client()


def _flr_spent_today() -> float:
    """Sums anchor_log.flr_paid for today (UTC calendar day), queried fresh
    every check -- not an in-memory counter, so a service restart mid-day
    doesn't lose track of what's already been spent today."""
    start_of_day = datetime.combine(
        datetime.now(timezone.utc).date(), dtime.min, tzinfo=timezone.utc
    ).isoformat()
    res = (
        _client.table(ANCHOR_LOG_TABLE).select("flr_paid")
        .gte("ts", start_of_day).execute()
    )
    return sum(float(r["flr_paid"]) for r in res.data)


def _flr_spent_all_time() -> float:
    res = _client.table(ANCHOR_LOG_TABLE).select("flr_paid").execute()
    return sum(float(r["flr_paid"]) for r in res.data)


def _log_anchor_row(symbol, feed_id_hex, tx_hash, gas_used, flr_paid,
                     divergence_bps, decision_hash_hex, cycle_id):
    _client.table(ANCHOR_LOG_TABLE).insert({
        "symbol": symbol,
        "feed_id": feed_id_hex,
        "tx_hash": tx_hash,
        "gas_used": gas_used,
        "flr_paid": flr_paid,
        "divergence_bps": divergence_bps,
        "decision_hash": decision_hash_hex,
        "cycle_id": cycle_id,
    }).execute()


def _run_cycle():
    network = NETWORKS["mainnet"]

    w3 = _connect(network)
    if w3.eth.chain_id != network["chain_id"]:
        # _connect() already refuses internally, but this is defense in
        # depth in case that ever changes -- refusing to send is this
        # function's job specifically, not just _connect()'s.
        record_health("anchor_writer", "REFUSED_CHAIN_ID", {
            "connected_chain_id": w3.eth.chain_id, "expected": network["chain_id"],
        }, 0)
        print(f"[anchor_writer] REFUSED -- connected chain_id={w3.eth.chain_id}, "
              f"expected {network['chain_id']}. Skipping this cycle entirely.")
        return

    acct = _account(w3, network)
    balance_flr = float(Web3.from_wei(w3.eth.get_balance(acct.address), "ether"))
    if balance_flr < MIN_WALLET_BALANCE:
        record_health("anchor_writer", "REFUSED_LOW_BALANCE", {
            "balance": balance_flr, "min_required": MIN_WALLET_BALANCE,
        }, 0)
        print(f"[anchor_writer] REFUSED -- balance {balance_flr} FLR is below "
              f"MIN_WALLET_BALANCE={MIN_WALLET_BALANCE}. Skipping this cycle entirely.")
        return

    spent_today = _flr_spent_today()
    if spent_today >= MAX_FLR_PER_DAY:
        record_health("anchor_writer", "REFUSED_DAILY_CAP", {
            "spent_today": spent_today, "max_flr_per_day": MAX_FLR_PER_DAY,
        }, 0)
        print(f"[anchor_writer] REFUSED -- already spent {spent_today} FLR today, "
              f"MAX_FLR_PER_DAY={MAX_FLR_PER_DAY}. Skipping this cycle entirely.")
        return

    abi, _bytecode = _compile()
    contract_address = os.getenv("DIVERGENCE_ANCHOR_ADDRESS_MAINNET", "").strip()
    if not contract_address:
        record_health("anchor_writer", "REFUSED_NO_CONTRACT_ADDRESS", {}, 0)
        print("[anchor_writer] REFUSED -- DIVERGENCE_ANCHOR_ADDRESS_MAINNET not set. "
              "Skipping this cycle entirely.")
        return

    symbols_written = []
    symbols_skipped = []
    calls_made = 0
    gas_used_total = 0
    flr_spent_this_run = 0.0

    for symbol in ANCHOR_SYMBOLS[:MAX_CALLS_PER_RUN]:
        if calls_made >= MAX_CALLS_PER_RUN:
            break  # hard stop, defense in depth -- the slice above already enforces this

        try:
            fetch_latest_decision_row(symbol=symbol, fleet="rider_flare")
        except ValueError as e:
            record_health("anchor_writer", "SYMBOL_SKIPPED_NO_ROW", {
                "symbol": symbol, "reason": str(e),
            }, 0)
            print(f"[anchor_writer] SKIPPED {symbol} -- {e}")
            symbols_skipped.append({"symbol": symbol, "reason": str(e)})
            continue

        # NOT "+ flr_spent_this_run" -- REAL BUG, FOUND LIVE (2026-08-11,
        # 22:46:33 UTC): _flr_spent_today() re-queries anchor_log fresh on
        # every call, which already reflects every row THIS run has written
        # so far (each _log_anchor_row() insert below completes, and is
        # visible to the next SELECT, before the loop reaches the next
        # symbol). Adding flr_spent_this_run on top counted every write made
        # so far in this run a second time. Confirmed against the actual
        # incident: refused at spent_today=1.83650155 when the real total was
        # 1.5614508 (fresh DB total, itself already including this run's
        # writes) + 0.27505075 (flr_spent_this_run, the same writes again) --
        # ~0.24 FLR of real headroom was refused as if it didn't exist. The
        # fresh DB read alone is already authoritative -- same reasoning as
        # the cycle-level check above (line ~172), which never had this bug
        # because it runs once, before any writes exist yet this run.
        spent_today = _flr_spent_today()
        if spent_today >= MAX_FLR_PER_DAY:
            record_health("anchor_writer", "REFUSED_DAILY_CAP", {
                "symbol": symbol, "spent_today": spent_today,
                "max_flr_per_day": MAX_FLR_PER_DAY,
            }, 0)
            print(f"[anchor_writer] REFUSED {symbol} -- daily cap would be exceeded "
                  f"({spent_today} >= {MAX_FLR_PER_DAY}). Stopping this cycle's sends.")
            break

        try:
            tx_hash, gas_used, ev, decision_row = record_divergence(
                w3, acct, abi, contract_address, network,
                is_mainnet=True, symbol=symbol, interactive=False,
            )
        except Exception as e:
            record_health("anchor_writer", "SEND_FAILED", {
                "symbol": symbol, "error": str(e),
            }, 0)
            print(f"[anchor_writer] SEND FAILED for {symbol}: {e}")
            continue

        receipt = w3.eth.get_transaction_receipt(tx_hash)
        gas_price = receipt.get("effectiveGasPrice") or w3.eth.get_transaction(tx_hash)["gasPrice"]
        flr_paid = float(Web3.from_wei(gas_used * gas_price, "ether"))

        _log_anchor_row(
            symbol=symbol,
            feed_id_hex=ev["feedId"].hex(),
            tx_hash=tx_hash,
            gas_used=gas_used,
            flr_paid=flr_paid,
            divergence_bps=ev["divergenceBps"],
            decision_hash_hex=ev["decisionHash"].hex(),
            cycle_id=decision_row["cycle_id"],
        )

        calls_made += 1
        gas_used_total += gas_used
        flr_spent_this_run += flr_paid
        symbols_written.append({
            "symbol": symbol, "tx_hash": tx_hash, "gas_used": gas_used,
            "flr_paid": flr_paid, "divergence_bps": ev["divergenceBps"],
        })
        print(f"[anchor_writer] ANCHORED {symbol}: tx={tx_hash} gas={gas_used} "
              f"FLR={flr_paid} divergenceBps={ev['divergenceBps']}")

    record_health("anchor_writer", "CYCLE", {
        "symbols_written": symbols_written,
        "symbols_skipped": symbols_skipped,
        "gas_used_total": gas_used_total,
        "flr_spent_this_run": flr_spent_this_run,
        "flr_spent_today": _flr_spent_today(),
        "flr_spent_all_time": _flr_spent_all_time(),
    }, 0)
    print(f"[anchor_writer] cycle done -- {len(symbols_written)} anchored, "
          f"{len(symbols_skipped)} skipped, {flr_spent_this_run} FLR spent this run.")


def run_service():
    rider_team._install_rotating_log("anchor_writer")

    network = NETWORKS["mainnet"]
    try:
        w3 = _connect(network)
        acct = _account(w3, network)
        balance = float(Web3.from_wei(w3.eth.get_balance(acct.address), "ether"))
    except Exception as e:
        balance = None
        print(f"[anchor_writer] could not read startup balance: {e}")

    record_health("anchor_writer", "STARTUP", {
        "symbols": ANCHOR_SYMBOLS,
        "cadence_hours": CYCLE_SEC / 3600,
        "max_calls_per_run": MAX_CALLS_PER_RUN,
        "max_flr_per_day": MAX_FLR_PER_DAY,
        "min_wallet_balance": MIN_WALLET_BALANCE,
        "wallet_balance": balance,
    }, 0)

    print("=" * 78)
    print("      PRV1311 -- FLARE MAINNET ANCHOR WRITER")
    print("=" * 78)
    print(f"Symbols  : {ANCHOR_SYMBOLS}")
    print(f"Cadence  : every {CYCLE_SEC/3600:.0f}h")
    print(f"Ceilings : max {MAX_CALLS_PER_RUN} calls/run, "
          f"max {MAX_FLR_PER_DAY} FLR/day, min {MIN_WALLET_BALANCE} FLR balance")
    print("Status   : LIVE -- MAINNET (Ctrl+C to stop)\n")

    while True:
        try:
            _run_cycle()
            time.sleep(CYCLE_SEC)
        except KeyboardInterrupt:
            print("\n[anchor_writer] stopped safely.")
            break
        except Exception as e:
            record_health("anchor_writer", "LOOP_ERROR", {"error": str(e)}, 0)
            print(f"\n[anchor_writer Error] {e}")
            time.sleep(60)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # This entry point takes no arguments and falls straight into a live
        # mainnet loop with no confirmation prompt (see SAFETY MODEL above).
        # An unrecognized flag -- including a typo'd --help -- must refuse to
        # start, not silently run the service. Caught live: an unhandled
        # `--help` here ran a real anchoring cycle before it could be killed.
        print(f"[anchor_writer] REFUSED -- unrecognized arguments {sys.argv[1:]}. "
              "This entry point takes no arguments and starts the live mainnet "
              "service unconditionally; run with none.", file=sys.stderr)
        sys.exit(64)
    run_service()
