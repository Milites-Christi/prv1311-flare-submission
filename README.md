# PRV1311 on Flare

An autonomous market-state reader. It measures whether Flare's FTSOv2 oracle
diverges from a centralized venue on the same asset, runs a live paper
trading strategy priced entirely off FTSO alongside the Coinbase-priced
original, and anchors the resulting decisions to Flare mainnet as hash
commitments made before the outcome exists.

Not a trading bot pitch. The artifact that matters here is the comparison
data and the record of how it was reached.

---

## Why this exists

Look at the tape right now. War. A closed strait. More war. Price hikes. Oil
up. Every input a person uses to decide is screaming at the same time, and
retail traders are getting drowned — I've been one of them, on my own money,
in the same market.

PRV1311 has been flipping through it. Roughly two weeks of continuous
operation, paper-traded, with fees modeled honestly on both legs, sitting at
**$1,200+ in ledger profit**. Not because it predicted a war or read a
headline. Because it doesn't read headlines at all. It reads market state,
mechanically, the same way every cycle, and it writes down why it declined
every trade it didn't take.

These are not normal conditions. They're about as far from normal as I've
traded through, and the standard fear gauges have been close to useless for
weeks. So the honest question isn't whether this works in a bull market.
It's the reverse: **it works now, in this. What does it do when things are
ordinary?**

I don't know yet. But I'd rather be holding a system proven in the ugly part
of the cycle than one tuned on a calm one.

---

## Who it's for

Institutional capital runs on a discipline ordinary investors never get
handed: a full signal stack, read mechanically, with emotion taken out of
the reading. That's not a secret and it isn't magic. It's a method, and
methods can be scaled down.

PRV1311 is that architecture sized for people who don't have six figures to
deploy. Tiered position sizing, small compounding flips, no requirement that
you time anything or watch anything. Stability in this market shouldn't be a
privilege of account size.

---

## How I got here

This didn't come from backtesting a hunch.

I spend most of my life doing hermeneutics — the discipline of reading a
text on its own terms. You establish what's actually there before you decide
what it means. You read it in its context, not yours. You let it say what it
says instead of importing what you expected to find, and you show your work
so someone else can check whether you read it honestly or just read yourself
into it.

That's the whole method here, pointed at a market instead of a manuscript.
Most trading systems import a thesis and then hunt for confirmation. This one
establishes market state first — what the data actually says, gate by gate —
and only then asks whether that state warrants action. The decision log is
the "show your work" part. Every candidate, every gate, every refusal,
written down before the outcome exists.

The gates aren't decoration. Each one exists because a specific way of
misreading the market kept costing money, and each one blocks that specific
misreading.

---

## Why Flare

Three reasons, in order of how much they matter to whether this thing lives.

**1. Coarse prices make the logic blind.**

Every entry gate in PRV1311 is percentage-based off price. That means the
quality of the price *resolution* isn't a detail — it's whether the gate can
form a judgment at all.

It couldn't. Thirteen of our assets were blocked on `pullback_insufficient`
in 97–100% of cycles. OP showed exactly **one distinct Coinbase price across
114 samples over two hours and nineteen minutes** while the oracle moved
continuously underneath it. The gate wasn't rejecting bad setups. It was
blind — asked to measure a 10% pullback against a quote that structurally
could not express one, and returning a tautology every cycle.

FTSO's ~100-provider composite resolves prices a single venue can't. That
doesn't make it a substitute for a centralized feed. **It makes the engine
function on assets where a single venue couldn't support the logic at all.**

**2. Fees decide whether the strategy is viable.**

This is a high-frequency, low-percentage design. Scavenger targets sit at 3%,
riders at 5%, the entry tier lower still. At those margins fees aren't
overhead — they're existential. A ~0.6% round-trip taker fee consumes a fifth
of a 3% move before slippage, and that math kills small compounding flips
outright.

Flare's transaction costs are fractions of a cent. For a system built on many
small correct decisions rather than a few large ones, that isn't a
nice-to-have. It's the difference between a strategy that compounds and one
that donates. Execution on Flare isn't built yet — it's on the roadmap below
— but it is the natural home for this design, and the fee structure is why.

**3. The data is the whole trust surface.**

