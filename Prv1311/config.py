"""
================================================================================
PROJECT: Prv1311 — Quantitative Asset Accumulation Engine (CORE Module)
FILE: config.py
================================================================================
Central control panel. All tunable thresholds, exchange targets, and ladder
sizes live here.

DATA SOURCE: Coinbase (real live volume + taker data via trades). Binance.US
was abandoned -- it had near-zero live volume (ghost town). Coinbase quotes in
USD (not USDT) and offers 1h/6h/1d candles (no 4h -- we use 6h in its place).

NOTE ON THE NUMBERS: the 6-2-1-1 ladder and -20/-40/-60% depths were tuned
against 6 years of real data. Do not revert to round-number placeholders
without re-measuring.
================================================================================
"""

# --- Execution Parameters ---
EXCHANGE_ID = 'coinbase'           # LIVE data source (real volume, taker data)
STARTING_CAPITAL_USD = 20000.0     # Total mock capital for the paper ledger
LEDGER_FILE = 'data/ledger.json'

# --- Ticker sanity check (screener.fetch_live_price -- protects every engine) ---
# Reject (return None) a live ticker read that diverges from the last daily close
# by more than this. Deliberately wide: real small-cap volatility (see HFT, a
# ~74% single-day intraday range on a genuine pump-then-crash) can legitimately
# move this much -- the goal is catching a broken/stale/corrupted print, not
# filtering volatility. A tight threshold would false-reject real trades constantly.
MAX_TICKER_DIVERGENCE_PCT = 50.0

# --- Exchange cost model (recorded economics only -- no entry/exit logic change) ---
# Applied by rider_team.py and scavengers.py on open AND close. Recorded as a
# separate 'exchange_fee' ledger field, distinct from the internal 20% platform
# fee (FEE_RATE) that both engines already skim off winning flips.
TAKER_FEE_PCT = 1.2   # Coinbase Advanced Trade taker fee, % of notional
MAKER_FEE_PCT = 0.6   # maker fee, % of notional
# Per-fleet execution style -- which fee applies to that fleet's open/close fills.
# Rider stays taker (unaffected); Scav assumes resting limit orders (see the fill
# model in scavengers.py -- entries/exits book the LIMIT price, not the polled
# ticker price, so a maker fee assumption is consistent with how fills are recorded).
RIDER_EXEC_STYLE = 'taker'
SCAV_EXEC_STYLE = 'maker'

# --- Multi-Asset Basket (Coinbase quotes in USD) ---
BASKET = ['XLM/USD', 'XRP/USD', 'ADA/USD']
TEST_SYMBOL = BASKET[0]

# --- Quote currency + timeframe mapping for Coinbase ---
QUOTE = 'USD'                      # Coinbase quotes in USD, not USDT
TF_SHORT = '1h'                    # short timeframe (unchanged)
TF_MED = '6h'                      # Coinbase has no 4h -> use 6h in its place

# --- Screener (Asset Selection) Parameters ---
PRICE_CEILING = 5.0
PRICE_FLOOR = 0.001
MIN_VOL_MCAP = 0.010
MAX_FDV_MCAP = 3.0
TOP_N = 20
# Liquidity gate reads candle volume (ticker quoteVolume is None on Coinbase).
# Min 24h dollar volume for an asset to count as tradeable-liquid:
MIN_24H_USD_VOLUME = 1_000_000     # $1M/day

# Stablecoins / pegged assets -- never traded (excluded everywhere).
STABLE_EXCLUDES = {'USDC', 'USDT', 'DAI', 'PYUSD', 'WBTC', 'USDS', 'GUSD',
                   'PAX', 'TUSD', 'BUSD', 'FRAX', 'USDP', 'CBETH', 'WETH',
                   'USD1', 'EURC', 'PYUSD'}
# Manual denylist -- category risk the metrics can't see (meme/hype).
DENYLIST = {'DOGE', 'SHIB', 'PEPE', 'BONK', 'WIF', 'FLOKI'}

