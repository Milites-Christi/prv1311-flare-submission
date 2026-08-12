"""
================================================================================
flare/deploy_anchor.py — compiles, deploys, and smoke-tests DivergenceAnchor
on Coston2 (default) or Flare mainnet (Flare hackathon, Day 3, Task 4)
================================================================================
One-shot script, not a service: deploys DivergenceAnchor.sol, then calls
recordDivergence() once for BTC/USD with a real live Coinbase price, and
prints everything needed to verify the on-chain read independently -- contract
address, tx hash, explorer URLs, the decoded event, and a fresh off-chain
ftso.py read taken right after, so oracleValue/oracleTimestamp in the event
can be compared against an independent Python read of the same feed.

NETWORK SELECTION -- the guard is a guard on both paths, not removed for
mainnet, inverted:
  - default (no flag): Coston2, chain 114. Refuses to proceed against
    anything else.
  - --mainnet: Flare mainnet, chain 14. Refuses to proceed against anything
    else. There is no other way into this path -- it does not activate by
    RPC availability, by .env content, or by inference. Only this literal
    flag on the command line.
Either way, _connect() checks the live chain_id against whichever network was
explicitly selected and raises before anything is signed or sent if they
don't match. Requires FLARE_DEPLOYER_KEY in .env -- holding testnet C2FLR for
the default path, real FLR for --mainnet (see flare/README.md for wallet
setup).

--dry-run builds the deploy transaction (gas, gasPrice, nonce, chainId, data
length -- everything but the signature) and prints it. Nothing is signed,
nothing is sent, regardless of which network is selected. Since a dry run by
definition never deploys anything, it cannot preview the recordDivergence
transaction either -- that call needs a real deployed contract address, which
only exists after an actual deploy runs.

On --mainnet (and only on --mainnet -- Coston2 stays frictionless, no real
value there), every transaction is gated behind a printed confirmation banner
(network, chain id, from address, balance, estimated cost) and a blocking
typed-confirmation prompt before it is signed and broadcast. See
_confirm_mainnet_send().

decisionHash is a real commitment: keccak256 of the canonical JSON
serialization (flare/decision_hash.py) of the most recent rider_flare
BTC/USD row in rider_decisions at call time -- symbol, every gate verdict,
block reason, and prices the engine actually used. Anyone can independently
fetch that same row later (by its cycle_id, printed below) and recompute
the identical hash. A continuous per-cycle on-chain writer is out of scope
here (gas/cadence undecided, roadmap item) -- this fixes the one-shot smoke
test's commitment, not the cadence of future writes.
================================================================================
"""

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import argparse
import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from web3 import Web3
from web3.logs import DISCARD

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

import solcx

from screener import exchange
from flare.ftso import feed_id_bytes
from flare.price_adapter import get_live_price
from flare.decision_hash import canonicalize_decision_row, fetch_latest_decision_row

# Network profiles. Coston2 is the default in every code path below -- a run
# with no flags behaves exactly as it always has. --mainnet is the only way
# to select the second profile; there is no fallback, no auto-detection, and
# _connect() independently verifies the live chain_id matches whichever one
# was picked before anything downstream runs.
NETWORKS = {
    "coston2": {
        "rpc": "https://coston2-api.flare.network/ext/C/rpc",
        "chain_id": 114,
        "explorer": "https://coston2-explorer.flare.network",
        "label": "Coston2",
        "currency": "C2FLR",
        "faucet": "https://faucet.flare.network/coston2",
    },
    "mainnet": {
        "rpc": "https://flare-api.flare.network/ext/C/rpc",
        "chain_id": 14,
        "explorer": "https://flare-explorer.flare.network",
        "label": "Flare Mainnet",
        "currency": "FLR",
        "faucet": None,
    },
}

MAINNET_CONFIRM_PHRASE = "SEND MAINNET"

CONTRACT_PATH = Path(__file__).resolve().parent / "contracts" / "DivergenceAnchor.sol"
VENUE_DECIMALS = 8

FEE_CALCULATOR_ABI = [{
    "inputs": [{"internalType": "bytes21[]", "name": "_feedIds", "type": "bytes21[]"}],
    "name": "calculateFeeByIds",
    "outputs": [{"internalType": "uint256", "name": "_fee", "type": "uint256"}],
    "stateMutability": "view", "type": "function",
}]


def _compile():
    solcx.install_solc('0.8.24')
    out = solcx.compile_files(
        [str(CONTRACT_PATH)],
        output_values=['abi', 'bin'],
        solc_version='0.8.24',
        evm_version='cancun',
    )
    key = [k for k in out if k.endswith(':DivergenceAnchor')][0]
    return out[key]['abi'], out[key]['bin']


