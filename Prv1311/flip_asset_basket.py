"""
================================================================================
PROJECT: Prv1311 — Asset Basket (config helper)
FILE: flip_asset_basket.py
================================================================================
Ported from Accum-Flip "Initialize Asset Basket." The ORIGINAL was a placeholder
seed list meant to be replaced by the real screener (mktcap 250M-100B, volume/
depth filtered). Prv1311's screener.py + config.WATCHLIST already do that live —
so this file is kept as a CONFIG HELPER, not the source of truth.

WHAT IT PROVIDES:
  - SEED_BASKET: the original 10 tickers + their CoinGecko ids (the useful
    artifact — the ticker->coingecko_id map, needed for token-directory profiles)
  - DEFERRED: tickers held pending Scout-gate (FLR, MORPHO, RE, WFLI)
  - get_basket(source=None): returns the active basket. If a future screener is
    passed (any object/list of tickers), it supplies the tickers and this maps
    them to CoinGecko ids where known — exactly the "structure asset input as a
    parameter" note from the source. With no source, returns the seed basket.
  - coingecko_id(ticker): the lookup other tools call for CoinGecko profiles.

This does NOT screen or trade. It's a naming/id bridge.
================================================================================
"""

# Seed basket (ticker -> coingecko_id). From the source node.
SEED_BASKET = [
    {'ticker': 'XLM',  'coingecko_id': 'stellar'},
    {'ticker': 'XRP',  'coingecko_id': 'ripple'},
    {'ticker': 'ALGO', 'coingecko_id': 'algorand'},
    {'ticker': 'ARB',  'coingecko_id': 'arbitrum'},
    {'ticker': 'SUI',  'coingecko_id': 'sui'},
    {'ticker': 'POL',  'coingecko_id': 'polygon-ecosystem-token'},
    {'ticker': 'OP',   'coingecko_id': 'optimism'},
    {'ticker': 'AERO', 'coingecko_id': 'aerodrome-finance'},
    {'ticker': 'AXL',  'coingecko_id': 'axelar'},
    {'ticker': 'ORCA', 'coingecko_id': 'orca'},
]

# Held pending Scout-gate (from the source note).
DEFERRED = ['FLR', 'MORPHO', 'RE', 'WFLI']

# Extra ticker->id mappings for the wider watchlist (extend as needed for the
# token directory; unknown tickers just return None and can be filled in later).
EXTRA_IDS = {
    'FLR': 'flare-networks',
    'MORPHO': 'morpho',
    'HBAR': 'hedera-hashgraph',
    'SOL': 'solana',
    'AVAX': 'avalanche-2',
    'LINK': 'chainlink',
    'AAVE': 'aave',
    'FIL': 'filecoin',
    'JASMY': 'jasmycoin',
    'ROSE': 'oasis-network',
    'PAXG': 'pax-gold',
    'SHIB': 'shiba-inu',
    'NEAR': 'near',
    'KSM': 'kusama',
    'ZRO': 'layerzero',
    'STORJ': 'storj',
}


def _id_map():
    m = {a['ticker']: a['coingecko_id'] for a in SEED_BASKET}
    m.update(EXTRA_IDS)
    return m


def coingecko_id(ticker):
    """ticker -> coingecko_id, or None if unknown. The lookup token-directory
    profile fetches call."""
    return _id_map().get(ticker.upper())


def get_basket(source=None):
    """Return the active basket as [{ticker, coingecko_id}, ...].

    source: optional future screener output — a list of tickers, or a list of
    dicts with a 'ticker' key, or an object with a .tickers attribute. When
    given, IT supplies the tickers (the seed is bypassed) and we map ids where
    known. With no source, returns the seed basket unchanged."""
    if source is None:
        return list(SEED_BASKET)

    # normalize source into a list of tickers
    tickers = []
    if hasattr(source, 'tickers'):
        tickers = list(source.tickers)
    elif isinstance(source, (list, tuple)):
        for item in source:
            if isinstance(item, str):
                tickers.append(item)
            elif isinstance(item, dict) and 'ticker' in item:
                tickers.append(item['ticker'])

    idm = _id_map()
    return [{'ticker': t.upper(), 'coingecko_id': idm.get(t.upper())}
            for t in tickers]


if __name__ == "__main__":
    print("Seed basket:")
    for a in get_basket():
        print(f"  {a['ticker']:<6} -> {a['coingecko_id']}")
    print(f"Deferred (Scout-gate): {', '.join(DEFERRED)}")
    print("\nID lookups:")
    for t in ['XRP', 'SOL', 'FLR', 'UNKNOWNCOIN']:
        print(f"  {t:<12} -> {coingecko_id(t)}")
    print("\nFuture-screener example (['HBAR','AVAX','LINK']):")
    for a in get_basket(['HBAR', 'AVAX', 'LINK']):
        print(f"  {a['ticker']:<6} -> {a['coingecko_id']}")