# --- CORE Accumulator (Scale-In) Ladder ---
LADDER_SIZES = [6, 2, 1, 1]
RUNG_DROPS = [0.0, 0.20, 0.40, 0.60]

# --- Per-Asset Capital + Slice Sizing ---
CAPITAL_PER_ASSET = STARTING_CAPITAL_USD / len(BASKET)
SLICE_USD = CAPITAL_PER_ASSET / 12
ENTRY_BAND = 1.05

# --- Bucket structure (allocator) ---
BUCKET_USD = STARTING_CAPITAL_USD / 12   # $1,666.67
NUM_CORE_BUCKETS = 5
DIP_TRIGGER_PCT = 0.20

# --- RIDER selectivity gates (the three that stop indiscriminate firing) ---
MAX_CONCURRENT_RIDERS = 3          # hard exposure cap -- never ride more than 3 at once
RIDER_PULLBACK_PCT = 10.0          # meaningful dip, not 4% noise (was 4%)
RIDER_TARGET_PCT = 5.0             # flip target (+5%)
# GATE-PARITY AUDIT (2026-08-12): cross-imported directly by scavengers.py
# and dogs.py (neither has its own SCAV_FLOOR_BUFFER/DOG_FLOOR_BUFFER) —
# same class of issue as the old RIDER_TARGET_PCT cross-import. Editing this
# value retunes three fleets at once, not just Rider. Slated to split into
# SCAV_FLOOR_BUFFER / DOG_FLOOR_BUFFER after 2026-08-14; see
# docs/CHANGELOG.md "Gate-parity audit — decisions".
RIDER_FLOOR_BUFFER = 1.05          # rider stays out if price < floor*1.05 (that's CORE territory)
# Catch-band floor: entry must be BELOW the pullback trigger but ABOVE this, or it's
# a real breakdown, not a dip -- that's CORE's mechanical ladder's job, not a rejected
# opportunity for Rider. 30% sized off real history: worst actual Rider entry so far
# was 23.85% below its reference high; this leaves headroom above that without
# reaching into genuine-crash territory (7-day window absorbs more ordinary chop).
RIDER_MAX_DROP_PCT = 30.0

# ============================================================================
# --- RIDER TEAM: dynamic sizing (ADJUSTABLE — turn these dials and watch) ---
# ============================================================================
# The rider engine sizes its buckets from these knobs instead of a hardcoded
# $20k/12 split. Edit, restart the engine, observe. CORE is unaffected.
#
#   bucket = RIDER_POOL_USD / RIDER_COUNT, clamped to [RIDER_FLOOR, RIDER_CEILING]
#   RIDER_COUNT is TOTAL slots (includes the reserve slots).
#   reserve   = bucket * RESERVE_RIDERS  (held back, untouchable dry powder)
#   active    = RIDER_COUNT - RESERVE_RIDERS
#
# Examples:
#   POOL 20000, COUNT 12  -> bucket 1666.67, 10 active, reserve 3333.33
#   POOL 10000, COUNT 12  -> bucket  833.33, 10 active, reserve 1666.67
#   POOL  1667, COUNT  6  -> bucket  277.83,  4 active, reserve  555.67
#   POOL   250, COUNT  1  -> bucket  250.00 (floor), 1 slot, reserve 0 if 0
# ----------------------------------------------------------------------------
RIDER_POOL_USD = 20000.0     # total rider capital ($250 min – $20,000 max)
RIDER_COUNT = 12             # total slots to split the pool across (incl. reserve)
RIDER_CEILING = 1667.0       # max $ ONE rider deploys (entry cap — NOT a profit cap)
RIDER_FLOOR = 250.0          # min $ a rider deploys; engine won't slice below this
RESERVE_RIDERS = 2           # slots' worth held back as untouchable reserve

