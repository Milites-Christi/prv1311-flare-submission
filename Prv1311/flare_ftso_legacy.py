from web3 import Web3
from datetime import datetime, timezone

# 1. Connect to the Flare Network (Coston2 Testnet)
RPC_URL = "https://coston2-api.flare.network/ext/C/rpc"
w3 = Web3(Web3.HTTPProvider(RPC_URL))

# 2. The Canonical FlareContractRegistry (Same address on all Flare networks)
REGISTRY_ADDRESS = Web3.to_checksum_address("0xaD67FE66660Fb8dFE9d6b1b4240d8650e30F6019")

# Minimal ABI to interact with the Registry
REGISTRY_ABI = [
    {
        "inputs": [{"internalType": "string", "name": "_name", "type": "string"}],
        "name": "getContractAddressByName",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    }
]

# Initialize the Registry contract
registry_contract = w3.eth.contract(address=REGISTRY_ADDRESS, abi=REGISTRY_ABI)

# 3. Minimal ABI for FTSO v2
FTSOV2_ABI = [
    {
        "inputs": [{"internalType": "bytes21", "name": "_feedId", "type": "bytes21"}],
        "name": "getFeedById",
        "outputs": [
            {"internalType": "uint256", "name": "value", "type": "uint256"},
            {"internalType": "int8", "name": "decimals", "type": "int8"},
            {"internalType": "uint64", "name": "timestamp", "type": "uint64"}
        ],
        "stateMutability": "view",
        "type": "function"
    }
]

# 4. Helper Function: Convert Asset Name to Flare's 21-byte Feed ID format
def get_feed_id(category: int, name: str) -> bytes:
    """
    Category 1 is Crypto. Name (e.g., 'BTC/USD') is converted to bytes 
    and padded to 20 bytes.
    """
    cat_byte = category.to_bytes(1, 'big')
    name_bytes = name.encode('utf-8')
    padding = b'\x00' * (20 - len(name_bytes))
    return cat_byte + name_bytes + padding

# 5. Execute the Dynamic On-Chain Query
def check_flare_macro(asset_name: str = "BTC/USD"):
    try:
        # Step A: Dynamically resolve the true FtsoV2 address from the Registry
        ftso_v2_address = registry_contract.functions.getContractAddressByName("FtsoV2").call()
        if int(ftso_v2_address, 16) == 0:
            return {"status": "error", "message": "FtsoV2 not found in registry"}

        # Step B: Initialize the actual FTSO contract
        ftso_v2 = w3.eth.contract(address=ftso_v2_address, abi=FTSOV2_ABI)
        
        # Step C: Generate Feed ID and pull the decentralized price
        feed_id = get_feed_id(1, asset_name)
        value, decimals, timestamp = ftso_v2.functions.getFeedById(feed_id).call()
        
        actual_price = value / (10 ** decimals)
        last_updated = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        
        return {
            "status": "success",
            "asset": asset_name,
            "price": actual_price,
            "ftso_contract_used": ftso_v2_address,
            "last_updated_utc": last_updated.strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    print(f"Resolving FTSOv2 via Registry: {REGISTRY_ADDRESS}...")
    result = check_flare_macro("BTC/USD")
    print(result)