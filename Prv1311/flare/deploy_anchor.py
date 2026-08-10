"""
================================================================================
flare/deploy_anchor.py — compiles, deploys, and smoke-tests DivergenceAnchor
on Coston2 (Flare hackathon, Day 3, Task 4)
================================================================================
One-shot script, not a service: deploys DivergenceAnchor.sol to Coston2, then
calls recordDivergence() once for BTC/USD with a real live Coinbase price, and
prints everything needed to verify the on-chain read independently -- contract
address, tx hash, explorer URLs, the decoded event, and a fresh off-chain
ftso.py read taken right after, so oracleValue/oracleTimestamp in the event
can be compared against an independent Python read of the same feed.

Coston2 only. Never touches mainnet -- COSTON2_CHAIN_ID is checked against
the connected RPC before anything is signed or sent. Requires
FLARE_DEPLOYER_KEY in .env, holding testnet C2FLR only (see
flare/README.md / the wallet setup steps for how to get one).

decisionHash here is a descriptive string, not a real rider_decisions
commitment -- correct for this smoke test per the Day 3 spec. The real
recorder (later work) will hash the canonical JSON of the actual decision
row instead.
================================================================================
"""

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from web3 import Web3

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

import solcx

from screener import exchange
from flare.ftso import feed_id_bytes
from flare.price_adapter import get_live_price

COSTON2_RPC = "https://coston2-api.flare.network/ext/C/rpc"
COSTON2_CHAIN_ID = 114
COSTON2_EXPLORER = "https://coston2-explorer.flare.network"

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


def _connect():
    w3 = Web3(Web3.HTTPProvider(COSTON2_RPC))
    if not w3.is_connected():
        raise RuntimeError("Could not connect to Coston2 RPC.")
    if w3.eth.chain_id != COSTON2_CHAIN_ID:
        raise RuntimeError(
            f"Connected chain_id={w3.eth.chain_id}, expected Coston2's {COSTON2_CHAIN_ID}. "
            f"Refusing to proceed -- this script must never touch anything but Coston2."
        )
    return w3


def _account(w3):
    key = os.getenv("FLARE_DEPLOYER_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "FLARE_DEPLOYER_KEY is empty or missing in Prv1311/.env -- "
            "see flare/README.md for wallet setup steps."
        )
    acct = w3.eth.account.from_key(key)
    balance = w3.eth.get_balance(acct.address)
    print(f"Deployer address: {acct.address}")
    print(f"Balance: {Web3.from_wei(balance, 'ether')} C2FLR")
    if balance == 0:
        raise RuntimeError(
            f"Deployer wallet {acct.address} has 0 C2FLR -- fund it from "
            f"https://faucet.flare.network/coston2 first."
        )
    return acct


def _gas_price_wei(w3, receipt, tx_hash):
    price = receipt.get("effectiveGasPrice")
    if price is None:
        price = w3.eth.get_transaction(tx_hash)["gasPrice"]
    return price


def _print_gas(label, receipt, gas_price_wei):
    cost_wei = receipt.gasUsed * gas_price_wei
    cost_c2flr = Web3.from_wei(cost_wei, "ether")
    print(f"{label} gas used      : {receipt.gasUsed} gas units")
    print(f"{label} gas price     : {gas_price_wei} wei/gas")
    print(f"{label} cost          : {cost_c2flr} C2FLR")


def _raw_rpc_get_feed(ftso_v2_address, feed_id: bytes):
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
    resp = requests.post(COSTON2_RPC, json={
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


def deploy(w3, acct, abi, bytecode):
    Contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx = Contract.constructor().build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "chainId": COSTON2_CHAIN_ID,
    })
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"Deploy tx sent: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"Deployed at: {receipt.contractAddress}")
    gas_price = _gas_price_wei(w3, receipt, tx_hash)
    _print_gas("Deploy", receipt, gas_price)
    return receipt.contractAddress, receipt.gasUsed, tx_hash.hex()


