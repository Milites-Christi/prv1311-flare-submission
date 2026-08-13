# PRV1311 on Flare

An autonomous market-state reader. It measures whether Flare's FTSOv2 oracle
diverges from a centralized venue on the same asset, runs a live paper
trading strategy priced entirely off FTSO alongside the Coinbase-priced
original, and anchors the resulting decisions to Flare mainnet as hash
commitments made before the outcome exists.

Not a trading bot pitch. The artifact that matters here is the comparison
data and the record of how it was reached.

---

Why this exists

Retail traders are unknowingly competing against institutional hardware. 
The chart available to a retail participant is the visible residue of activity 
already executed on systems they cannot observe, and every retail automation 
reads that same residue as though it were signal.

PRV1311 was built to address that gap where it originates: in the reading of the market itself.

The present environment is an unusually severe test of that premise. 
Conditions are as adverse as any I have traded through; the conventional sentiment
gauges have been unreliable for months, and the system has continued operating 
throughout. Roughly two weeks of continuous operation, paper-traded, with fees 
modeled on both legs, currently at $1,200+ in ledger profit. 
Not through prediction — the engine does not read headlines. It reads market state 
mechanically, on an identical cycle every time, and records the reasoning behind 
every position it declined to take.

The relevant question is therefore inverted. It is not whether the architecture
performs in favorable conditions, but what it does when conditions become ordinary.
That remains to be measured. I would rather hold a system demonstrated in the difficult
portion of a cycle than one calibrated during a calm one.

Who it's for

Institutional capital operates on a discipline retail participants are never handed: 
a complete signal stack, read mechanically, with emotional interpretation removed 
from the reading. This is neither secret nor proprietary. 
It is a method, and methods scale down.

PRV1311 is that architecture sized for participants without seven figures to deploy. 
Tiered position sizing, small compounding flips, and no requirement that the user time 
entries or monitor positions. 
Stability in this market should not be a function of account size.
---

## How I got here

This didn't come from backtesting a hunch.

My training is in hermeneutics — the science of interpretation, the discipline 
of reading a text strictly on its own terms. In this architecture, the method 
is pointed at a market rather than a manuscript.

Conventional trading systems import a thesis and hunt for confirmation. 
This framework establishes market state first, analyzing what the data dictates, 
gate by gate, before determining whether that state warrants action.

[ Raw Market Data ] ──> [ Systematic Gates ] ──> [ Market State ] ──> [ Action / Refusal ]
                                 │
                                 └──> Logs to: [ Decision Log ] ──> [ Hash anchored on Flare ]

The Architecture of Interpretation: Context Over Expectation. 

The engine isolates what is actively occurring in the data, stripping 
away preconceived macro narrative and prior expectation.
The "Show Your Work" Principle. Every candidate, gate evaluation, and 
refusal is committed to a deterministic decision log and hash-anchored 
on-chain before the financial outcome exists. Reveal a row afterward, 
and any party can recompute the hash independently.
Defensive Gate Design. The evaluation gates are not decorative. Each 
was engineered to block a specific historical misreading of the market 
that previously resulted in capital loss.

---

Why Flare

Three reasons, ordered by how directly each bears on whether the system is viable at all.

1. Coarse price resolution renders the logic blind.


Every entry gate in PRV1311 is percentage-based off price. 
Price resolution is therefore not an implementation detail;
it determines whether a gate can form a judgment at all.

It could not. Thirteen assets were blocked on pullback_insufficient in 97–100% of cycles. 
OP registered one distinct Coinbase price across 114 samples over two hours and nineteen 
minutes while the oracle moved continuously beneath it. The gate was not rejecting weak 
setups. It was blind — asked to measure a 10% pullback against a quote structurally 
incapable of expressing one, and returning a tautology every cycle.

FTSO's approximately 100-provider composite resolves prices a single venue cannot. 
This does not make it a substitute for a centralized feed. 
It makes the engine operable on assets where a single venue cannot support the logic at all.


2. Fee structure determines whether the strategy is viable.

This is a high-frequency, low-percentage design.
Scavenger targets sit at 3%, riders at 5%, the entry tier lower still. At those margins 
fees are not overhead; they are determinative. A ~0.6% round-trip taker fee consumes a 
fifth of a 3% move before slippage, which eliminates small compounding flips outright.