Take the human out of the reading and there's no one left to say "that price
looks wrong." A mechanical system inherits its data's flaws completely and
without protest. So price-feed integrity stops being an implementation detail
and becomes the entire trust model — which is why FTSO isn't a dependency I
assume is correct here. It's the hypothesis under test, measured against a
centralized venue across thousands of live decisions, with the results
anchored on-chain where anyone can check them.

---

## What the divergence numbers actually mean

Read the ranking carefully, because it's easy to read backwards.

**Divergence measures the venue's staleness, not the oracle's error.** A
composite of roughly a hundred providers carries better information about a
thin asset than any single exchange does. When FLR shows wide divergence,
that means one US exchange prices FLR worst — which is exactly what you'd
predict for the thinnest possible source, and exactly what the mechanism
below explains.

The clearest case: at one anchor, Coinbase's OP quote sat frozen at exactly
$0.092 for **fourteen minutes across thirteen consecutive samples** while
FTSO tracked real movement underneath it. An engine pricing off Coinbase was
computing percentage gates against a fourteen-minute-old fiction. The
FTSO-priced engine had current information. That's not the oracle disagreeing
with the market — that's the venue having nothing to say, and the oracle
being the only source that noticed.

### A/B result: FTSO vs centralized venue

Shared window: `ts >= 2026-08-07 23:20:04+00`, both fleets, the same
16-symbol `FLARE_UNIVERSE` filter.

| block_reason | rider (Coinbase-priced) | rider_flare (FTSO-priced) |
|---|---|---|
| Total rows | 6,124 | 6,736 |
| `pullback_insufficient` | 5,188 (84.7%) | 6,408 (95.1%) |
| `already_held` | 911 (14.9%) | 150 (2.2%) |
| `floor_fetch_failed` | 9 | 175 (2.6%) |
| `price_fetch_failed` | 12 | — |
| `obi_gate_blocked` | 2 | 1 |
| null | 2 | 2 |

**Three caveats, stated plainly rather than softened:**

- **The 84.7% vs 95.1% gap is not an oracle effect.** It's driven by
  `already_held` — the venue-priced engine has more capital deployed in
  these sixteen assets from a longer run. Excluding `already_held`, the two
  engines agree on 99.5% (venue) and 97.3% (oracle) of evaluations.
- **`floor_fetch_failed` (175) is CoinGecko rate-limiting** on a separate
  90-day historical-data call, not an FTSO failure. FTSO returned zero read
  failures across the window; the centralized venue returned twelve
  `price_fetch_failed`.
- **The oracle-priced engine logged more rows despite a smaller universe.**
  The venue engine's team-full and cash-floor gates use `BREAK`, so once
  the team fills, later symbols in the list go unevaluated that cycle — a
  structural property of the parent engine, unrelated to data source.

### Tick-size mechanism

The ranking above isn't arbitrary. Tick size relative to price — how coarse
a venue's quote grid is at that asset's price point — predicts the
*ranking* of divergence magnitude: Pearson r = 0.9887, Spearman ρ = 0.965
(n=114 per symbol). It is **not a universal ceiling**: the half-tick bound
binds as an absolute limit for OP only. For the other 15 symbols the bound
is a fraction of a basis point, and read-timing plus real price movement
between snapshots dominate at that scale.

Convergent evidence: an independent proxy for the same property —
1/(distinct venue price levels observed) — correlates with the same
ranking at r = 0.99. FLR fits the pattern rather than breaking it: half-tick
bound 8.3 bps against an observed 9.45 bps. FLR ranking high doesn't mean
the oracle is unreliable for it — it means the single venue prices it
worst, exactly what the mechanism predicts for the thinnest source.

Real `quote_increment` per symbol, pulled live from Coinbase's public
product endpoint, not assumed:

| Symbol | quote_increment |
| --- | --- |
| OP-USD | 0.001 |
| FLR-USD | 0.00001 |
| ARB-USD | 0.0001 |
| BTC-USD | 0.01 |
| ETH-USD | 0.01 |
| SOL-USD | 0.01 |
| XRP-USD | 0.0001 |
| LINK-USD | 0.001 |
| AVAX-USD | 0.001 |
| ADA-USD | 0.00001 |
| HBAR-USD | 0.00001 |
| XLM-USD | 0.000001 |
| NEAR-USD | 0.0001 |
| AAVE-USD | 0.01 |
| UNI-USD | 0.0001 |
| ONDO-USD | 0.00001 |

