# Go-Live Authority Decision Memo

Prepared as design input for a go-live decision not yet made. Nothing in
this document has been built — this is analysis, not a plan already in
motion. Scope: how a multi-user Solo-Rider gets standing authority to trade
on a user's behalf while that user is asleep, and what venue-desync risk
that authority model creates given the current codebase's gates all read
Coinbase.

**Recommendation, stated up front:** Option 2 (trade-only exchange API
keys, withdrawal disabled). Reasoning and what would change it are at the
end of this document.

---

## The actual gate to going live: execution doesn't exist yet

This belongs at the top, not buried in an options comparison, because it's
true regardless of which authority model gets chosen: **`rider_team.py`
has zero real order-execution code today, in any form.** Every position
open and close is simulated. `open_rider()` does `s['USD_balance'] -= usd`
and computes `units` by dividing cash by the current price — pure
in-memory arithmetic against a JSON ledger, never a call to any exchange's
order endpoint. `close_rider()` is the same, in reverse. This is not a
detail inside Option 1 or Option 2's cost estimate — it's a dependency
underneath both of them. Neither authority model matters until this layer
exists.

What has to be built, estimated against this codebase specifically:

1. **Real order placement.** Replace `open_rider`/`close_rider`'s
   arithmetic with actual `exchange.create_order(...)` calls via `ccxt`
   (already proven in this codebase for reads, through `screener.py`).
   The call itself is small. What it breaks is the assumption every other
   item below depends on: that a trade completes synchronously, in the
   same cycle it was requested.

2. **Fill confirmation.** A placed order isn't necessarily filled
   immediately — this codebase currently assumes instant, complete
   execution at the requested price, because that's true of arithmetic
   but not of markets. Needs polling or a fill-event stream
   (`fetch_order`/order-update websocket) and a real "pending" state that
   `run_cycle()` has no concept of today.

3. **Partial-fill handling.** `s['riders'][sym]` assumes one clean entry
   at one price for the full bucket, full stop. A market order can
   partially fill in thin conditions; a limit order can sit open,
   partially filled, indefinitely. There is no partial-position concept
   anywhere in the current state shape — this is new state-machine logic
   with no existing analog to extend.

4. **Rejection handling.** An order can be rejected — insufficient
   balance, invalid size, a halted market, a transient API error. Today
   there is no failure path for "the trade attempt itself failed" as
   distinct from "a gate blocked it" — those are different failure
   classes and the decision log currently only knows the second one.

5. **Reconciliation between what the engine believes it holds and what
   the venue says it holds.** Right now the paper ledger IS the only
   truth that exists, by construction — there's nothing external to check
   it against. Once real orders exist, the engine's local state can drift
   from the exchange's actual account state, especially after any crash
   or restart, and nothing today checks for that drift, ever.

6. **What happens to an open position when the process dies mid-trade.**
   Today, a crash mid-cycle costs a missed paper-trade opportunity —
   genuinely nothing, since nothing real was ever committed. With real
   execution, a crash between "order sent" and "fill recorded" leaves a
   real, unrecorded position (or a real unfilled order) sitting on the
   exchange that the engine has no idea exists until reconciliation (item
   5) catches it — if it's built to catch it at all. This is the single
   highest-consequence gap in the list; it needs deliberately tested
   crash-recovery behavior, not an assumption that it won't happen.

These six items are tightly coupled — reconciliation depends on knowing
what a partial fill looks like, crash recovery depends on reconciliation
running correctly on every restart — and collectively they're a larger,
more foundational body of work than either authority model's own cost
estimate below. Both options *assume* this layer exists or gets built
alongside them; neither option's cost estimate in this memo includes it.
Read the authority comparison below with that dependency in mind.

---

## Why standing authority is needed at all

A connected wallet can authenticate a user; it cannot sign a transaction
while its owner is asleep. An engine that needs a per-trade signature from
a human is a signal service, not an autonomous system. Some form of
standing, scoped authority is required for the engine to act on its own
between signature events — the three shapes below are the realistic
options.

---

## Option 1 — Delegated on-chain authority (vault + scoped delegate)

A user deposits into a vault (a Solana PDA, or an EVM smart account with a
scoped session key). The engine holds a delegate that can trigger trades
against the vault's assets but cannot direct funds anywhere else.

**Standing authority held / provably cannot do:** The engine holds a
trade-only delegate scoped at the contract level — it can call swap-shaped
instructions but has no code path to a withdraw-to-arbitrary-address
instruction. This is provable by reading the vault contract's code, not
just trusting a policy statement — real security here rests entirely on
that contract being correct, since the enforcement is the contract, not the
engine's intentions.

