# PRV1311 — Changelog

A running record of what was fixed, changed, and added during the build. Newest first.

*A note on dates: entries from 2026-08-10 onward come from direct knowledge of the work.
Entries before that are reconstructed from file save timestamps still on disk and one
confirmed database timestamp, not from git commit history — the repository's git history
was intentionally squashed to a single clean commit on 2026-08-10 as part of a
credential-security pass before the code went public, so day-by-day commit history
doesn't exist for 2026-08-06 through 2026-08-09. Where a specific time can't be pinned
down precisely, that's noted inline rather than guessed.*

---

## Evidence of new work

A commit log can be backdated. Rows written continuously by a running process can't.
Everything below is a live query result — machine-written operational records, not
curated for this document — showing this system has been running and producing data
since early in the build window, independent of any commit history.

| Source | First record | Most recent | Count | Notes |
| --- | --- | --- | --- | --- |
| `rider_health` | 2026-08-07 04:21:29 UTC | still writing | 1,713 | 8 components reporting; see breakdown below |
| `rider_decisions` | 2026-08-07 07:57:26 UTC | 2026-08-11 01:22:04 UTC | 31,175 | rider 22,904 · scav 3,325 · rider_flare 4,160 · solo_rider 786 |
| `rider_cycles` | 2026-08-07 07:57:25 UTC | 2026-08-11 01:22:04 UTC | 1,795 | rider 314 · scav 436 · rider_flare 259 · solo_rider 786 |
| `oracle_divergence` | 2026-08-10 02:20:45 UTC | 2026-08-11 01:22:03 UTC | 12,192 | Flare oracle vs. live exchange price, one row per symbol per ~60s cycle |
| `footprint_nodes` | 2026-08-05 13:50:00 UTC (bucket end) | 2026-08-11 01:00:00 UTC (bucket end) | 2,283 | order-flow data, oldest-running collector in the system |
| `data/rider_ledger.json` trade history | 2026-08-01 14:42:34 | 2026-08-07 09:05:34 | 22 trades | local paper-trading ledger, not a database table |
| `data/scav_ledger.json` trade history | 2026-08-04 02:52:27 | 2026-08-09 21:23:21 | 11 trades | local paper-trading ledger, not a database table |

