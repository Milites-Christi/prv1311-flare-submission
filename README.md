# Flare hackathon build — Prv1311/flare/

Deadline: Aug 14. This directory is the entire scope of new work for the
event. Nothing outside it was modified except two small, additive,
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
of this week is the comparison data, not any one trade.

## Pre-hackathon state

Before this week, three independent, drifted copies of FTSOv2-reading logic
existed in the repo (root `flare_ftso.py`, targeting the dead Coston2
testnet; `Flare_Trial.py\Flare_ftso.py`, mainnet but stale/cached;
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

## Built this week

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
version of those signals from in a week. `rider_flare.py`'s own docstring
states this plainly; it is not hidden behind the name.

## Ported / integrated

`rider_flare.py` does not duplicate `rider_team.py`'s gate-walking loop —
duplication is exactly how the three original FTSO readers drifted apart
before this rebuild. Instead, `rider_team.run_cycle()` / `run_engine()`
gained five default-preserving parameters (zero behavior change for the
live `PRV1311-RiderTeam` service, which calls every one of them at its
default):

| Parameter | Default | Purpose |
|---|---|---|
| `price_fn` | `screener.fetch_live_price` | swap in `price_adapter.get_live_price` for FTSO pricing |
| `fleet` | `'rider'` | explicit tag on every decision/cycle row — **mandatory**, not left to the DB column default; a silently-missing fleet tag would land `rider_flare`'s rows as `'rider'` with no error and contaminate the whole week's A/B comparison |
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

## Repository layout

This repository contains two related codebases.

`/` (root) — the original PRV1311 system. The parent build this
project grew out of, retained here for lineage and continuing
development.

`/Prv1311/` — the codebase for this hackathon submission, including
`Prv1311/flare/`, which holds all Flare-specific work: FTSO
integration, the DivergenceAnchor contract, the on-chain divergence
recorder, and decision-hash canonicalization.

Where a filename appears in both locations the two versions have
diverged; `/Prv1311/` is the code that runs the live system and the
code this submission is about.