**Revocation:** The user revokes or rotates the delegate's authority
on-chain — one transaction, effective as soon as it confirms (seconds on
Solana; a handful of seconds to roughly a minute on most EVM L2s or Flare).
The user's assets never left the vault, so revocation doesn't require
moving funds, only cutting the delegate's authority.

**What breaks if the engine dies mid-position:** DEX swaps are atomic —
there is no half-executed on-chain state, and vault funds are never at
risk since the engine never had a withdrawal path in the first place. What
*can* break is the engine's own bookkeeping: if the process crashes between
deciding to trade and recording the result, its local ledger and the
Supabase decision log can drift out of sync with the vault's actual
on-chain balance. This requires a reconciliation step on every restart
(read real vault balance, compare against last-known ledger state, resolve
the difference) — not built today, and not optional for this option.

**Engineering cost against this codebase specifically:** The largest of
the three by a wide margin. `rider_team.py` today has **zero
order-execution code anywhere** — `open_rider()`/`close_rider()` do pure
in-memory arithmetic against a JSON ledger; there is no buy/sell call to
any venue at all, paper or otherwise. This option requires, from scratch:
a vault smart contract (ideally professionally audited before real capital
touches it), an execution adapter translating rider_team's BUY/SELL
decisions into real swap transactions against a specific DEX, per-user
wallet/session-key lifecycle management, and the reconciliation logic
above. This is not an extension of the existing engine — it's a
different-shaped project sitting next to it.

---

## Option 2 — Trade-only exchange API keys, withdrawal disabled

The user issues an API key on their own exchange account (e.g. Coinbase),
scoped to trading only, with withdrawal permission explicitly disabled at
the exchange's own permission system.

**Standing authority held / provably cannot do:** The engine can place and
cancel orders on the user's own exchange account. It cannot initiate a
withdrawal to any address — not because the engine chooses not to, but
because the exchange itself enforces that at the API-key permission level,
external to anything this codebase controls. Provable via the exchange's
own key-permission display, a real externally-enforced control rather than
an engine-side promise.

**Revocation:** The user disables or deletes the API key in their exchange
account settings directly. Takes effect within seconds to at most a couple
of minutes, depending on the exchange's key-cache propagation. Funds never
leave the user's own exchange account, so there is nothing to claw back —
revocation just stops future orders.