def _connect(network):
    w3 = Web3(Web3.HTTPProvider(network["rpc"]))
    if not w3.is_connected():
        raise RuntimeError(f"Could not connect to {network['label']} RPC.")
    if w3.eth.chain_id != network["chain_id"]:
        raise RuntimeError(
            f"Connected chain_id={w3.eth.chain_id}, expected {network['label']}'s "
            f"{network['chain_id']}. Refusing to proceed -- this script must never "
            f"touch anything but the network explicitly selected on the command line."
        )
    return w3


def _account(w3, network):
    key = os.getenv("FLARE_DEPLOYER_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "FLARE_DEPLOYER_KEY is empty or missing in Prv1311/.env -- "
            "see flare/README.md for wallet setup steps."
        )
    acct = w3.eth.account.from_key(key)
    balance = w3.eth.get_balance(acct.address)
    print(f"Deployer address: {acct.address}")
    print(f"Balance: {Web3.from_wei(balance, 'ether')} {network['currency']}")
    if balance == 0:
        fund_hint = (f"fund it from {network['faucet']}" if network["faucet"]
                     else "fund it with real FLR before proceeding")
        raise RuntimeError(
            f"Deployer wallet {acct.address} has 0 {network['currency']} -- {fund_hint}."
        )
    return acct


def _gas_price_wei(w3, receipt, tx_hash):
    price = receipt.get("effectiveGasPrice")
    if price is None:
        price = w3.eth.get_transaction(tx_hash)["gasPrice"]
    return price


def _print_gas(label, receipt, gas_price_wei, currency):
    cost_wei = receipt.gasUsed * gas_price_wei
    cost = Web3.from_wei(cost_wei, "ether")
    print(f"{label} gas used      : {receipt.gasUsed} gas units")
    print(f"{label} gas price     : {gas_price_wei} wei/gas")
    print(f"{label} cost          : {cost} {currency}")


def _tx_data_len_bytes(data):
    if isinstance(data, (bytes, bytearray)):
        return len(data)
    if isinstance(data, str) and data.startswith("0x"):
        return (len(data) - 2) // 2
    return len(data or "")


def _print_dry_run(tx, network, label):
    """Prints the exact transaction that WOULD be sent -- to, data length,
    gas, fee fields, chainId, nonce, and cost -- then returns without
    signing or broadcasting anything. Used for both --dry-run and as the
    visible content of the mainnet confirmation banner.

    web3.py's build_transaction() picks legacy 'gasPrice' or EIP-1559
    'maxFeePerGas'/'maxPriorityFeePerGas' depending on what the connected
    node reports supporting -- Flare mainnet returned EIP-1559 fields, so
    this handles both rather than assuming one."""
    gas = tx.get("gas")
    legacy_price = tx.get("gasPrice")
    max_fee = tx.get("maxFeePerGas")
    priority_fee = tx.get("maxPriorityFeePerGas")
    # worst-case per-gas cost: legacy gasPrice if that's what the tx uses,
    # otherwise maxFeePerGas (the actual per-gas cap under EIP-1559).
    per_gas_cost = legacy_price if legacy_price is not None else max_fee
    cost = Web3.from_wei(gas * per_gas_cost, "ether") if gas and per_gas_cost else "?"
    print("\n" + "-" * 78)
    print(f"  {label}")
    print("-" * 78)
    print(f"  network    : {network['label']} (chain_id={network['chain_id']})")
    print(f"  to         : {tx.get('to') or '(none -- this is a contract creation)'}")
    print(f"  from       : {tx.get('from')}")
    print(f"  data length: {_tx_data_len_bytes(tx.get('data'))} bytes")
    print(f"  gas        : {gas}")
    if legacy_price is not None:
        print(f"  gasPrice   : {legacy_price} wei ({Web3.from_wei(legacy_price, 'gwei')} gwei)")
    if max_fee is not None:
        print(f"  maxFeePerGas        : {max_fee} wei ({Web3.from_wei(max_fee, 'gwei')} gwei)")
        print(f"  maxPriorityFeePerGas: {priority_fee} wei ({Web3.from_wei(priority_fee, 'gwei')} gwei)")
    print(f"  chainId    : {tx.get('chainId')}")
    print(f"  nonce      : {tx.get('nonce')}")
    print(f"  cost (worst case @ per-gas cap above): {cost} {network['currency']}")
    print("-" * 78)


