# Flare hackathon build — Prv1311/flare/

This directory holds the Flare-specific work: FTSOv2 integration, the
on-chain divergence measurement, and the FTSO-priced paper-trading engine.
Nothing outside it was modified except two small, additive,
default-preserving parameters added to `rider_team.py` (see "Ported /
integrated" below) — every other file in this repo is untouched.

## What this is

An autonomous market-state reader for Flare's FTSOv2 oracle: it measures
whether FTSO's decentralized price feed diverges from a centralized venue
(Coinbase) for the same asset, and runs a real (paper) trading strategy
priced entirely off FTSO instead of Coinbase, side by side with the
Coinbase-priced original, so the divergence isn't just measured — it's
tested against an actual trading outcome.

This is a market reader, not a trading bot pitch. The interesting artifact
here is the comparison data, not any one trade.

## Pre-hackathon state

Before this build, three independent, drifted copies of FTSOv2-reading
logic existed in the repo (root `flare_ftso.py`, targeting the dead
Coston2 testnet; `Flare_Trial.py\Flare_ftso.py`, mainnet but stale/cached;
and an inline copy inside the file then named `solo_rider_flare.py`). None
of them handled the one behavior that actually matters on live mainnet:
`getFeedsById` reverts the *entire batch* if even one requested feed ID
doesn't exist — it does not return a zero value for the bad one. All three
would have frozen every tracked symbol's price the first time a bad symbol
was mixed into a batch.

That file also sent `updated_at: "now()"` as a literal Python string on
every ledger upsert — a `timestamptz` column would reject that on insert.
Fixed since (`datetime.now(timezone.utc).isoformat()`, not a column-type
change) as part of the Day 3+ go-live rewrite, which also renamed it to
`solo_rider.py` (Coinbase-priced, no Flare path) and deleted
`Flare_Trial.py\` entirely — both `root flare_ftso.py` and the deleted
`Flare_Trial.py\Flare_ftso.py` are gone; `root flare_ftso.py` was renamed
to `flare_ftso_legacy.py` to remove a case-insensitive filename collision
with the sibling that imported it, which was fatal on Linux, harmless on
Windows.

## What was built

- **`ftso.py`** — the one canonical FTSOv2 reader. Fixed known-good universe
  (`establish_coverage()`) + bisection (`_call_batch`) as the single
  mechanism for both discovering which symbols have live feeds and
  self-healing if a previously-good feed stops resolving mid-session. Never
  falls back to `getFeedById` to probe one symbol at a time — a batch of
  size 1 is still `getFeedsById`. Empirically confirmed 16 of 20 candidate
  symbols have live mainnet feeds (missing: `COTI`, `EUL`, `KAITO`, `LDO`) —
  materially more coverage than expected going in.
- **`price_adapter.py`** — single owner of the 16-symbol confirmed universe
  (`FLARE_UNIVERSE`) and `get_live_price()`, the one function anything
  wanting an FTSO-sourced price should call. Established once at import;
  every other module in this package shares that one coverage answer.
- **`divergence.py`** — one-shot FTSO-vs-Coinbase spread measurement,
  reads the whole 16-symbol universe from Coinbase in a single bulk
  `fetch_tickers()` call rather than 16 separate `fetch_ticker()` calls
  (16x less venue traffic, and it tightens the oracle/venue timestamp gap
  since one venue snapshot lines up against one oracle batch).
- **`divergence_recorder.py`** — the continuous version of the above:
  every 60s, records one row per symbol to Supabase `oracle_divergence`
  (spread in bps, both raw timestamps, and a normalized `timestamp_gap_ms`).
  This is the actual dataset the hackathon's finding rests on, not a
  debug log. Two Coinbase calls/minute total.
- **`rider_flare.py`** — the FTSO-priced Rider twin. Same entry/exit gate
  logic as `rider_team.py` (imported, not copied), fixed to the 16-symbol
  confirmed universe, priced off FTSO instead of Coinbase for entry/exit
  decisions. Separate ledger (`data/rider_flare_ledger.json`), separate
  Supabase state table (`rider_flare_state`), decision/cycle rows explicitly
  tagged `fleet='rider_flare'` (not relying on any DB column default —
  see "Ported / integrated" for why that mattered enough to be mandatory).

### Honest scope of what's actually Flare-priced

Only the number used for entry/exit decisions comes from FTSO. The daily
candles feeding the regime gate and the anomaly (blow-off-pump) gate, the
90-day floor, the rolling 7-day high, order-book imbalance, and order-flow
all still read Coinbase. Flare has no OHLCV history endpoint, no order
book, and no trade tape — there's nothing to build a fully-Flare-priced
version of those signals from with the data sources available today.
`rider_flare.py`'s own docstring states this plainly; it is not hidden
behind the name.

## Ported / integrated

`rider_flare.py` does not duplicate `rider_team.py`'s gate-walking loop —
duplication is exactly how the three original FTSO readers drifted apart
before this rebuild. Instead, `rider_team.run_cycle()` / `run_engine()`
gained six default-preserving parameters (zero behavior change for the
live `PRV1311-RiderTeam` service, which calls every one of them at its
default):

| Parameter | Default | Purpose |
|---|---|---|
| `price_fn` | `screener.fetch_live_price` | swap in `price_adapter.get_live_price` for FTSO pricing |
| `fleet` | `'rider'` | explicit tag on every decision/cycle row — **mandatory**, not left to the DB column default; a silently-missing fleet tag would land `rider_flare`'s rows as `'rider'` with no error and contaminate the whole A/B comparison |
| `ledger_file` | `RIDER_LEDGER_FILE` | separate `data/rider_flare_ledger.json` |
| `state_table` | `'rider_state'` | separate `rider_flare_state` Supabase table |
| `universe_fn` | `None` → live market scan | fixed 16-symbol FTSO universe instead of the broad Coinbase scan |
| `log_name` | `'rider_team'` | separate `logs/rider_flare.log` — two services sharing one rotating log file risks a `PermissionError` on Windows during log rollover |

Everything else — `screener`, `anomaly_gate`, `footprint_gate`,
`supabase_client`, `rider_decision_log`, `orderbook_imbalance`, `regime` —
is imported directly from the hardened shared modules, unmodified.

## Running

From `Prv1311/` (working directory matters — these are package-relative
imports):

```
python -m flare.divergence            # one-shot spread report, all 16 symbols
python -m flare.divergence_recorder    # continuous recorder -> oracle_divergence
python -m flare.rider_flare            # FTSO-priced Rider twin -> rider_flare_ledger.json
```

`divergence_recorder.py` and `rider_flare.py` are registered as Windows
Task Scheduler services (`PRV1311-DivergenceRecorder`,
`PRV1311-RiderTeamFlare`) — AtStartup, SYSTEM, `RestartCount 999`, same
shape as the existing `PRV1311-RiderTeam` / `PRV1311-FootprintWorker`
services. Install scripts: `install_divergence_recorder_task.ps1`,
`install_rider_flare_task.ps1` (repo root of `Prv1311/`).

## Tick-size mechanism

Divergence magnitude between FTSO and the venue is not random noise — it's
substantially explained by the venue's quote tick size relative to price.
Coinbase can only express discrete price levels on a given tick; the
oracle drifts continuously underneath that, so apparent divergence tracks
how coarse the venue's tick is at that asset's price point. Full
methodology, per-step evidence, and the two exceptions found along the way
are in `docs/CHANGELOG.md` under "Tick-size law verification" — this
section states the result, plainly, without overclaiming it:

- **Tick size relative to price predicts the *ranking* of divergence
  magnitude**: Pearson r = 0.9887, Spearman ρ = 0.965 (n=114 per symbol).
- **It is NOT a universal ceiling.** The half-tick bound binds as an
  absolute limit for OP only; for the other 15 symbols the bound is a
  fraction of a basis point, and read-timing plus real price movement
  dominate at that scale.
- **Convergent evidence:** an independent proxy for the same property,
  1/(distinct venue price levels), correlates with the same ranking at
  r = 0.99.
- **FLR fits** (half-tick bound 8.3 bps vs. observed 9.45 bps). FLR
  ranking high means the single venue prices it worst, which is what the
  mechanism predicts for the thinnest source.

**Real `quote_increment`** per symbol, pulled live from Coinbase's public
product endpoint (`https://api.exchange.coinbase.com/products/{PRODUCT_ID}`,
no auth), not assumed:

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

## A/B result: FTSO vs centralized venue

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

**Caveats — read before drawing any conclusion from the table above:**

- **The 84.7% vs 95.1% difference is NOT an oracle effect.** It is driven
  by `already_held`: the venue-priced engine has more capital deployed in
  these sixteen assets from a longer run. Excluding `already_held`, the
  two engines agree on 99.5% (venue) and 97.3% (oracle) of evaluations.
- **`floor_fetch_failed` (175) is CoinGecko rate-limiting on a separate
  90-day historical-data call, NOT an FTSO failure.** FTSO returned zero
  read failures across the window; the centralized venue returned twelve
  `price_fetch_failed`.
- **The oracle-priced engine logged MORE rows than the venue-priced
  engine despite a smaller universe.** The venue engine's team-full and
  cash-floor gates use `BREAK`, so once the team fills, later symbols in
  the list go unevaluated that cycle. This is a structural property of
  the parent engine, unrelated to data source.

**Roadmap:** the historical-data adapter is provider-agnostic by design;
B3 Data API integration is the next planned source, alongside FDC
attestation of the venue price.