**`rider_health` first report per component** (8 total): `footprint_worker` 08-07 04:21:29 · `rider_engine` 08-07 08:29:13 · `universe_fetch` 08-07 21:37:23 (first row is a `DEGRADED` report — this component doesn't emit a startup row at all, only reports when it falls back) · `run_all` 08-07 16:53:01 · `divergence_recorder` 08-07 23:19:43 · `rider_flare_engine` 08-07 23:19:51 · `solo_rider_service` 08-10 01:50:19 · `solo_rider_engine_unknown` 08-10 01:50:25 (first row is `CYCLE_PAPER`, not a startup report — this component only reports per-cycle).

---

## 2026-08-11

### Fixed
- Anchor writer service registered successfully but exited immediately on every run.
  Root cause: the scheduled task invoked the script by file path, which puts the
  script's own subdirectory on the import path instead of the project root, so every
  internal import failed before logging started. Now invoked as a module with the
  working directory pinned to the project root.
- The failure was invisible to our health monitoring because the process died before
  the logger initialized. Added a startup guard that reports the reason and exits with
  a distinct code, so the same class of failure is now visible from the task's exit
  code alone.
- A verification step during this fix accidentally ran one extra live anchoring cycle
  outside the planned schedule (an unrecognized command-line flag fell through to the
  live service instead of being rejected, the same invocation gap as the previous bug).
  Caught within the same run and stopped; the cycle itself completed cleanly — 5
  transactions, all decisionHash-verified against the chain, no ledger drift. Closed
  by rejecting any unrecognized argument outright rather than silently starting the
  service, so a mistyped flag can no longer trigger a live run.

### Changed
- Anchor cadence raised from 12h to 8h for more on-chain samples before judging; daily
  spend cap and minimum wallet balance adjusted to match.

---

## 2026-08-10

### Fixed
- Solo Rider page crashed on render: the Active Riders card referenced a `riders` variable that no longer existed after the page was migrated to the Supabase engine mirror. Page appeared blank/gone. (Base44: `src/pages/SoloRider.jsx`)
- Order form advertised limits that didn't match either backend — said $999 max and 3 active riders; actual is $25–$1000 and one active position.

### Changed
- Base44's order-placement function no longer writes execution state directly. It validates the asset and submits a pending order; the Python engine is now the single writer of all position state. Previously both systems could claim a position was open at the same time.
- Orders were being created already marked "filled," which made them invisible to the Python engine's pending-order check. Orders now start as "pending" like they're supposed to.
- Capital range aligned to $25–$1000 across the order-placement function, the order form, and the Python engine.
- Site navigation reorganized: Dashboard now holds CORE, Tactical Model, Dogs Lab, and Solo Rider. Research Lab holds data views only. The old "Live Engine" dropdown was retired and its four pages moved intact under Research Lab.

### Added
- A "reason for rejection" field on the order record, so a rejected order can tell the user why instead of failing silently.

### Disabled
- The in-sandbox order simulator that had been standing in for the real engine. It reset a position's entry price to the current price every time it hit its profit target, so it compounded indefinitely and could never register a loss. Replaced by the real Python engine, which has actual position state, actual exits, a real fee model, an entry tolerance band, and a gate that quarantines coins coming off artificial price spikes.

---

## 2026-08-09

### Fixed
- The Flare oracle price reader could lose its live data feed and never recover on its own, running blind indefinitely. It now checks in periodically and heals itself instead of requiring a manual restart.
- The price-divergence recorder (which compares the Flare oracle's price against a live exchange price) could fail silently for hours, writing zero rows with no alert anywhere. It now reports its status every cycle and automatically restarts itself after repeated failures.

### Added
- Solo Rider (single-asset, single-position paper trading) deployed as a background Windows service, matching the other automated engines.
- A "go-live authority" document laying out what real trade execution would require and who has to approve it before it ever happens.
- A full data-handoff document describing every database table, column, and freshness rule, written for the team building the public-facing site.

---

## 2026-08-08

*Dates for this day are approximate — reconstructed from a shared engine file's save timestamp, which has been touched many times since for unrelated work, so the exact time of day isn't reliable, only the calendar date.*

### Changed
- Trading fee model tightened so paper profits reflect the real exchange fee on both the buy and the sell, not just the raw price move.
- Entry logic no longer requires price to hit the exact dip target — a small tolerance band around it still counts, so a real entry isn't missed by a rounding hair.

### Added
- A public write-up for the Flare hackathon track documenting what was carried over from the earlier prototype versus newly built from scratch.

---

## 2026-08-07

### Added
- A per-cycle decision log: every candidate the engine looks at now gets a record explaining exactly why it did or didn't trade — not just the trades that actually fired.
- A volatility anomaly gate that quarantines a coin coming off an artificial price spike, so a fake dip right after a pump doesn't get mistaken for a real buying opportunity.
- The Rider engine and the Run-All orchestrator deployed as background Windows services.
- The Flare oracle price-reading module and the logic comparing the oracle's price against a live exchange price, for the Flare hackathon track.
- Rider Flare (the Flare-oracle-priced trading engine) and the smart contract that anchors divergence readings on the blockchain.
- Rider Flare and the price-divergence recorder deployed as background Windows services; the divergence recorder's first successful run was later that same night.

---

## 2026-08-06

### Fixed
- A network issue where database connections could silently fail on this machine because antivirus software was inspecting encrypted traffic; connections now trust the same security certificate the antivirus already trusts.

### Added
- An order-flow confirmation gate that checks recent trade activity before allowing an entry, giving a clear pass/fail verdict plus a plain-English reason.
- The Footprint Worker (the trade-flow data collector feeding that gate) deployed as a background Windows service.