Flare's transaction costs are fractions of a cent. For a system constructed from many 
small correct decisions rather than a few large ones, that distinction separates a 
strategy that compounds from one that donates. Execution on Flare is not yet built;
it appears on the roadmap below, but the fee structure is why it is the natural venue for this design.

3. The data is the entire trust surface.

Remove the human from the reading and no participant remains to observe that a price is wrong. 
A mechanical system inherits the flaws of its data completely and without objection. 
Price-feed integrity therefore ceases to be an implementation concern and becomes the whole 
of the trust model — which is why FTSO is not treated here as a dependency assumed correct. 
It is the hypothesis under test, measured against a centralized venue across thousands of 
live decisions, with the results anchored on-chain where any party can verify them.

What the divergence numbers indicate

The ranking below is easily read backwards, so the interpretation warrants stating first.

Divergence measures the venue's staleness, not the oracle's error. A composite of 
approximately one hundred providers carries better information about a thin asset than
any single exchange does. Where FLR shows wide divergence, the finding is that one 
US exchange prices FLR worst; precisely what would be predicted for the thinnest 
available source, and precisely what the mechanism described below accounts for.

The clearest instance: at one anchor, the Coinbase OP quote remained fixed at exactly 
$0.092 for fourteen minutes across thirteen consecutive samples while FTSO tracked 
continuous movement beneath it. An engine pricing off that venue was computing percentage
gates against a fourteen-minute-old value. The FTSO-priced engine held current information. 
This is not the oracle disagreeing with the market; it is the venue having nothing to report,
and the oracle being the only source that registered the change.

A/B result: FTSO vs centralized venue

Shared window: ts >= 2026-08-07 23:20:04+00, both fleets, identical 16-symbol FLARE_UNIVERSE filter.

block_reason	rider (Coinbase-priced)	rider_flare (FTSO-priced)
Total rows	6,124	6,736
pullback_insufficient	5,188 (84.7%)	6,408 (95.1%)
already_held	911 (14.9%)	150 (2.2%)
floor_fetch_failed	9	175 (2.6%)
price_fetch_failed	12	—
obi_gate_blocked	2	1
null	2	2

Three caveats, stated directly rather than softened:

The 84.7% versus 95.1% gap is not an oracle effect. It is driven by already_held:
The venue-priced engine holds more capital deployed across these sixteen assets from a longer run. 
Excluding already_held, the two engines agree on 99.5% (venue) and 97.3% (oracle) of evaluations.
floor_fetch_failed (175) reflects CoinGecko rate-limiting on a separate 90-day historical-data call,
not an FTSO failure. FTSO returned zero read failures across the window; the centralized venue 
returned twelve price_fetch_failed.
The oracle-priced engine logged more rows despite a smaller universe. 

The venue engine's team-full and cash-floor gates use BREAK, so once the team fills, later symbols 
in the list go unevaluated for that cycle — a structural property of the parent engine, unrelated 
to the data source.

Tick-size mechanism:

The ranking is not arbitrary. Tick size relative to price; the coarseness of a venue's quote grid 
at a given asset's price point predicts the ranking of divergence magnitude across the sixteen symbols: 
Pearson r = 0.9887, Spearman ρ = 0.965. Each symbol's mean divergence derives from 114 samples; 
the correlation itself is computed across the sixteen symbol-level means.

This is not a universal ceiling. The half-tick bound binds as an absolute limit for OP alone.
For the remaining fifteen symbols, the bound is a fraction of a basis point, and read-timing
together with genuine price movement between snapshots dominates at that scale.

Convergent evidence: an independent proxy for the same property — 1/(distinct venue price levels observed) 
— correlates with the same ranking at r = 0.99. 
FLR conforms to the pattern rather than breaking it, with a half-tick bound of 8.3 bps against an 
observed 9.45 bps. 

FLR ranking high does not indicate the oracle is unreliable for that asset; it indicates the 
single venue prices it worst, which is what the mechanism predicts for the thinnest source.

Actual quote_increment per symbol, retrieved live from Coinbase's public product endpoint rather than assumed:

---

Asset roles