def _confirm_mainnet_send(w3, acct, network, tx, label):
    """Blocking confirmation gate before ANY transaction is signed and sent
    on mainnet. Never called for Coston2 -- no real value at stake there, so
    the original friction-free flow stays friction-free. On mainnet, this is
    the one deliberate keystroke required between the command and the send;
    anything other than an exact match on MAINNET_CONFIRM_PHRASE aborts."""
    balance = w3.eth.get_balance(acct.address)
    print("\n" + "!" * 78)
    print(f"  MAINNET TRANSACTION -- {label}")
    print("!" * 78)
    print(f"  Network  : {network['label']} (chain_id={network['chain_id']})")
    print(f"  From     : {acct.address}")
    print(f"  Balance  : {Web3.from_wei(balance, 'ether')} {network['currency']}")
    _print_dry_run(tx, network, "TRANSACTION TO BE SENT")
    print("!" * 78)
    typed = input(
        f"\nType exactly '{MAINNET_CONFIRM_PHRASE}' to broadcast this transaction "
        f"on Flare Mainnet. Anything else aborts: "
    )
    if typed != MAINNET_CONFIRM_PHRASE:
        raise RuntimeError("Mainnet send aborted -- confirmation phrase did not match.")
    print("Confirmed. Sending...")


def _raw_rpc_get_feed(rpc_url, ftso_v2_address, feed_id: bytes):
    """Third independent read. Bypasses BOTH web3.py's contract abstraction
    AND flare/ftso.py's caching/known-good-universe logic entirely -- a raw
    JSON-RPC POST via `requests` with hand-encoded calldata. If this
    disagrees with the on-chain event, the bug is in our reading code, not
    in the story being told about it. If it agrees, three independently-
    coded paths (Solidity, ftso.py, this) all saw the same chain state."""
    selector = Web3.keccak(text="getFeedById(bytes21)")[:4]
    # bytes21 is a fixed-size bytes type -- ABI-encoded as the 21 bytes
    # right-padded with zeros out to one 32-byte word.
    padded_feed_id = feed_id + b"\x00" * (32 - len(feed_id))
    calldata = "0x" + selector.hex() + padded_feed_id.hex()
    resp = requests.post(rpc_url, json={
        "jsonrpc": "2.0", "id": 1, "method": "eth_call",
        "params": [{"to": ftso_v2_address, "data": calldata}, "latest"],
    }, timeout=15)
    resp.raise_for_status()
    result = resp.json()
    if "error" in result:
        raise RuntimeError(f"raw eth_call failed: {result['error']}")
    raw = result["result"][2:]  # strip 0x
    # three return values, each a 32-byte word: uint256, int8, uint64
    value = int(raw[0:64], 16)
    decimals_byte = int(raw[64:128][-2:], 16)
    decimals = decimals_byte - 256 if decimals_byte > 127 else decimals_byte
    timestamp = int(raw[128:192], 16)
    return value, decimals, timestamp


def _write_env_var(env_path, key, value):
    """Adds or replaces exactly one line in .env, in place. Never reads
    back or prints any OTHER line -- only touches the one target key."""
    lines = []
    found = False
    if env_path.exists():
        with open(env_path, "r") as f:
            for line in f:
                if line.startswith(f"{key}="):
                    lines.append(f"{key}={value}\n")
                    found = True
                else:
                    lines.append(line)
    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{key}={value}\n")
    with open(env_path, "w") as f:
        f.writelines(lines)


def deploy(w3, acct, abi, bytecode, network, is_mainnet, dry_run=False):
    Contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx = Contract.constructor().build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "chainId": network["chain_id"],
    })

    if dry_run:
        _print_dry_run(tx, network, "DRY RUN -- DEPLOY (nothing sent)")
        return None, None, None

    if is_mainnet:
        _confirm_mainnet_send(w3, acct, network, tx, "DEPLOY DivergenceAnchor")

    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"Deploy tx sent: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"Deployed at: {receipt.contractAddress}")
    gas_price = _gas_price_wei(w3, receipt, tx_hash)
    _print_gas("Deploy", receipt, gas_price, network["currency"])
    return receipt.contractAddress, receipt.gasUsed, tx_hash.hex()


