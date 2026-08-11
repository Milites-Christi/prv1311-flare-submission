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