BTC is not a tradable asset in PRV1311. It functions as a directional reference;
a component of how market state is read, not a member of the traded universe.

The sole circumstance in which a user holds BTC is by electing to park realized 
profit there rather than in a stablecoin. It appears in the divergence measurements
because it is a required input to the reading, not because the system acquires it.

What was here before

Worth stating directly, since evidence of new work is easier to evaluate against
a visible starting point.

Three independent and mutually drifted copies of FTSOv2-reading logic existed in this 
repository: root flare_ftso.py, targeting the discontinued Coston2 testnet; Flare_Trial.py\Flare_ftso.py, 
on mainnet but stale; and an inline copy within what was then solo_rider_flare.py.

None handled the one behavior that governs live mainnet use: getFeedsById reverts the 
entire batch if any single requested feed ID does not exist. It does not return zero 
for the invalid entry; it fails the whole call. All three copies would have frozen every
tracked symbol's price the first time an invalid symbol entered a batch. 

This behavior does not appear in Flare's documentation, so far as I could determine. 
It surfaced by running the call against mainnet and observing the failure.

The same file transmitted updated_at: "now()" as a literal Python string on every ledger
upsert, which a timestamptz column rejects on insert.

All of this is resolved. 

The three copies are gone: one deleted alongside Flare_Trial.py\, one renamed to
flare_ftso_legacy.py to eliminate a case-insensitive filename collision that was 
fatal on Linux and silent on Windows, and the inline copy removed when solo_rider_flare.py 
became solo_rider.py. The "now()" string is now datetime.now(timezone.utc).isoformat().
A single canonical reader replaced all three.

Built for this event

All new work resides in Prv1311/flare/. Nothing outside that directory was modified except 
six additive, default-preserving parameters on rider_team.py, detailed below.

ftso.py — the canonical FTSOv2 reader. A fixed known-good universe (establish_coverage()) 
combined with bisection (_call_batch) handles both discovery of which symbols carry live 
feeds and self-healing when a previously valid feed ceases to resolve mid-session.

It never falls back to getFeedById to probe a single symbol; a batch of size one is 
still getFeedsById. Confirmed 16 of 20 candidate symbols carry live mainnet feeds 
(absent: COTI, EUL, KAITO, LDO) — broader coverage than anticipated at the outset.
price_adapter.py — sole owner of the confirmed 16-symbol universe (FLARE_UNIVERSE) and 
get_live_price(). Established once at import, so every module shares a single coverage
determination rather than deriving its own.
divergence.py — one-shot FTSO-versus-venue spread measurement. 

Reads all 16 symbols from Coinbase in a single bulk fetch_tickers() call rather than
16 separate calls: sixteenfold less venue traffic, and it narrows the oracle/venue 
timestamp gap by aligning one venue snapshot against one oracle batch.
divergence_recorder.py — the continuous form. Every 60 seconds, one row per symbol to 
Supabase oracle_divergence carrying spread in basis points, both raw timestamps, and a 
normalized timestamp_gap_ms. 

This is the dataset the finding rests on, not a debug log. 

Two venue calls per minute in total. rider_flare.py 
The FTSO-priced Rider twin. Identical gate logic to rider_team.py, imported rather than 
copied, fixed to the 16-symbol universe and priced off FTSO for entry and exit. 
Separate ledger, separate state table, decision rows explicitly tagged fleet='rider_flare'.
decision_hash.py — canonical serialization of a decision row, ensuring the same row hashes
identically regardless of when or where it is re-fetched. Explicit field order pinned from
the live schema, fixed 10-decimal float formatting, nulls preserved as null.
contracts/DivergenceAnchor.sol, deploy_anchor.py, and anchor_writer.py — the on-chain
component, described below.
onchain_swaps.py, onchain_divergence.py, onchain_divergence_recorder.py —
read-only measurement of on-chain DEX prices against off-chain sources, writing to a 
dedicated table. Not wired into any trading path.
coingecko_adapter.py — provider-agnostic historical-data adapter serving the 90-day floor and rolling high.

The contract

DivergenceAnchor is live on Flare mainnet at 0x086b912dD8aD5639c5adFD57bF8724B485786eDC. 
The Coston2 deployment shares that address through deterministic addressing, so the chain 
should be confirmed before reading its history.