# --- RIDER TEAM (decoupled engine) ---
MAX_ACTIVE_RIDERS = 10             # up to 10 riders fire at once...
RESERVE_BUCKETS = 2                # ...leaving 2 buckets (~$3,333) ALWAYS as
                                   # dry powder -- the customer is never fully
                                   # stuck. Riders can never touch the reserve.
RIDER_LEDGER_FILE = 'data/rider_ledger.json'

# --- Sidebar token directory (rider_dashboard.py / token_directory.py) ---
WATCHLIST = [
    "XRP", "B3", "HBAR", "FLR", "ZORA", "XYO", "FIL", "PREDI", "NOICE",
    "AVAX", "PROMPT", "SOL", "LINGO", "PAXG", "JASMY", "LCX", "G", "ROSE",
    "KMNO", "BIRB", "BNKRW", "BNKR", "ZBCN", "DOVU", "CLAWNCH", "BTC2",
    "ORCA", "FIS", "STORJ", "ZRO", "ARC", "OPG", "CLAWBANK", "DELU",
    "ODAI", "AXOBOTL", "ALLO", "GWEI", "MORPHO", "FIGHT", "WCHAN",
    "CHIPPY", "GRVT", "TRUMP", "SHIB", "KSM", "RE", "MPLX", "BASED1",
    "GROVE", "ERA", "AAVE", "COTI", "NEAR", "LINK", "VVV", "AERO",
    "EUL", "KAITO",
]
# ============================================================================
# --- SCAVENGERS: isolated velocity engine (ADJUSTABLE) ---
# ============================================================================
# A small, SEPARATE high-velocity team. Fully isolated from the main Rider
# Team above -- own pool, own ledger, own reserve. Same mechanics, but enters
# on a shallower 5% pullback from a shorter 3-day high to scavenge routine
# chop. Turn the dials, restart scavengers.py, watch. Does not affect the
# main team at all.
#
#   bucket = SCAV_POOL_USD / SCAV_COUNT, clamped to [SCAV_FLOOR, SCAV_CEILING]
#   SCAV_COUNT is TOTAL slots (includes reserve).
#   active = SCAV_COUNT - SCAV_RESERVE_RIDERS
# ----------------------------------------------------------------------------
SCAV_POOL_USD = 2000.0        # scavenger capital, isolated ($250 min – $20k max)
SCAV_COUNT = 4                # total slots (incl. reserve)
SCAV_CEILING = 1667.0         # max $ one scavenger deploys (entry cap, not profit)
SCAV_FLOOR = 250.0            # min $ a scavenger deploys
SCAV_RESERVE_RIDERS = 1       # slots' worth held back untouchable
SCAV_PULLBACK_PCT = 5.0       # shallower dip than the 10% main team
SCAV_LOOKBACK_DAYS = 3        # shorter, more responsive window (vs main team's 7)
# Own flip target -- NOT RIDER_TARGET_PCT. Retrace-to-prior-high math is why 5%
# is wrong here: entry at -5% off the high needs a 95% retrace to hit a 5% target
# (vs the Rider's -10% entry / 5% target, which only needs a 45% retrace). 3.0%
# puts Scav's retrace requirement at 57% -- much closer to the Rider's own bar.
SCAV_TARGET_PCT = 3.0
# Catch-band floor, same idea as RIDER_MAX_DROP_PCT but tighter: the SAME magnitude
# of decline compressed into a 3-day window is a sharper, more crash-like signal than
# over 7 days (see HFT: pumped ~3.5x then crashed >50% inside this exact window).
# Worst real Scav entry so far was 14.59% below its reference high; 20% leaves
# headroom without inviting pump-and-dump catches. Below this band is CORE's
# territory, not a rejected opportunity for Scav.
SCAV_MAX_DROP_PCT = 20.0
SCAV_LEDGER_FILE = 'data/scav_ledger.json'