**What breaks if the engine dies mid-position:** If the engine places an
order and crashes before recording the fill, the real position lives on
the exchange (visible in the user's own order/account history) but the
engine's local ledger doesn't know about it until restart. Same
reconciliation-on-restart requirement as Option 1, but materially simpler
— it's one exchange's REST API via `ccxt` (already the library in use
throughout this codebase), not a new blockchain integration.

**Engineering cost against this codebase specifically:** Much smaller than
Option 1, and a natural extension rather than a new project. `screener.py`
already wraps `ccxt` behind a shared, rate-limited, cached exchange handle
that `rider_team.py` depends on for every price/candle/order-book read;
converting `open_rider()`/`close_rider()` from paper arithmetic to real
`exchange.create_order(...)` calls uses that same library, already proven
against this exact venue. New work: per-user encrypted API-key storage (a
real new security requirement — trade-only keys are still meaningful
credentials), per-user authenticated exchange client construction (today
there is exactly one shared `screener.exchange`, used for market data
only, never for placing an order under any specific user's credentials),
and the reconciliation-on-restart logic described above, scoped to one
REST API rather than on-chain state.

---

## Option 3 — Signals only

The engine holds no funds and no keys. It publishes what the gates decided;
a human, or a separately-authorized system, acts on it.

**Standing authority held / provably cannot do:** None, by construction —
there is nothing to hold, and therefore nothing to prove it can't do.

**Revocation:** Not applicable. There is no standing authority to revoke;
the user simply stops reading the signal.

**What breaks if the engine dies mid-position:** Nothing financially — a
signal not published is a reliability problem, not a funds-safety one,
because there is no real position for the engine to be tracking in the
first place.

**Engineering cost against this codebase specifically:** Smallest of the
three, effectively zero new engine work — the decision log
(`rider_decisions`, `log_decision`/`log_cycle`) **already is this
product**, functioning as designed since early in this engagement. What's
missing is entirely a delivery problem (a dashboard, an API, a
notification channel), not an engine problem.

Named honestly: this is the safest and cheapest option, and it is also
explicitly not what "autonomous system" means in the framing that opened
this document. Including it accurately, not as a straw man.

---

## The venue-desync problem (applies to Option 1, not Option 2)

Every gate in this system reads Coinbase — daily OHLCV for the 90-day
floor and the rolling 7-day high, order-book depth for the OBI gate, and
Coinbase's trades stream for the order-flow shield. Under Option 1, the
engine *decides* on Coinbase's tape but *executes* against a DEX's
liquidity — a different venue than the one every gate was calibrated
against. That's single-venue desync built directly into the execution
path, which is precisely the failure mode the Flare divergence work this
week exists to measure and expose. Under Option 2, this problem doesn't
exist at all — the deciding venue and the executing venue are the same
venue by construction.

Options, enumerated honestly, with cost:

**(a) Port every gate to DEX-native data.** Daily candles from an
on-chain/DEX data source, a liquidity-depth analog in place of order-book
imbalance (AMM pools don't have an order book — "depth" becomes
price-impact-per-size, a materially different metric, not a drop-in
replacement), and a trade-tape equivalent from on-chain swap events.
**Cost: high.** This isn't a port, it's rebuilding three to four gates
against a fundamentally different data shape, plus hardening a brand-new
data dependency (rate limits, reliability, caching) to the same standard
the Coinbase side already required weeks of work to reach.

**(b) Restrict to assets where the two venues track tightly.** Use
evidence, not assumption — the divergence-measurement infrastructure built
this week is exactly the right tool to decide which assets qualify, and it
already produced a real counterexample: OP held a persistent ~20bps
divergence across all three measurement runs, while most other symbols
were noise-level. **Cost: low** — closer to a universe restriction
(`universe_fn` already supports this) than new code. Honest tradeoff: this
doesn't fix the desync, it scopes the strategy to where the desync is
small enough not to matter, and "tracks tightly today" isn't a permanent
guarantee — it needs ongoing monitoring, not a one-time check.

**(c) Disclose the gap plainly.** Ship Option 1 as-is, state the
single-venue-desync risk explicitly in the product itself. **Cost: low**
(documentation only), but it pushes real risk onto the end user with no
mitigation beyond awareness. Worth naming directly: shipping this alone,
while sitting on the exact measurement work built this week to expose this
class of risk, would be a real inconsistency — not a neutral choice.

**(d) A targeted pre-execution check, not a full gate replacement.** Before
executing a DEX trade the Coinbase-based gates already approved, run one
cheap real-time check against the DEX's own current price/liquidity
(a quote or simulated swap) and abort or resize if it's too far from
Coinbase's read — directly reusing this week's own divergence-measurement
pattern and thresholds. **Cost: medium**, well below (a) since it adds one
check rather than rebuilding four gates, and it targets the failure mode
that actually costs money (executing into thin or mispriced DEX liquidity)
even though it doesn't make the entry *decision* itself DEX-native.

No single option above is being pushed as the answer to this sub-problem —
they're real, costed alternatives. (b) and (d) together are the cheapest
combination that meaningfully reduces the risk without a multi-week
rebuild, if Option 1 is ever chosen.

---

## Money math: float vs. Decimal (flagged, not started)

`rider_team.py` uses plain floats throughout — `open_rider`, `close_rider`,
`deployable_cash`, `compute_sizing`, the ledger's JSON serialization. This
stays untouched here, per instruction; this section documents the tradeoff
for the record, not as a task in progress.

**What float buys today:** simplicity, zero migration risk to the
currently-live paper-trading service, and no custom JSON
encode/decode step (`Decimal` isn't natively JSON-serializable — the
previous version of this file (then named `solo_rider_flare.py`, since
renamed to `solo_rider.py` once the Flare-pricing angle was dropped) had
already solved this once,
with its own `money()` helper, before this rewrite removed it along with
everything else that duplicated logic rather than importing it).

**What float risks:** floating-point rounding error accumulates across
many sequential operations. For paper trading this is cosmetic. Once real
capital is split three ways every trade — user balance, treasury, fee
wallet — small accumulated drift becomes a real, auditable discrepancy
between what the ledger says and what actually happened, and it's exactly
the kind of thing a user (or a regulator) checks first.

**Cost of migrating:** touches every money-handling line in
`rider_team.py`, plus everything downstream that reads or writes the
ledger — dashboards, `sync_supabase`'s JSON serialization,
`config.py`'s numeric constants. Because `rider_team.py` is the one place
this logic lives — `flare/rider_flare.py` and `solo_rider.py`
both import it rather than duplicating it — the
migration happens exactly once and everything downstream inherits it
correctly. That's a genuine advantage of the import-don't-copy shape
already established this engagement: the cost is real, but it doesn't
multiply per caller.

**Flagged as required before real capital moves under Option 1 or Option
2.** Not required for Option 3 (no money moves) or for continued paper
trading.

---

## Recommendation

**Option 2 — trade-only exchange API keys, withdrawal disabled.**

Reasoning: `rider_team.py` has no execution code today in any form, so
every option requires building something new — but Option 2 builds on the
exact library (`ccxt`, via `screener.py`) already deeply embedded
throughout this codebase, against a venue (Coinbase) the gates are already
calibrated against. It has no venue-desync problem by construction. Its
revocation is real, user-controlled, and externally enforced by the
exchange rather than by a smart contract this team would need to write and
get audited. Option 1 is not wrong, but it's a different, larger project
sitting next to this one, not a natural next step from it.

**What would change this recommendation:**

- If the target users specifically want DEX-only assets Coinbase never
  lists — Option 2 is fundamentally bound to what the user's exchange
  account can reach. That would push toward Option 1 despite its cost,
  since it's the only option that reaches those assets at all.
- If an audit budget and timeline become available for a vault contract,
  and the design reuses an already-audited session-key/smart-account
  standard rather than a bespoke contract — Option 1's cost objection
  weakens considerably.
- If the Flare divergence work (or a DEX equivalent) shows CEX/DEX
  tracking is tight and stable for the specific assets in scope, (b) and
  (d) above become cheap enough that Option 1's venue-desync objection
  substantially weakens too.
- **Now checked, not left unverified — with one honest limit on how it was
  checked.** Coinbase's User Agreement states: *"By using a Coinbase
  Account, you agree and represent that you will use the Coinbase
  Services only for yourself, and not on behalf of any third party,
  unless you have obtained prior approval from Coinbase in accordance
  with Section 3.2 and 4.11 of this Agreement."* Every direct fetch
  attempt against `coinbase.com/legal/*` (multiple URL variants, plus a
  PDF) returned 403 — this quote is corroborated consistently across
  independent secondary sources citing the same section numbers, but was
  not independently confirmed against the primary document directly.
  Reasoned interpretation, clearly separated from the quote itself:
  Coinbase's own "Learn" content documents connecting third-party
  automated trading platforms (3Commas, Bitsgap, Cryptohopper) via
  user-issued API keys as an expected, supported use case — this looks
  like the same shape Option 2 proposes (each user issues their own
  trade-only key; the software never becomes the account holder). The
  clause's "on behalf of any third party" language most plausibly targets
  someone else operating *your* account as if they were you, not you
  authorizing software to trade *your own* account — but that's an
  inference, not a citation, and the exact language that leaves it
  genuinely ambiguous is "on behalf of any third party" itself: it's
  never defined precisely enough in what I found to say for certain
  whether automated software acting on a user's own self-issued,
  trade-only key counts as "on behalf of" that user (fine) or "on behalf
  of" a third-party operator (requires approval). No account-tier
  threshold specific to this clause turned up in the search. Coinbase
  Prime (institutional custody/trading) exists as a formal partner path,
  found via an SEC filing reference, but it's built for pooled/custodial
  multi-client trading — not needed for Option 2 as designed, since funds
  never leave each user's own account. **Before this recommendation is
  treated as fully settled, get a direct answer from Coinbase** (support
  or partnerships) rather than resting on inference — this narrowed the
  question to one specific ambiguous phrase, it didn't close it.

- **Fee reality check against this codebase's existing assumptions.**
  Current Advanced Trade retail pricing (lowest tier, $0–$10K/30-day
  volume, corroborated across several independent sources but not
  fetched from Coinbase's own fee page directly — same 403 pattern):
  **0.40% maker / 0.60% taker**, scaling down at higher volume.
  `config.py` currently hardcodes `TAKER_FEE_PCT=1.2` and
  `MAKER_FEE_PCT=0.6` — the configured taker fee is roughly double the
  real current retail rate, and maker is close but not exact. Worth
  reconciling those constants against a verified current source before
  Option 2 goes anywhere near real capital; running a fee model that's
  miscalibrated against the venue it's supposed to represent undermines
  the exact "auditable discrepancy" concern raised in the money-math note
  above.