The governing principle: the contract reads FTSOv2 itself, on-chain. The oracle value is never
a function argument. Were the price read in Python and written in, the result would be a database
storing my own assertion about what the oracle reported. Instead the contract fetches the feed,
normalizes both sides to 18 decimals, computes divergence in basis points on-chain, and emits it.

Alongside each reading, it stores a decisionHash — the keccak256 of that cycle's decision row,
committed before the outcome existed. Reveal the row afterward and any party recomputes the hash. 
That property is the substance of the work.

Scope of what is Flare-priced

Only the value used for entry and exit decisions originates from FTSO. Daily candles feeding
the regime and anomaly gates, the 90-day floor, the rolling 7-day high, order-book imbalance,
and order flow continue to read a centralized venue. Flare exposes no OHLCV history endpoint,
no order book, and no trade tape, so fully Flare-priced equivalents of those signals cannot 
presently be constructed. The rider_flare.py docstring states this directly; it is not 
concealed behind the module name.

Ported/integrated

rider_flare.py does not duplicate the rider_team.py gate loop;
duplication is precisely how the three original FTSO readers drifted apart. 
Instead, run_cycle() and run_engine() gained six default-preserving parameters. 
The live PRV1311-RiderTeam service calls each at its default, leaving its behavior unchanged.

(table unchanged)

Everything else — screener, anomaly_gate, footprint_gate, supabase_client, rider_decision_log, 
orderbook_imbalance, regime — is imported unmodified from the hardened shared modules.

Running

From Prv1311/. Working directory matters; these are package-relative imports.

(code block unchanged)

All are registered as Windows Task Scheduler services (AtStartup, SYSTEM, RestartCount 999), 
with install scripts in Prv1311/. anchor_writer spends real FLR on every anchor it writes to mainnet;
it is gated behind a typed confirmation and mechanical per-run and per-day ceilings, and refuses any chain other than 14.

## Roadmap

**Per-user access credentials.** The site currently sits behind a single
shared access code. That becomes one credential per user: machine-generated,
issued in sequence, and recorded in a sealed vault. Error logs surface the
user number only — never the credential — so a fault can be traced to an
account without the credential ever appearing in a log, a stack trace, or a
support ticket. Records live in the vault; nothing readable leaves it.

**The Voyeur.** A single-purpose sentinel stationed at each data gate,
inspecting every inbound payload for hidden prompt injection. One job, one
gate, nothing else — no routing, no decisions, no trading logic. A system
that reads market state autonomously has to assume its inputs are hostile,
and a component that only watches is a component that can be reasoned about
completely.

**Pattern Recognition fleet.** One instance first, tuned against a specific
asset set tracked for over eighteen months, then the tuned logic duplicated
across additional instances. 

These are semi-liquid names — deep enough to
enter and exit without moving price at this size, thin enough that most
systems ignore them. Position sizing is capped at $100–500, $750 at the
outside, which is what makes them tradeable at all.

This shares the tactical
layer's logic, not the accumulation core's: the core assumes a materially
larger starting balance.

**Security hardening.** The same method that produced the reading engine,
turned on the security layer — take existing architectures apart on their
own terms, keep the primitives that fit this system, and leave the rest.
Adopting a framework wholesale imports its assumptions along with its
protection.

**Execution on Flare.** At 3–5% targets, a ~0.6% round-trip venue fee
consumes a fifth of the move before slippage. Flare's costs are fractions of
a cent; the difference between a strategy that compounds and one that
donates. Measurement ships now; execution is the next build.

**FDC attestation of the venue price**, so both sides of the oracle-vs-venue
comparison are Flare-verified rather than one taken on trust.

**Provider-agnostic historical data.** The adapter already accepts alternate
sources; B3 Data API is the next planned integration.

**Dual-oracle consensus execution** for FLR and FXRP, weighing off-chain
against on-chain OHLC to inform entry and exit.

---

## Repository layout

The repository root holds the original PRV1311 system — the parent build this
grew out of, kept for lineage and continuing development. `Prv1311/` is the
codebase this submission is about, and `Prv1311/flare/` is all the
Flare-specific work.

Where a filename appears in both places the two have diverged. `Prv1311/` is
what runs live.