---

## Asset roles

**BTC is not a tradable asset in PRV1311.** It's a directional reference —
part of how market state is read, not part of the traded universe. The only
case a user ends up holding BTC is electing to park realized profit there
rather than in a stablecoin. It appears in the divergence measurements
because it's a required input to the reading, not because the system buys it.

---

## What was here before

Worth stating plainly, because "evidence of new work" is easier to trust
when you can see what the starting point actually looked like.

Three independent, drifted copies of FTSOv2-reading logic existed in this
repo: root `flare_ftso.py` targeting the dead Coston2 testnet,
`Flare_Trial.py\Flare_ftso.py` on mainnet but stale, and an inline copy
inside what was then `solo_rider_flare.py`.

None handled the one behavior that actually matters on live mainnet:
**`getFeedsById` reverts the entire batch if any single requested feed ID
doesn't exist.** It doesn't return zero for the bad one — it takes the whole
call down. All three copies would have frozen every tracked symbol's price
the first time a bad symbol landed in a batch. That isn't in Flare's docs
anywhere I could find; it came out of running it against mainnet and
watching it fail.

That same file also sent `updated_at: "now()"` as a literal Python string on
every ledger upsert, which a `timestamptz` column rejects on insert.

**All of that is resolved.** The three copies are gone — one deleted with
`Flare_Trial.py\`, one renamed to `flare_ftso_legacy.py` to kill a
case-insensitive filename collision that was fatal on Linux and silent on
Windows, and the inline copy removed when `solo_rider_flare.py` became
`solo_rider.py`. The `"now()"` string is now a real
`datetime.now(timezone.utc).isoformat()`. One canonical reader replaced all
three.

---

## Built for this event

Everything new lives in `Prv1311/flare/`. Nothing outside it was modified
except six additive, default-preserving parameters on `rider_team.py`
(detailed below).

- **`ftso.py`** — the one canonical FTSOv2 reader. A fixed known-good
  universe (`establish_coverage()`) plus bisection (`_call_batch`) handles
  both discovering which symbols have live feeds and self-healing when a
  previously-good feed stops resolving mid-session. It never falls back to
  `getFeedById` to probe one symbol — a batch of size 1 is still
  `getFeedsById`. Confirmed 16 of 20 candidate symbols have live mainnet
  feeds (missing: `COTI`, `EUL`, `KAITO`, `LDO`), more coverage than I
  expected going in.
- **`price_adapter.py`** — single owner of the confirmed 16-symbol universe
  (`FLARE_UNIVERSE`) and `get_live_price()`. Established once at import, so
  every module shares one coverage answer rather than each deriving its own.
- **`divergence.py`** — one-shot FTSO-vs-venue spread measurement. Reads all
  16 symbols from Coinbase in a single bulk `fetch_tickers()` rather than 16
  separate calls: 16× less venue traffic, and it tightens the oracle/venue
  timestamp gap because one venue snapshot lines up against one oracle batch.
- **`divergence_recorder.py`** — the continuous version. Every 60s, one row
  per symbol to Supabase `oracle_divergence` with spread in bps, both raw
  timestamps, and a normalized `timestamp_gap_ms`. This is the dataset the
  finding rests on, not a debug log. Two venue calls per minute total.
- **`rider_flare.py`** — the FTSO-priced Rider twin. Same gate logic as
  `rider_team.py`, imported rather than copied, fixed to the 16-symbol
  universe, priced off FTSO for entry/exit. Separate ledger, separate state
  table, decision rows explicitly tagged `fleet='rider_flare'`.
- **`decision_hash.py`** — canonical serialization of a decision row, so the
  same row always hashes identically no matter when or where it's re-fetched.
  Explicit field order pinned from the live schema, fixed 10-decimal float
  formatting, nulls preserved as null.
- **`contracts/DivergenceAnchor.sol`** + **`deploy_anchor.py`** +
  **`anchor_writer.py`** — the on-chain half. See below.
- **`onchain_swaps.py`**, **`onchain_divergence.py`**,
  **`onchain_divergence_recorder.py`** — read-only measurement of on-chain
  DEX prices against off-chain, writing to its own table. Not wired into any
  trading path.
- **`coingecko_adapter.py`** — provider-agnostic historical-data adapter for
  the 90-day floor and rolling high.

### The contract

`DivergenceAnchor` is live on **Flare mainnet** at
`0x086b912dD8aD5639c5adFD57bF8724B485786eDC`. (The Coston2 deployment shares
that address through deterministic addressing — check the chain before
reading its history.)

The point that matters: **the contract reads FTSOv2 itself, on-chain.** The
oracle value is never a function argument. If Python read the price and wrote
it in, this would be a database storing my own claim about what the oracle
said. Instead the contract fetches the feed, normalizes both sides to 18
decimals, computes divergence in basis points on-chain, and emits it.

Alongside each reading it stores a `decisionHash` — the keccak256 of that
cycle's decision row, committed before the outcome existed. Reveal the row
later and anyone recomputes the hash. That's the part I actually care about.

### Honest scope of what's Flare-priced

Only the number used for entry/exit decisions comes from FTSO. Daily candles
feeding the regime and anomaly gates, the 90-day floor, the rolling 7-day
high, order-book imbalance, and order flow all still read a centralized
venue. Flare has no OHLCV history endpoint, no order book, and no trade tape
— there's nothing to build fully-Flare-priced versions of those signals from
in a week. `rider_flare.py`'s own docstring says so; it isn't hidden behind
the name.

---

## Ported / integrated

`rider_flare.py` doesn't duplicate `rider_team.py`'s gate loop — duplication
is exactly how the three original FTSO readers drifted apart. Instead
`run_cycle()` / `run_engine()` gained six default-preserving parameters. The
live `PRV1311-RiderTeam` service calls every one at its default, so its
behavior is unchanged.

| Parameter | Default | Purpose |
|---|---|---|
| `price_fn` | `screener.fetch_live_price` | swap in `price_adapter.get_live_price` for FTSO pricing |
| `fleet` | `'rider'` | explicit tag on every decision/cycle row — **mandatory**, not left to a DB column default; a silently-missing tag would land `rider_flare` rows as `'rider'` with no error and contaminate the entire comparison |
| `ledger_file` | `RIDER_LEDGER_FILE` | separate `data/rider_flare_ledger.json` |
| `state_table` | `'rider_state'` | separate `rider_flare_state` table |
| `universe_fn` | `None` → live market scan | fixed 16-symbol FTSO universe instead of the broad venue scan |
| `log_name` | `'rider_team'` | separate `logs/rider_flare.log` — two services sharing one rotating log risks a `PermissionError` on Windows during rollover |

Everything else — `screener`, `anomaly_gate`, `footprint_gate`,
`supabase_client`, `rider_decision_log`, `orderbook_imbalance`, `regime` —
is imported from the hardened shared modules, unmodified.

---

## Running

From `Prv1311/` — working directory matters, these are package-relative
imports:

```
python -m flare.divergence                    # one-shot spread report, 16 symbols
python -m flare.divergence_recorder           # continuous recorder -> oracle_divergence
python -m flare.rider_flare                   # FTSO-priced Rider twin
python -m flare.onchain_divergence_recorder   # on-chain vs off-chain measurement
python -m flare.anchor_writer                 # anchors readings to Flare mainnet
```

All are registered as Windows Task Scheduler services (AtStartup, SYSTEM,
`RestartCount 999`), install scripts in `Prv1311/`. `anchor_writer` spends
real FLR on registration — it's gated behind a typed confirmation and
mechanical per-run and per-day ceilings, and it refuses any chain but 14.

---

## Roadmap

- **Execution on Flare.** The fee argument above is the reason. Measurement
  ships now; execution is the next build.
- **FDC attestation of the venue price**, so both sides of the comparison are
  Flare-verified rather than one side taken on trust.
- **Provider-agnostic historical data.** The adapter is already built to
  accept alternate sources; B3 Data API is the next planned integration.
- **Dual-oracle consensus execution** for FLR and FXRP, weighing off-chain
  against on-chain OHLC to inform entry and exit.

---

## Repository layout

The repository root holds the original PRV1311 system — the parent build this
grew out of, kept for lineage and continuing development. `Prv1311/` is the
codebase this submission is about, and `Prv1311/flare/` is all the
Flare-specific work.

Where a filename appears in both places the two have diverged. `Prv1311/` is
what runs live.