def record_btc_divergence(w3, acct, abi, contract_address):
    contract = w3.eth.contract(address=contract_address, abi=abi)
    feed_id = feed_id_bytes("BTC/USD")

    ticker = exchange.fetch_ticker("BTC/USD")
    venue_price = ticker["last"]
    venue_value = int(round(venue_price * (10 ** VENUE_DECIMALS)))
    print(f"Venue (Coinbase) BTC/USD: {venue_price} -> venueValue={venue_value}, decimals={VENUE_DECIMALS}")

    decision_hash = Web3.keccak(text=f"DivergenceAnchor Coston2 smoke test - BTC/USD - {time.time()}")

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
        "chainId": COSTON2_CHAIN_ID,
        "value": fee,
    })
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"recordDivergence tx sent: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    gas_price = _gas_price_wei(w3, receipt, tx_hash)
    _print_gas("recordDivergence", receipt, gas_price)

    events = contract.events.DivergenceRecorded().process_receipt(receipt)
    if not events:
        raise RuntimeError("No DivergenceRecorded event found in the receipt.")
    ev = events[0]["args"]

    print("\n--- Decoded DivergenceRecorded event (from the chain) ---")
    for k, v in ev.items():
        print(f"  {k}: {v.hex() if isinstance(v, (bytes, bytearray)) else v}")

    # Independent check #1: flare/ftso.py's own get_live_price(), the same
    # module used elsewhere in this codebase. Confirms our Python reader
    # agrees with what the contract saw on-chain.
    off_chain = get_live_price("BTC/USD")
    print("\n--- Independent read #1: flare/ftso.py (same module used elsewhere) ---")
    if off_chain:
        price, source, ts, fid = off_chain
        print(f"  price: {price}  oracle_timestamp: {ts}  feed_id: {fid}")
    else:
        print("  FAILED -- no off-chain price available for comparison")

    # Independent check #2, the THIRD read overall: raw JSON-RPC eth_call,
    # bypassing web3.py's contract abstraction AND flare/ftso.py entirely.
    ftso_v2_addr = contract.functions.ftsoV2().call()
    raw_value, raw_decimals, raw_timestamp = _raw_rpc_get_feed(ftso_v2_addr, feed_id)
    raw_price = raw_value / (10 ** raw_decimals)
    print("\n--- Independent read #2 (THIRD read): raw JSON-RPC eth_call ---")
    print(f"  price: {raw_price}  oracle_timestamp: {raw_timestamp}  "
          f"raw_value: {raw_value}  raw_decimals: {raw_decimals}")
    print(f"  matches on-chain event oracleValue exactly: {raw_value == ev['oracleValue']}")
    print(f"  matches on-chain event oracleTimestamp exactly: {raw_timestamp == ev['oracleTimestamp']}")

    return tx_hash.hex(), receipt.gasUsed, ev


if __name__ == "__main__":
    print("=" * 78)
    print("  DivergenceAnchor -- Coston2 deploy + smoke test")
    print("=" * 78)
    abi, bytecode = _compile()
    print("Compiled OK.")
    w3 = _connect()
    print(f"Connected to Coston2 (chain_id={w3.eth.chain_id})")
    acct = _account(w3)
    contract_address, deploy_gas, deploy_tx_hash = deploy(w3, acct, abi, bytecode)
    tx_hash, call_gas, event = record_btc_divergence(w3, acct, abi, contract_address)

    _write_env_var(ENV_PATH, "DIVERGENCE_ANCHOR_ADDRESS", contract_address)
    print(f"\nWrote DIVERGENCE_ANCHOR_ADDRESS={contract_address} to {ENV_PATH}")

    print("\n" + "=" * 78)
    print("  SUMMARY")
    print("=" * 78)
    print(f"Contract address    : {contract_address}")
    print(f"Explorer (contract) : {COSTON2_EXPLORER}/address/{contract_address}")
    print(f"Deploy tx           : {deploy_tx_hash}")
    print(f"Explorer (deploy tx): {COSTON2_EXPLORER}/tx/{deploy_tx_hash}")
    print(f"recordDivergence tx : {tx_hash}")
    print(f"Explorer (call tx)  : {COSTON2_EXPLORER}/tx/{tx_hash}")
    print(f"Deploy gas used     : {deploy_gas} gas units")
    print(f"recordDivergence gas used: {call_gas} gas units")
    print("(Full gas-unit / gas-price / C2FLR-cost breakdown for both printed above.)")