def record_divergence(w3, acct, abi, contract_address, network, is_mainnet, symbol="BTC/USD", interactive=True):
    """interactive=True (default) is the CLI path -- a real mainnet send
    blocks on the typed confirmation in _confirm_mainnet_send(). Set
    interactive=False only from a caller that has already gated the send
    through its own mechanical checks (flare/anchor_writer.py's chain-id /
    balance / daily-cap / call-count ceilings) -- an unattended service
    cannot block on input() waiting for a human. This is the ONLY other
    caller allowed to pass interactive=False; everything else keeps the
    typed-confirmation gate."""
    contract = w3.eth.contract(address=contract_address, abi=abi)
    feed_id = feed_id_bytes(symbol)

    ticker = exchange.fetch_ticker(symbol)
    venue_price = ticker["last"]
    venue_value = int(round(venue_price * (10 ** VENUE_DECIMALS)))
    print(f"Venue (Coinbase) {symbol}: {venue_price} -> venueValue={venue_value}, decimals={VENUE_DECIMALS}")

    # Real commitment: the most recent rider_flare decision row for this
    # symbol, canonicalized and hashed -- not a placeholder string. Whoever
    # wants to verify this later fetches rider_decisions where cycle_id =
    # <printed below> and recomputes the same hash from
    # canonicalize_decision_row(). Raises ValueError (not caught here) if no
    # row exists for this symbol -- callers must not substitute a
    # placeholder when that happens, so this deliberately does not catch it.
    decision_row = fetch_latest_decision_row(symbol=symbol, fleet="rider_flare")
    print(f"Anchoring decision row: cycle_id={decision_row['cycle_id']} "
          f"ts={decision_row['ts']} block_reason={decision_row['block_reason']} "
          f"fired={decision_row['fired']}")
    decision_hash = Web3.keccak(canonicalize_decision_row(decision_row))

    # Read the fee the same way the contract itself will: via whatever
    # FeeCalculator address it resolved at construction.
    fee = 0
    fee_calc_addr = contract.functions.feeCalculator().call()
    if int(fee_calc_addr, 16) != 0:
        fee_calc = w3.eth.contract(address=fee_calc_addr, abi=FEE_CALCULATOR_ABI)
        fee = fee_calc.functions.calculateFeeByIds([feed_id]).call()
    print(f"Live fee for this feed: {fee} wei")

    tx = contract.functions.recordDivergence(
        feed_id, venue_value, VENUE_DECIMALS, decision_hash
    ).build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "chainId": network["chain_id"],
        "value": fee,
    })
    # eth_estimateGas has no safety margin built in -- confirmed live on
    # mainnet (tx 737cb784..., 2026-08-11): a warm-storage recordDivergence
    # call estimated at exactly 137,563 gas reverted out-of-gas at execution
    # time, gasUsed == gas limit sent, no margin for FtsoV2's own internal
    # warm/cold state shifting between estimation and mining. +20% headroom
    # on top of the raw estimate; unused gas is refunded, so this costs
    # nothing when the raw estimate was already sufficient.
    tx["gas"] = int(tx["gas"] * 1.2)

    if is_mainnet and interactive:
        _confirm_mainnet_send(w3, acct, network, tx, f"recordDivergence({symbol})")

    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"recordDivergence tx sent: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    gas_price = _gas_price_wei(w3, receipt, tx_hash)
    _print_gas("recordDivergence", receipt, gas_price, network["currency"])

    events = contract.events.DivergenceRecorded().process_receipt(receipt, errors=DISCARD)
    if not events:
        raise RuntimeError("No DivergenceRecorded event found in the receipt.")
    ev = events[0]["args"]

    print("\n--- Decoded DivergenceRecorded event (from the chain) ---")
    for k, v in ev.items():
        print(f"  {k}: {v.hex() if isinstance(v, (bytes, bytearray)) else v}")

    # Independent check #1: flare/ftso.py's own get_live_price(), the same
    # module used elsewhere in this codebase. Confirms our Python reader
    # agrees with what the contract saw on-chain.
    off_chain = get_live_price(symbol)
    print("\n--- Independent read #1: flare/ftso.py (same module used elsewhere) ---")
    if off_chain:
        price, source, ts, fid = off_chain
        print(f"  price: {price}  oracle_timestamp: {ts}  feed_id: {fid}")
    else:
        print("  FAILED -- no off-chain price available for comparison")

    # Independent check #2, the THIRD read overall: raw JSON-RPC eth_call,
    # bypassing web3.py's contract abstraction AND flare/ftso.py entirely.
    ftso_v2_addr = contract.functions.ftsoV2().call()
    raw_value, raw_decimals, raw_timestamp = _raw_rpc_get_feed(network["rpc"], ftso_v2_addr, feed_id)
    raw_price = raw_value / (10 ** raw_decimals)
    print("\n--- Independent read #2 (THIRD read): raw JSON-RPC eth_call ---")
    print(f"  price: {raw_price}  oracle_timestamp: {raw_timestamp}  "
          f"raw_value: {raw_value}  raw_decimals: {raw_decimals}")
    print(f"  matches on-chain event oracleValue exactly: {raw_value == ev['oracleValue']}")
    print(f"  matches on-chain event oracleTimestamp exactly: {raw_timestamp == ev['oracleTimestamp']}")

    # Independent recomputation of decisionHash, post-mine -- re-fetches the
    # SAME cycle_id fresh from Supabase (not reusing decision_row above) and
    # recomputes independently of whatever value was actually sent, same
    # verification pattern used on Coston2.
    from supabase_client import get_client
    verify_res = (
        get_client().table("rider_decisions").select("*")
        .eq("cycle_id", decision_row["cycle_id"]).eq("symbol", symbol)
        .eq("fleet", "rider_flare").limit(1).execute()
    )
    verify_row = verify_res.data[0]
    recomputed_hash = Web3.keccak(canonicalize_decision_row(verify_row))
    print("\n--- Independent decisionHash re-verification ---")
    print(f"  on-chain decisionHash        : {ev['decisionHash'].hex()}")
    print(f"  independently recomputed hash: {recomputed_hash.hex()}")
    match = ev["decisionHash"] == recomputed_hash
    print(f"  RESULT: {'MATCH' if match else 'MISMATCH'}")
    if not match:
        raise RuntimeError("decisionHash MISMATCH -- reporting as-is, not papering over it.")

    return tx_hash.hex(), receipt.gasUsed, ev, decision_row


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Deploy + smoke-test DivergenceAnchor. Defaults to Coston2; "
                     "--mainnet is the only way to target Flare mainnet."
    )
    parser.add_argument(
        "--mainnet", action="store_true",
        help="Target Flare mainnet (chain 14) instead of the default, Coston2 "
             "(chain 114). Every transaction requires a typed confirmation before "
             "it is signed and sent.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Build and print the deploy transaction (to, data length, gas, "
             "gasPrice, chainId, nonce, cost) without signing or sending anything. "
             "No confirmation prompt -- nothing broadcasts either way.",
    )
    args = parser.parse_args()

    network = NETWORKS["mainnet"] if args.mainnet else NETWORKS["coston2"]

    print("=" * 78)
    mode = "DRY RUN" if args.dry_run else "deploy + smoke test"
    print(f"  DivergenceAnchor -- {network['label']} {mode}")
    print("=" * 78)
    abi, bytecode = _compile()
    print("Compiled OK.")
    w3 = _connect(network)
    print(f"Connected to {network['label']} (chain_id={w3.eth.chain_id})")
    acct = _account(w3, network)

    contract_address, deploy_gas, deploy_tx_hash = deploy(
        w3, acct, abi, bytecode, network, is_mainnet=args.mainnet, dry_run=args.dry_run,
    )

    if args.dry_run:
        print("\nDry run complete -- deploy transaction shown above, nothing sent.")
        print("recordDivergence cannot be previewed in a dry run -- it needs a real")
        print("deployed contract address, which only exists after an actual deploy runs.")
        raise SystemExit(0)

    tx_hash, call_gas, event, decision_row = record_divergence(
        w3, acct, abi, contract_address, network, is_mainnet=args.mainnet, symbol="BTC/USD",
    )

    _write_env_var(
        ENV_PATH,
        "DIVERGENCE_ANCHOR_ADDRESS_MAINNET" if args.mainnet else "DIVERGENCE_ANCHOR_ADDRESS",
        contract_address,
    )
    print(f"\nWrote {'DIVERGENCE_ANCHOR_ADDRESS_MAINNET' if args.mainnet else 'DIVERGENCE_ANCHOR_ADDRESS'}"
          f"={contract_address} to {ENV_PATH}")

    print("\n" + "=" * 78)
    print("  SUMMARY")
    print("=" * 78)
    print(f"Network             : {network['label']}")
    print(f"Contract address    : {contract_address}")
    print(f"Explorer (contract) : {network['explorer']}/address/{contract_address}")
    print(f"Deploy tx           : {deploy_tx_hash}")
    print(f"Explorer (deploy tx): {network['explorer']}/tx/{deploy_tx_hash}")
    print(f"recordDivergence tx : {tx_hash}")
    print(f"Explorer (call tx)  : {network['explorer']}/tx/{tx_hash}")
    print(f"Deploy gas used     : {deploy_gas} gas units")
    print(f"recordDivergence gas used: {call_gas} gas units")
    print(f"(Full gas-unit / gas-price / {network['currency']}-cost breakdown for both printed above.)")