# ============================================================================
# --- DOGS LAB: experimental research fleet (ADJUSTABLE / EXPERIMENTAL) ---
# ============================================================================
# NOT a finished strategy — a TEST BENCH. Pulls week/3d/24h high+low per asset,
# runs the existing triple-confirmation (RSI/absorption/VWAP) for a probability
# read, and LOGS ITS REASONING on every candidate it evaluates (fired or not).
# The point is a decision record to learn from, not just trades. Everything here
# is meant to be edited, rerun, or ripped out. Isolated pool + ledger + page.
#
#   bucket = DOG_POOL_USD / DOG_COUNT, clamped to [DOG_FLOOR, DOG_CEILING]
#   DOG_COUNT is TOTAL slots (includes reserve).
#   active = DOG_COUNT - DOG_RESERVE_RIDERS
# ----------------------------------------------------------------------------
DOG_POOL_USD = 2000.0         # experimental capital, isolated
DOG_COUNT = 4                 # total slots (incl. reserve)
DOG_CEILING = 1667.0          # max $ one dog deploys
DOG_FLOOR = 250.0             # min $ a dog deploys
DOG_RESERVE_RIDERS = 1        # slots held back untouchable

DOG_PULLBACK_PCT = 10.0       # candidate trigger: >=10% below a window high
DOG_TARGET_PCT = 3.5          # exit flip (+3.5%)

# Which windows the lab measures (days). It records the high AND low of each.
DOG_WINDOWS = [1, 3, 7]       # 24h / 72h / weekly

# Probability gate: how many of the 3 triple-confirmation checks must agree
# before a dog is allowed to FIRE. 0 = observe-only (log, never spend).
# 1 = loose, 2 = moderate, 3 = strict (all agree). Set to spend = 2.
DOG_MIN_CONFIRMATIONS = 2

DOG_LEDGER_FILE = 'data/dog_ledger.json'

# ============================================================================
# --- MARKOV SIGNAL ENGINE: isolated (the crown jewel, ported) ---
# ============================================================================
# Directional sweep/reclaim/divergence engine. Signal-only in this spot system:
# computes long+short, LOGS both, FIRES longs only. Uses a real stop-loss (2:1
# R:R) — the ONE fleet that cuts losses, by design. Fully isolated: own hourly
# data layer (markov_screener), own ledger, own page.
# ----------------------------------------------------------------------------
MARKOV_POOL_USD = 2000.0        # isolated experimental capital
MARKOV_BUCKET_USD = 500.0       # $ per position
MARKOV_LEDGER_FILE = 'data/markov_ledger.json'

# --- EWMA BUY-ZONE LAB (isolated) ---
EWMA_LAB_LEDGER_FILE = 'data/ewma_lab_ledger.json'

# --- REGIME CLASSIFIER LAB (isolated) ---
REGIME_LAB_LEDGER_FILE = 'data/regime_lab_ledger.json'

# --- CHEAP WINDOW LAB (isolated) ---
CHEAP_WINDOW_LEDGER_FILE = 'data/cheap_window_ledger.json'
# --- ORDER-BOOK IMBALANCE (OBI) LAB (isolated, live depth) ---
OBI_LEDGER_FILE = 'data/obi_ledger.json'

# ============================================================================
# --- CORE: mechanical bear/crash accumulation engine (isolated) ---
# ============================================================================
# Buys the breach, scales down the [6,2,1,1] ladder, never-cuts. Self-calculates
# the whole ladder from ANY pool value ($250-$20k, random cents fine).
# ----------------------------------------------------------------------------
CORE_POOL_USD = 20000.0        # max; self-scales down for any real client amount
CORE_COUNT = 8                 # buckets (incl. reserve); bucket = POOL/COUNT clamped
CORE_CEILING = 1667.0          # max $ per bucket (caps entry, not profit)
CORE_FLOOR = 250.0             # min $ per bucket (refuse-and-explain below this)
CORE_RESERVE_BUCKETS = 2       # untouchable reserve slots — never 100% deployed
CORE_LEDGER_FILE = 'data/core_ledger.json'