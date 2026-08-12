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

## 2026-08-12

### Summary

**Completed**
- **ETH anchor backfill.** Recovered a real on-chain anchor that mined but
  was never logged (`anchor_log`) — tx
  `0x7e3afb0f7d83678f16d339ed7b570639a49e6d61d257a1fb715945e3f7e9bf2a`,
  block `0x40141c6`, gas `153647`, `0.09987055` FLR. Every field re-derived
  independently from the chain (receipt, decoded event, recomputed
  `feed_id`) before inserting — not copied from what was handed over.
  Landed as `anchor_log.id=19`.
- **Fixed the daily-spend-cap double-count bug** in `flare/anchor_writer.py`.
  It was refusing new anchors early because it counted the current run's
  already-written spend twice (real incident: refused at `1.8365` FLR when
  true spend was `1.5615`). Fixed, then verified live: a full cycle spent
  `0.4751` FLR on top of a real `1.6613` prior total, landing at `2.1365` —
  counted once, not twice. **5/5 anchored, 0 skipped**, confirmed via fresh
  `anchor_log`/`rider_health` queries (ids 20-24).
- **Investigated scheduled-task exit code 78.** Boot time (`15:30:20` local)
  matches the reported failure almost exactly. Likely cause found: a live
  Flare-mainnet RPC call at Python import time (`flare.price_adapter`) that
  took 19.8 of 22.5 total import seconds even on a healthy network —
  plausible to fail in the fragile first seconds after boot. Could not
  recover the exact historical error: Task Scheduler's operational event
  log is disabled on this machine, and nothing captures stderr for a
  Task-Scheduler-launched process. **Not fully closed** — see open items.
- **Found all 8 scheduled services unregistered**, not just the one being
  debugged: FootprintWorker, RiderTeam, RiderTeamFlare, DivergenceRecorder,
  OnchainDivergenceRecorder, SoloRider, RunAll, AnchorWriter — confirmed
  zero `PRV1311-*` tasks in Task Scheduler. Built
  [`reregister_all_tasks.ps1`](../reregister_all_tasks.ps1): registers all 8
  in one pass, the 7 free ones first, AnchorWriter last behind a typed
  confirmation (`REGISTER ANCHOR WRITER`) since it auto-spends real FLR on
  registration.
- **Diagnosed the Scavengers fleet gap** (`rider_decisions.fleet='scav'`
  silent since `2026-08-10 01:23:21 UTC`). Not a bug: `rider_cycles` for
  `scav` is current and ticking every 10 minutes right now; every cycle
  halts on the first candidate with `halt_reason='team_full'` because all 3
  active slots are full (`SCAV_COUNT=4` minus `SCAV_RESERVE_RIDERS=1`).
  3 real positions open (LDO, KAITO, COTI, \$500 each) waiting for
  `SCAV_TARGET_PCT=3.0`% — per the never-cut rule, none exits early. No
  code changed.
- **Website: fixed the Solo Rider "Waiting for Entry" card** to show real
  distance-from-high data instead of a bare status with no context (Base44
  frontend — reported, not independently checked from this session; no
  visibility into that codebase from here).

**Still open**
- **`reregister_all_tasks.ps1` is built but not yet run.** Directly
  reconfirmed just now: `Get-ScheduledTask` shows zero `PRV1311-*` tasks,
  no `anchor_writer` process running. An earlier report that all 8 were
  "re-registered and verified" did not hold up against this check — logged
  here as still-open, not completed, until it's actually run and verified.
- **`tp_pct`** (user-chosen take-profit target on the Solo Rider order form)
  is not read by the trading logic. Decide: drop the field from the form,
  or make the engine honor it. Not independently checked from this session
  (Base44 frontend + order logic).
- **Exit 78's exact historical cause is unconfirmed** — a plausible
  mechanism was traced (see above), not proven. Treat as a watch item for
  judging week, not resolved.

### Fixed — `push_ledger()`/`push_dogs()`/`push_scavengers()`/`push_core()`/`push_markov()` had stopped syncing

`rider_state`, `dog_state`, and `scav_state` were frozen at their
`updated_at` since 2026-08-04 — over a week — while `rider_decisions`/
`rider_cycles` kept updating live for every fleet. Two distinct, unrelated
root causes, found by checking in the order asked rather than guessing:

- **`rider_state` was a false alarm, not an outage.** Directly confirmed the
  live push was succeeding: `logs/rider_team.log` shows `[SUPABASE] ledger
  pushed` lines every ~15 minutes, most recently seconds before this check,
  and the pushed `USD_balance` matched the log's own live total exactly.
  The real defect was narrower: `push_ledger()`'s upsert never set
  `updated_at` explicitly, so the column only ever reflected the row's
  original creation — already flagged as unreliable in
  `docs/SITE_HANDOFF.md` ("do not build a staleness indicator on this
  column"). Data was fine the whole time; the freshness signal itself was
  broken.
- **`dog_state`/`scav_state` (and, not asked about but found the identical
  way: `core_state`/`markov_state`) were genuinely stale — real bug.**
  `push_dogs()`/`push_scavengers()`/`push_core()`/`push_markov()` all live
  only inside each module's own `run_engine()` (the standalone-service
  infinite-loop wrapper), never inside `run_cycle()` itself. `run_all.py`
  drives SCAV/DOGS/CORE/MARKOV by calling `run_cycle()` directly through its
  own generic `worker()` loop — it never goes through `run_engine()`, so
  those four push calls were structurally unreachable the whole time SCAV/
  DOGS/CORE/MARKOV have run via `run_all.py`. Confirmed by comparing
  `scav_state`'s Supabase content (`USD_balance: 500.0`, stale) against the
  live local `data/scav_ledger.json` (`USD_balance: 581.09`, current) — the
  *content* was stale, not just the timestamp, ruling out a cosmetic
  explanation for these four. Not a swallowed exception either — calling
  the exact same upsert by hand succeeded instantly with zero errors.

**Fix:**
- `run_all.py`: `worker()` now takes an optional `push_fn`, called right
  after a successful `cycle()`. `JOBS` wires `push_scavengers`/`push_dogs`/
  `push_core`/`push_markov` in for SCAV/DOGS/CORE/MARKOV; `None` (no change)
  for EWMA/REGIME/CHEAP/OBI, which already push from inside their own
  `run_cycle()` and were never affected.
- `sync_supabase.py`: every `push_*` function now sets `updated_at`
  explicitly, returns `True`/`False` (existing callers that ignore the
  return value are unaffected), and reports every attempt to `rider_health`
  under a new `component='ledger_sync'` — exactly the "add a row so this
  can't go silently dark again" ask. Consecutive-failure counts are tracked
  per table, not globally, so one table's failure streak can't hide inside
  another's success.
- **Caught a second real bug while verifying the first fix, before shipping
  it**: the initial version printed a unicode checkmark inside the success
  path. On this machine's raw (non-UTF8) console, that `print()` itself
  raised `UnicodeEncodeError` — caught by the function's own `except`
  clause, which then reported the push as **failed** and logged a false
  `ledger_sync` ERROR row, even though the upsert had already committed
  (`rider_state.updated_at` was already fresh despite the "failed" report).
  Fixed by deciding success/failure from the upsert alone, before any
  print, and switching every message to plain ASCII — a print can no longer
  turn a real success into a reported failure. The false-positive ERROR row
  from the first attempt is still visible in `rider_health` (id `4671`),
  immediately followed by five real OK rows from the corrected version.

**Verified live** (2026-08-12T02:48 UTC), all five tables in one pass:
`rider_state` (`10794.19`), `scav_state` (`581.09`), `dog_state`
(`1521.28`), `core_state` (`14530.82`), `markov_state` (`2000.00`) — every
`updated_at` fresh (`02:48:26`-`02:48:28`), every value matching the real
local ledger content, five `ledger_sync` OK rows landed
(ids `4677`-`4681`).

**Not yet in effect for the live processes.** Python doesn't hot-reload —
the currently-running interactive `rider_team.py` and `run_all.py`
processes still hold the old code in memory and won't pick up this fix
until restarted. The verification above used a fresh process, which is why
it worked immediately; it was also a one-time real push, not an ongoing
one. Deliberately did not restart the live sessions to make this "stick"
automatically — that interrupts currently-running threads mid-cycle, and
that's Clay's call, not an unattended one.

### Investigated (Task Scheduler persistence gap)

Zero PRV1311-* scheduled tasks currently registered (confirmed again,
unchanged from the 2026-08-11 finding) while the engines are still running
interactively — no live outage, but no persistence either. Judging is Aug
15-21, so this is a real gap, not a cosmetic one. Checked before assuming
anything:

- **`LastBootUpTime`: 2026-08-11 15:30:20 local (19:30:20 UTC), unchanged
  from the last check.** No reboot has happened since — specifically, none
  after 22:45 UTC on 2026-08-11 (when AnchorWriter's task was last confirmed
  registered). Current time is already past UTC midnight into 2026-08-12.
  **This means the tasks going missing is NOT a recurrence of the Aug 10
  machine-sleep pattern** — that would show a boot time newer than 22:45
  UTC, and it doesn't. Something un-registered them without a reboot
  (consistent with the 2026-08-11 finding that an `uninstall_anchor_writer_
  task.ps1` exists and appears to have been run deliberately, likely to
  allow that day's extensive manual/foreground testing without the
  scheduled service double-spending concurrently).
- **Exactly 8 install scripts exist**, not assumed, not "seven":
  `install_footprint_worker_task.ps1`, `install_rider_team_task.ps1`,
  `install_rider_flare_task.ps1`, `install_divergence_recorder_task.ps1`,
  `install_onchain_divergence_recorder_task.ps1`, `install_solo_rider_task.ps1`,
  `install_run_all_task.ps1`, `install_anchor_writer_task.ps1` — registering
  `PRV1311-FootprintWorker`, `PRV1311-RiderTeam`, `PRV1311-RiderTeamFlare`,
  `PRV1311-DivergenceRecorder`, `PRV1311-OnchainDivergenceRecorder`,
  `PRV1311-SoloRider`, `PRV1311-RunAll`, and `PRV1311-AnchorWriter`
  respectively (task names confirmed by grepping each script's own
  `$TaskName`, not inferred from the filename).

### Added — `reregister_all_tasks.ps1`

One script that runs all 8 install scripts in sequence — the 7 free ones
first, `install_anchor_writer_task.ps1` last, gated behind an explicit typed
confirmation (`REGISTER ANCHOR WRITER`) since registering it auto-starts a
live mainnet cycle within seconds (up to 5 real transactions, capped at
`MAX_FLR_PER_DAY=1.8`/`MIN_WALLET_BALANCE=2.0`, both confirmed restored to
production values 2026-08-11). Must be run from an elevated PowerShell by a
human — this session still can't self-elevate. Verification of what it
actually produced (STARTUP rows / fresh table rows per component, not just
`LastTaskResult`) is pending Clay confirming he's run it.

### Investigated — SCAV gap in `rider_decisions` (NOT a bug, no fix applied)

Diagnosis only, as asked — reported before touching anything. Root cause
found and it isn't what the symptom suggested:

- `rider_decisions` for `fleet='scav'` really did stop at `2026-08-10
  01:23:21 UTC` — confirmed.
- **But `rider_cycles` for `fleet='scav'` is current as of right now**
  (`2026-08-12T01:22:25`, cadence exactly every 10 min, 176 rows in the last
  30h alone) — `scavengers.run_cycle()` is being invoked by `run_all.py`'s
  thread loop on schedule, right now, not crashing, not swallowed. This by
  itself rules out "SCAV stopped running" — it's running fine.
- **Every one of those 176+ cycles shows `symbols_evaluated: 0,
  halt_reason: 'team_full', halt_at_symbol: 'BTC/USD', riders_open: 3`.**
  `run_cycle()`'s entry loop checks `len(s['riders']) >= sizing['active_slots']`
  *before* incrementing `symbols_evaluated` or calling `log_decision()` for
  the first symbol — so when the team is full, the loop halts on the very
  first candidate, before anything gets logged. Zero `rider_decisions` rows
  isn't a failure to evaluate; it's that there was nothing to evaluate.
  `SCAV_COUNT=4`, `SCAV_RESERVE_RIDERS=1` → 3 active slots (config.py) —
  matches `riders_open: 3` exactly.
- **Confirmed via `data/scav_ledger.json`**: 3 riders open right now — LDO
  (entry \$0.299), KAITO (entry \$0.7804), COTI (entry \$0.01178), \$500
  each. None has closed since before the gap started. `SCAV_TARGET_PCT=3.0`
  — each needs a +3% move from its own entry to flip and free a slot; per
  this repo's "never sell at a loss" rule, none of the three will exit
  early regardless of how long they sit underwater or flat.
- **Corrected one premise in the original framing**: `rider_team.py` and
  `rider_flare.py` are NOT part of `run_all.py`'s process — `run_all.py`'s
  own JOBS list explicitly excludes RIDER (it runs as the separate
  `PRV1311-RiderTeam` service specifically to avoid two processes writing
  the same ledger/rows) and doesn't include rider_flare either (that's
  `PRV1311-RiderTeamFlare`, also separate). Only SCAV/DOGS/CORE/MARKOV/
  EWMA/REGIME/CHEAP/OBI run inside `run_all.py`. That rider and rider_flare
  kept producing data doesn't speak to `run_all.py`'s own health one way or
  the other — checked `run_all`'s own `rider_health` STARTUP/cycle activity
  directly instead, and it's healthy (restarted cleanly at the 2026-08-11
  boot, ticking normally since).
- **No component logs under any name other than the fleet** — checked the
  full distinct list of `rider_health` components (`anchor_writer`,
  `divergence_recorder`, `footprint_worker`, `onchain_divergence_recorder`,
  `rider_engine`, `rider_flare_engine`, `run_all`, `solo_rider_engine_unknown`,
  `solo_rider_service`) — there's no separate `scavengers`/`scav` entry;
  SCAV doesn't get its own per-component health row at all, only the shared
  `rider_cycles`/`rider_decisions` rows tagged `fleet='scav'`, which is why
  this was invisible until queried directly.

**Verdict: not a bug.** SCAV is running exactly as designed; it's just been
mechanically full for ~2 days because none of its 3 open positions has
hit +3% yet. No fix applied — nothing to fix. Worth deciding, separately,
whether `SCAV_COUNT`/`SCAV_RESERVE_RIDERS` should change, or whether this
is simply what "never cut" looks like when the market hasn't cooperated —
that's a strategy call, not a code fix.

### Summary — Dashboard/Dogs Lab/Performance showing stale or missing data

**Two separate root causes under one reported symptom.** Not one bug —
two, found by checking each fleet independently rather than assuming they
shared a cause.

- **`rider_state`: false alarm.** The underlying data was fine the whole
  time. The "last updated" timestamp was simply never being set on a
  successful push, so genuinely fresh data looked stale. Nothing was
  actually broken here.
- **`dog_state`/`scav_state`/`core_state`/`markov_state`: real bug.** All
  four fleets' database push calls were never actually being triggered by
  the process that runs them — not an error, not a crash, just a call that
  was never reached. All four tables had been frozen since **Aug 4**, with
  nothing anywhere reporting it.
- **Fix**: wired the missing push call into the correct place in the code
  that actually drives those four fleets, fixed the timestamp so it means
  what it says, and added a health-check log entry for every future push
  attempt — success or failure — so this can't go silently dark again.
- **Caught a second bug mid-verification**: a checkmark character in a
  success message crashed on this machine's console encoding. That crash
  got mistaken for a real failure, logging one false failure entry even
  though the actual database write had already succeeded. Fixed by never
  letting a print statement decide success or failure.
- **Verified live**: all 5 fleet tables (rider, dogs, scav, core, markov)
  confirmed fresh and matching their real current balances immediately
  after the fix — not assumed, checked directly against the database.

### Merged — local repo and GitHub's pre-existing unrelated history

Merged local `main` (9 commits) with `origin/main`'s pre-existing,
unrelated history (2 commits, created directly on GitHub) via
`--allow-unrelated-histories`, resolving the one real conflict
(`.gitignore`) by union of both sides rather than picking one — no
commits rewritten, squashed, or dropped on either side. No other files
were deleted or reconciled; the resulting root-vs-`Prv1311/` duplicate
filename list (23 names existing at both paths) is flagged for Clay's
manual review, not resolved here. Merge commit `dd83a2e`, pushed clean
to `origin/main`.

### Added — startup delay for the three services exposed to the exit-78 risk

Followed up on the exit-78 recommendation logged earlier instead of leaving
it as a dangling suggestion. Checked which scheduled entry points actually
import `flare.price_adapter` (the module whose live Flare-mainnet RPC call
at Python import time is the traced, unproven cause of the boot-time exit-78
incident) — only three, not all eight:

- `install_anchor_writer_task.ps1` (`flare.anchor_writer` → `flare.deploy_anchor`
  → `flare.price_adapter`)
- `install_divergence_recorder_task.ps1` (`flare.divergence_recorder` →
  `flare.price_adapter`)
- `install_rider_flare_task.ps1` (`flare.rider_flare` → `flare.price_adapter`)

The other five (FootprintWorker, RiderTeam, RunAll, SoloRider,
OnchainDivergenceRecorder) don't import it and aren't exposed to this
specific race — left untouched rather than applying the fix everywhere on
the theory that more caution can't hurt. Added `$Trigger.Delay = "PT2M"` to
each of the three, with a comment explaining exactly why. This does not fix
the underlying import-time network dependency — it avoids racing the
narrow post-boot window where it's most likely to fail. Syntax-verified via
`[System.Management.Automation.Language.Parser]::ParseFile` (parse only,
nothing executed/registered). Still requires Clay to actually run
`reregister_all_tasks.ps1` from an elevated session for this to take effect.

---

### Gate-parity audit

Read-only audit, no code changed. Scope: `Prv1311\` only (including `Prv1311\flare\`) —
the 19 same-named `.py` files at the repo root (`backtest.py`, `portfolio_state.py`,
`rider.py`, `scanner.py`, `pipeline.py`, `scorecard.py`, etc.) are the stale
Binance.US-era research lab, not live code, and none of them define any of the 15
gates below. **Stale-root check: clean — zero gate-defining files (`screener.py`,
`config.py`, `dynamic_rsi.py`, `taker_absorption.py`, `vwap_bands.py`,
`orderbook_imbalance.py`, `regime.py`, `confluence_gate.py`, `anomaly_gate.py`,
`flip_ewma_buyzone.py`, `flip_cheap_window.py`) exist at root at all.**

**Live entry path per fleet** (RUNNER RULE — a gate only counts if it runs on this path):

| Fleet | Driven by | `run_engine()` status |
| --- | --- | --- |
| `rider_team.py` | own Windows service → `run_engine()` → `run_cycle()` | live (it's the service loop) |
| `scavengers.py` | `run_all.py` → `run_cycle()` directly | dead path |
| `core.py` | `run_all.py` → `run_cycle()` directly | dead path |
| `markov.py` | `run_all.py` → `run_cycle(s)` directly | dead path |
| `dogs.py` | `run_all.py` → `run_cycle()` directly | dead path |
| `solo_rider.py` | own Windows service → `run_service()` → `rider_team.run_cycle()` per user config | n/a (never calls its own `run_engine()`) |
| `flare/rider_flare.py` | own Windows service → `rider_team.run_engine()` → `run_cycle()`, with `price_fn`=FTSO, `ohlcv_fn`=CoinGecko, `universe_fn`=fixed 16-symbol list | live (same service-loop shape as rider_team) |

Two lines flagged ambiguous before this audit are now resolved: `rider_team.py`
578–583 is the startup print banner inside `run_engine()` (`gates += " | regime..."`
etc.), not a second live gate check — the real evaluation is in `run_cycle()`.
`core.py` 338's `check_flow()` call has no `USE_FLOW_GATE` toggle guarding it — it's
unconditional, unlike the flagged version in `rider_team.py`/`scavengers.py` (see
matrix note below).

**Matrix** — WIRED / FLAGGED_OFF / DEAD_PATH / IMPORTED_UNUSED / MISSING:

| Gate | rider_team.py | scavengers.py | core.py | markov.py | dogs.py | solo_rider.py | rider_flare.py |
| --- | --- | --- | --- | --- | --- | --- | --- |
| VWAP bands | MISSING | MISSING | MISSING | MISSING | WIRED | MISSING | MISSING |
| Regime gate | WIRED | WIRED | MISSING | MISSING | WIRED | WIRED | WIRED |
| OBI gate | WIRED | WIRED | WIRED | MISSING | WIRED | WIRED | WIRED |
| Flow gate | WIRED (flagged) | WIRED (flagged) | WIRED (**unflagged**) | MISSING | MISSING | WIRED (flagged) | WIRED (flagged) |
| Confluence gate | MISSING | MISSING | MISSING | WIRED | MISSING | MISSING | MISSING |
| Anomaly gate | WIRED | WIRED | MISSING | MISSING | MISSING | WIRED | WIRED |
| Catch-band (MAX_DROP_PCT) | WIRED | WIRED | MISSING | MISSING | MISSING | WIRED | WIRED |
| Ticker sanity check | WIRED | WIRED | WIRED | MISSING | WIRED | WIRED | FLAGGED_OFF (replaced by FTSO) |
| Dynamic RSI | MISSING | MISSING | MISSING | MISSING | WIRED | MISSING | MISSING |
| Taker absorption | MISSING | MISSING | MISSING | MISSING | WIRED | MISSING | MISSING |
| Maturity gate (90d floor) | WIRED | WIRED | WIRED | MISSING | WIRED | WIRED | WIRED |
| Floor buffer | WIRED | WIRED | **IMPORTED_UNUSED** | MISSING | WIRED | WIRED | WIRED |
| Liquidity (MIN_24H_USD_VOLUME) | WIRED | WIRED | WIRED | MISSING | WIRED | MISSING (bypassed) | MISSING (bypassed) |
| EWMA buy-zone | MISSING* | MISSING* | MISSING* | MISSING* | MISSING* | MISSING* | MISSING* |
| Cheap window | MISSING* | MISSING* | MISSING* | MISSING* | MISSING* | MISSING* | MISSING* |

\* EWMA buy-zone (`flip_ewma_buyzone.py`) and Cheap Window (`flip_cheap_window.py`)
are not fleet gates at all — they're standalone research jobs in `run_all.py`'s
`JOBS` list (`EWMA`, `CHEAP`), each with its own ledger/push, called by no fleet.
Same dual-purpose pattern noted for two other "gates": `regime.py` and
`orderbook_imbalance.py` each export the function a fleet imports (`classify_regime`,
`obi_gate`) **and** separately run their own `run_cycle()` as an independent
`REGIME`/`OBI` lab job in `run_all.py` — two different call paths off the same file,
neither one dead. No gate-shaped module outside the 15 listed was found.

**(A) DELIBERATE** — evidence quoted from the code:

- **Markov's near-total absence from the matrix** (no VWAP/RSI/absorption/regime/OBI/
  flow/anomaly/catch-band/maturity/liquidity/ticker-sanity). `markov.py` docstring:
  *"Liquidity-sweep + reclaim + RSI-divergence engine... CONFLUENCE GATE (wired above
  the fire path)... ISOLATED: own hourly data layer (markov_screener), own ledger,
  own page."* It runs `confluence_gate()` as its one and only fire-gate; everything
  else in the matrix belongs to a pullback-from-high architecture Markov doesn't use.
- **Dogs' absence of flow gate and anomaly gate.** `dogs.py` docstring lists its gate
  stack explicitly: *"4. checks the REGIME gate (daily reverting) and OBI gate (book
  toxicity)"* — two gates named, no third or fourth. Flow and anomaly are absent
  because they were never in the design, not because they broke.
- **VWAP/Dynamic-RSI/Taker-absorption wired only in dogs.py.** `dogs.py` docstring:
  *"runs the existing triple-confirmation (RSI + absorption + VWAP) for a probability
  read"* — framed as this fleet's own experimental instrumentation (*"A TEST BENCH,
  not a finished strategy"*), not a baseline every fleet should have.
- **rider_flare's ticker-sanity check replaced, not wired.** Its own docstring:
  *"the price used for entry/exit decisions is FTSO... This is a pure data-source
  swap through the same ohlcv_fn parameter mechanism price_fn already used."* FTSO
  has no `MAX_TICKER_DIVERGENCE_PCT`-style check; that specific Coinbase-tuned gate
  doesn't apply once the price source changes.
- **rider_flare's liquidity gate bypassed by design.** `_universe()`'s docstring:
  *"Fixed to the confirmed FTSOv2 A/B set -- not a live market scan."* There's no
  market scan step to filter by `MIN_24H_USD_VOLUME` against.
- **solo_rider excluded from this A/B split per the task's own scope** (its one
  liquidity-bypass difference is the direct, documented consequence of *"WHAT THE
  USER ACTUALLY GETS TO SET: asset choice and capital amount"* — the user's single
  chosen asset skips the market-scan liquidity pre-filter that only exists to narrow
  a market-wide candidate list down).

**(B) UNEXPLAINED — candidate bugs:**

- **`core.py` has no regime gate and no anomaly gate anywhere in the file** — not
  imported, not called, no `USE_REGIME_GATE`/`USE_ANOMALY_GATE` flag, and no comment
  anywhere in `core.py` explaining the absence the way `dogs.py`'s docstring
  explains its own gaps. Evidence absent either way; flagging as a candidate bug
  rather than assuming intent.
- **`core.py` imports `RIDER_FLOOR_BUFFER` (line 43) but never uses it.** No
  `above_floor_buffer`-style check exists anywhere in `core.py`, unlike
  `rider_team.py`/`scavengers.py`/`dogs.py`, which all import the same constant and
  actually gate on it. Dead import, no comment explaining why it was left in — looks
  like a partially-finished port of the floor-buffer check that never got wired up.
- **`dogs.py` has no catch-band ceiling** (no `DOG_MAX_DROP_PCT`, no `MAX_DROP_PCT`
  reference of any kind). `rider_team.py` and `scavengers.py` both pair their
  pullback trigger with an upper crash-filter; `dogs.py`'s docstring describes its
  entry trigger (*"price >= DOG_PULLBACK_PCT below ANY window high"*) but never
  mentions, positively or negatively, a ceiling. Evidence absent either way —
  flagging as B, not assuming the omission was intentional.

**Threshold table** — every gate WIRED in more than one fleet:

| Gate | rider_team / solo_rider / rider_flare | scavengers | dogs | core | Defined in |
| --- | --- | --- | --- | --- | --- |
| Pullback trigger | `RIDER_PULLBACK_PCT` = 10.0% | `SCAV_PULLBACK_PCT` = 5.0% | `DOG_PULLBACK_PCT` = 10.0% | n/a (mechanical ladder, `core_breach()`) | `config.py` |
| Catch-band ceiling | `RIDER_MAX_DROP_PCT` = 30.0% | `SCAV_MAX_DROP_PCT` = 20.0% | **none** | n/a | `config.py` |
| Floor buffer | `RIDER_FLOOR_BUFFER` = 1.05 (imported directly, not fleet-prefixed) | same `RIDER_FLOOR_BUFFER` (imported directly) | same `RIDER_FLOOR_BUFFER` (imported directly) | same `RIDER_FLOOR_BUFFER`, imported but unused | `config.py` |
| Liquidity floor | `MIN_24H_USD_VOLUME` = $1,000,000/day (all fleets share one global value) | same | same | same | `config.py` |
| Ticker divergence | `MAX_TICKER_DIVERGENCE_PCT` = 50.0% (rider_flare: n/a, FTSO-priced) | same | same | same | `config.py` |
| Regime / OBI / Flow / Anomaly | shared function logic (`classify_regime`, `obi_gate`, `check_flow`, `check_anomaly`) — no fleet-specific threshold constant found in `config.py` for any of these four | same | same (regime+OBI only) | same (OBI+flow only) | `regime.py` / `orderbook_imbalance.py` / `footprint`-based flow module / `anomaly_gate.py` |

**Flags:**

- **(a) Cross-import** — `scavengers.py` and `dogs.py` both import `RIDER_FLOOR_BUFFER`
  directly rather than having their own `SCAV_FLOOR_BUFFER`/`DOG_FLOOR_BUFFER`. Same
  class of issue as the historical `RIDER_TARGET_PCT` cross-import. Not necessarily
  wrong — the buffer marks CORE's territory, which is fleet-agnostic by definition —
  but it means a future tune of `RIDER_FLOOR_BUFFER` silently retunes three other
  fleets at once with no fleet-specific override point.
- **(b) Scale mismatch** — Scav got a properly-paired, independently-scaled
  pullback/catch-band pair (`config.py` comments show the math: -5% pullback / 3-day
  lookback / 20% ceiling, sized off its own worst real entry). Dogs shares Rider's
  exact -10% pullback number but has no catch-band ceiling at all — the one pair in
  the table that was meant to scale together (per the Rider/Scav precedent) and
  didn't get a second half.

Full grep evidence, line numbers, and docstring quotes for every cell above are in
this session's working notes; not reproduced here in full to keep this entry
readable. **No code changed. Waiting on Clay's decision on wiring order before
touching anything.**

---

### Gate-parity audit — decisions

Clay reviewed the B-list from the audit above. Documentation-only pass — comments
and docstrings added at each site so the reasoning travels with the code; no
executable line touched anywhere.

**Rejected — not bugs, do not re-raise:**

- **`core.py`'s missing regime gate.** `classify_regime()` reports `'reverting'`
  or `'trending_up'`. CORE is the bear/crash engine — it exists to enter breaches
  while price is trending down. Wiring the regime gate would reject every entry
  the fleet exists to make. Same class of deliberate carve-out as the flow gate
  guarding rung 0 only in `rider_team.py`. DESIGN NOTES added to `core.py`'s
  module docstring.
- **`core.py`'s dead `RIDER_FLOOR_BUFFER` import.** `core_breach()` fires on
  price BELOW the 90-day floor; the floor buffer requires
  `price >= floor * 1.05`. Mutually exclusive by construction — wiring it would
  stop CORE from ever firing. The import stays, but it's now marked in the
  module docstring as scheduled for **deletion**, not for connecting.

**Confirmed gap — real, fix deferred to after 2026-08-14:**

- **`core.py`'s missing anomaly gate.** The catch-band elsewhere in this system
  routes below-band crashes to CORE by design, so an unwinding blow-off top
  (see HFT: ~3.5x pump then ~74% collapse) reaches CORE looking identical to a
  real breakdown — and the 6-2-1-1 ladder under never-cut would pin four rungs
  into it. Flagged in `core.py`'s module docstring.
- **`dogs.py`'s missing catch-band ceiling.** Dogs shares Rider's exact -10%
  pullback trigger but never got the matching upper crash-filter Rider
  (`RIDER_MAX_DROP_PCT`) and Scav (`SCAV_MAX_DROP_PCT`) both have. Comment added
  at the trigger line (`is_candidate = max_drop >= DOG_PULLBACK_PCT`).

**Threshold flag, unchanged status:** `RIDER_FLOOR_BUFFER` (`config.py`) is still
cross-imported directly by `scavengers.py` and `dogs.py` — same class of issue as
the old `RIDER_TARGET_PCT` cross-import. Comment added above the constant;
editing it retunes three fleets at once until it's split.

**Wiring order, once 2026-08-14 passes:**

1. Anomaly gate into `core.py`
2. Catch-band ceiling into `dogs.py`
3. Split `RIDER_FLOOR_BUFFER` into `SCAV_FLOOR_BUFFER` / `DOG_FLOOR_BUFFER`, and delete the dead `RIDER_FLOOR_BUFFER` import from `core.py`

No code behavior changed by this pass — comments and docstrings only.

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

### Investigated / Known gap
- On-chain order-flow indexing (DEX liquidity depth as a real execution-quality
  signal) was scoped and declined for this submission, but only after checking
  Flare's actual DEX liquidity directly — SparkDEX (v2/v3/v3.1/v4) + Enosys +
  BlazeSwap + the FXRP/FBTC FAssets pools, queried via GeckoTerminal's
  Flare-network pool index (search API scoped to `network=flare`, so this
  spans every DEX GeckoTerminal indexes on the chain, not one venue). A first
  pass checked BlazeSwap alone and returned "14/16 no liquidity" — a false
  reading, corrected below.
- Per-symbol result for all 16 `FLARE_UNIVERSE` symbols (best/largest pool
  shown per symbol; contract addresses are Flare mainnet pair addresses):

  | Symbol | Pool exists | Venue | Pair contract | Liquidity ($) | 24h swaps |
  |---|---|---|---|---|---|
  | FLR | **Y** (native) | SparkDEX v4 (stFLR/WFLR 0.05%) | `0x54b971682f4438ebd0c3ff4dcba67fb7e16b9de4` | $1,323,991 | 76 |
  | XRP | **Y** (via FXRP) | SparkDEX v4 (FXRP/USD₮0 0.05%) | `0x927485d88a66253c63af9163dca5f21c25a57393` | $1,815,401 | 1,071 |
  | ETH | **Y**, thin (via bridged WETH) | SparkDEX v4 (WETH/flrETH 0.05%) | `0x79af232ae7ccd460439af3515022c10f5509d9f8` | $132,398 | 34 |
  | BTC | N | — (FBTC search: 0 pools) | — | $0 | 0 |
  | SOL | N | — (2 hits, but "SolarX", a different token — not real SOL) | — | $0 | 0 |
  | UNI | N | — (3 hits, but "FUCT", a different token — not real Uniswap) | — | $0 | 0 |
  | LINK, AAVE, ONDO, AVAX, NEAR, HBAR, ADA, XLM, ARB, OP | N | — (0 indexed pools each) | — | $0 | 0 |

  FXRP alone carries 20 pools across SparkDEX v2/v3.1/v4 and Enosys v3
  (largest four: $1.82M, $929K, $803K, $668K reserve), confirming FAssets is
  where Flare's real cross-chain liquidity lives, exactly as expected. FLR's
  depth is unsurprising (native gas token). Real, corrected verdict: **13 of
  16 symbols have zero on-chain liquidity on any Flare DEX** — not because
  the wrong venue was checked, but because none of those 13 are FAssets-
  wrapped or bridged to Flare at all (SOL/UNI's search hits are unrelated
  tokens that happen to match the query string). FDC and Flare's
  confidential-compute order book remain the path forward for reading order
  flow on the 13 symbols with no venue to read it from.
- **Roadmap (named future work, not shipped):** dual-oracle consensus
  execution — weighing CoinGecko's off-chain OHLC against the on-chain
  swap-derived OHLC (see "on-chain divergence measurement" below) to help
  dictate entry/exit for Flare-native assets (FLR, FXRP), building directly
  on the divergence-measurement layer shipped today. What's shipped now is
  the measurement/cross-verification only — `flare/onchain_divergence.py`
  reads both sides and records the spread; nothing reads that spread back
  into `price_fn`, a gate, or any entry/exit decision. Consensus-weighted
  execution is the next step past this submission, not part of it.

### Added
- `flare/coingecko_adapter.py` — CoinGecko daily-close price source for the
  16 `FLARE_UNIVERSE` symbols, built to test (not assume) whether Coinbase's
  quote resolution is why `pullback_insufficient` fires so often. Static
  16-symbol CoinGecko id map; primary path is `/market_chart?interval=daily`
  (confirmed to return true ~24h-spaced points — 91 points at `days=90`);
  automatic fallback to the default hourly granularity, resampled to one
  point/UTC-day, on any failure of the primary call. 1.5s spacing between
  calls. **Wired into `rider_flare.py` later the same day — see below.**

**Quote-resolution comparison** (2026-08-11, 7-day window, hourly granularity
both sides — Coinbase via `ccxt fetch_ohlcv`, CoinGecko via `market_chart`
default hourly): CoinGecko returned a distinct price on every single sample
for all 16 symbols (169/169 each). Coinbase's resolution varies enormously by
symbol:

| Symbol | Coinbase distinct/samples | CoinGecko distinct/samples | Real `pullback_insufficient` rate (rider_decisions, all-time) |
|---|---|---|---|
| OP | 8/155 (94.8% flat) | 169/169 | 99.4% |
| FLR | 22/168 (86.9% flat) | 169/169 | 99.6% |
| ARB | 42/168 (75.0% flat) | 169/169 | 100.0% |
| LINK | 124/168 (26.2% flat) | 169/169 | 96.7% |
| AVAX | 125/168 (25.6% flat) | 169/169 | 99.4% |
| HBAR | 129/168 (23.2% flat) | 169/169 | 99.3% |
| AAVE | 138/168 (17.9% flat) | 169/169 | 46.1% |
| SOL | 143/168 (14.9% flat) | 169/169 | 99.6% |
| XRP | 145/168 (13.7% flat) | 169/169 | 99.3% |
| NEAR | 155/168 (7.7% flat) | 169/169 | 99.3% |
| UNI | 159/168 (5.4% flat) | 169/169 | 99.6% |
| ETH | 163/168 (3.0% flat) | 169/169 | 99.7% |
| ADA | 164/168 (2.4% flat) | 169/169 | 99.7% |
| ONDO | 164/168 (2.4% flat) | 169/169 | 43.3% |
| XLM | 166/168 (1.2% flat) | 169/169 | 99.3% |
| BTC | 168/168 (0.0% flat) | 169/169 | 99.6% |

Cross-referencing against the actual `pullback_insufficient` rate per symbol
(queried live from `rider_decisions`, all fleets `rider`+`rider_flare`,
33,901 rows scanned) narrows the migration's case considerably from the
initial hypothesis: **BTC is 0% flat on Coinbase and still blocked 99.6% of
the time** — for the highly-liquid majors (BTC, ETH, ADA, XLM, and SOL to a
lesser extent), Coinbase's resolution is already good, so the block is a
genuine "no pullback happened," not a quote-resolution artifact, and
CoinGecko would not change that outcome. The resolution-caused-false-block
hypothesis holds up only for the symbols that are BOTH heavily blocked AND
have visibly stale Coinbase ticks: **OP, FLR, and ARB are the strong cases**
(75-95% flat ticks, 99.4-100% blocked); **LINK, AVAX, and HBAR are moderate
cases** (23-26% flat, 96.7-99.4% blocked). AAVE and ONDO are notable outliers
— block rates of 46.1% and 43.3% are far below the rest of the universe, so
whatever the reason, it isn't a resolution problem for those two. Net: of the
13 symbols with zero real block-rate exceptions, roughly 3-6 look like a real
quote-resolution confound the migration would fix; the rest are correct gate
behavior on a fine-enough quote.

**CoinGecko endpoint findings** (2026-08-11, unauthenticated free tier):
- `/coins/{id}/ohlc` auto-selects candle granularity from the `days` window
  with no override — at `days=90` it returned 23 candles spaced exactly 4
  days apart. Unusable as a 90-day-floor source at that range.
- `/coins/{id}/market_chart?interval=daily` currently works unauthenticated
  and returns true ~24h-spaced points (91 points confirmed at `days=90`).
  Treated as untrusted/unstable (undocumented behavior on the free tier)
  rather than a guaranteed contract — `coingecko_adapter.py` carries an
  automatic fallback to the default hourly granularity, resampled to daily,
  rather than assuming this keeps working.
- The unauthenticated endpoint's practical burst tolerance is lower than 1-2s
  spacing alone suggests: a sequential 16-symbol research pass at 1.5s
  spacing with no prior traffic hit 429s after ~5 calls and needed
  exponential backoff (up to 45s) to complete all 16. The adapter's actual
  call pattern (one call per symbol per cycle, not a 16-call research burst)
  is a lighter load, but this is worth knowing before assuming 1-2s spacing
  alone is sufficient under any heavier use.

### Added (later the same day — wiring + on-chain divergence)

**CoinGecko candles wired into `rider_flare` (Task 1, isolated from Task 2 below).**
`rider_flare.py`'s daily-candle inputs — the 90-day floor, the rolling 7-day
high, and the regime/anomaly gates' `daily_closes()` — now read
`flare/coingecko_adapter.py` instead of Coinbase. `price_fn` (FTSO,
entry/exit) is untouched. Mechanism: a new `ohlcv_fn` parameter threaded
through `screener.calculate_90_day_floor`/`rolling_7_day_high` and
`rider_team.daily_closes`/`run_cycle`/`run_engine` — same shape as the
existing `price_fn` parameter, default `None` (→ unchanged Coinbase
behavior for every other caller: `harness.py`, `allocator.py`, `core.py`,
`scavengers.py`, `dogs.py`, `solo_rider.py`, `ranking.py` all pass nothing
and are byte-identical). Only `flare/rider_flare.py` passes the CoinGecko
functions. `get_cg_daily_ohlcv()` is a shape-compatible shim (CoinGecko has
no real OHLC, only a price series — open=high=low=close=price, volume=0,
documented in its own docstring as an honest simplification, not fabricated
spread data) capped at CoinGecko's real 365-day free-tier limit (confirmed
2026-08-11: `days=400` → 401 Unauthorized, `days=365` → 366 points), which
comfortably clears `screener.MIN_HISTORY_CANDLES` (280) so the maturity gate
behaves the same way it always has, just fed by different candle data.

**Reliability fix found by actually running it.** The first real verification
cycle (16 symbols, fresh state, throwaway ledger — never touched the live
`data/rider_flare_ledger.json`) with a hard per-cycle cache clear reproduced
the same 429 burst seen during the original research pass: 6 of 16 symbols,
including FLR, exhausted both the CoinGecko daily and hourly-resample paths
and came back empty that cycle. Since this wiring runs every 15 minutes in
production, that failure mode would recur every cycle, not be a one-off.
Fixed by switching the candle cache from a hard per-cycle clear (right for
Coinbase, which has no such rate limit) to a 1-hour TTL (`CG_CACHE_TTL_S`) —
daily candles don't need better than hourly freshness, and this cuts real
call volume ~4x. Also bumped the 429 retry from 1 attempt/8s to 2
attempts/8s+16s. Re-ran the same verification cycle after the fix: 15 of 16
symbols succeeded (only ONDO failed that pass — an isolated miss, not a
repeat of the systemic failure).

**Verification cycle results for OP/FLR/ARB** (2026-08-11, live FTSO price +
CoinGecko-sourced floor/7d-high, `rider_decisions`, fleet=`rider_flare`):

| Symbol | price | rolling_7d_high | floor_value | pullback_pct | block_reason |
|---|---|---|---|---|---|
| OP | 0.091111 | 0.09134027 | 0.08694646 | 0.25% | pullback_insufficient |
| FLR | 0.00604101 | 0.00609743 | 0.00607169 | 0.93% | pullback_insufficient |
| ARB | 0.078523 | 0.08032197 | 0.07565833 | 2.24% | pullback_insufficient |

Confirms the wiring is real: `rolling_7d_high`/`floor_value` are now distinct,
CoinGecko-derived values (not a flat repeated Coinbase tick), and the gate
correctly computes a genuine, small `pullback_pct` from them. **Honest
limitation:** this is one cycle, not a rate. All three still hit
`pullback_insufficient` this cycle because `pullback_pct` (0.25-2.24%) is
genuinely far under `RIDER_PULLBACK_PCT` (10%) right now — that's a real
"no dip happening," not a data artifact, and it's a different reason than
the historical Coinbase-tick-frozen block. A statistically meaningful
before/after block-RATE comparison (matching the historical 99.4-100% figures
above) needs the service to accumulate real cycles over time post-wiring;
that number isn't fabricated here and should be pulled from `rider_decisions`
after the service has run for a while.

**On-chain divergence measurement (Task 2) — NOT Goldsky.** A real Goldsky
subgraph deployment needs an account + API key this repo doesn't have
(confirmed: no `GOLDSKY_*` key in `.env`, and `goldsky` isn't even an npm
package — their CLI ships separately and needs `goldsky login`). Rather than
fake a subgraph or block on getting an account, built the same real thing —
on-chain swap-derived OHLC to cross-check CoinGecko's off-chain OHLC — by
reading `Swap` events straight from the pool contracts via the Flare block
explorer's indexed log API:
- `flare/onchain_swaps.py` — reads recent swaps via
  `flare-explorer.flare.network`'s (Blockscout) `/api/v2/addresses/{pool}/logs`
  endpoint, not `eth_getLogs` directly: the public Flare RPC caps
  `eth_getLogs` at **30 blocks per call** (confirmed 2026-08-11 — wider
  ranges return `"requested too many blocks ... maximum is set to 30"`),
  useless for a day of history. The explorer's API has no such cap and
  returns a pre-decoded `Swap` event plus block timestamp on every row.
  **Pools used are not the SparkDEX v4 pools the task named**: the v4 pools
  with the most FLR/FXRP liquidity ($1.3-1.8M, from the earlier DEX survey)
  are not standard Uniswap V3 pool contracts — `token0()`/`token1()`/`slot0()`
  revert against them (confirmed 2026-08-11), consistent with SparkDEX v4
  being a hooks/singleton architecture with no verified ABI available here;
  guessing at one and silently mis-decoding prices would be worse than using
  a smaller, real pool. Used the largest USD-stable pool of each asset
  confirmed to be a genuine Uniswap V3 fork instead: **FLR** → WFLR/USDT0
  0.3% on Enosys v3 (`0x3c2a7b76795e58829faaa034486d417dd0155162`, $363K
  reserve), **FXRP** → FXRP/USDT0 0.05% on SparkDEX v3.1
  (`0x88d46717b16619b37fa2dfd2f038defb4459f1f7`, $404K reserve).
- `flare/onchain_divergence.py` — one-shot measurement, same shape as
  `flare/divergence.py` (FTSO vs Coinbase): CoinGecko's live price vs the
  most recent real swap price. FXRP's off-chain reference is XRP (there is
  no independent FXRP market to quote — FXRP is a wrapped, over-collateralized
  representation of XRP; comparing against XRP's price IS the actual
  question, "does the wrap hold its peg on-chain," not a workaround).
  First real readings (2026-08-11): **FLR +41.6 bps**, **FXRP -15.2 bps**.
- `flare/onchain_divergence_recorder.py` — continuous version, same shape as
  `flare/divergence_recorder.py` (rotating log, STARTUP/CYCLE health rows,
  `BLIND_TRANSPORT`/`ZERO_ROWS_FATAL` thresholds), 5-minute cadence. The
  `onchain_divergence` table (this process has no DDL access to create
  itself — no `exec_sql` RPC, no direct Postgres connection string in
  `.env`) was created directly in Supabase later the same day. **Manual
  first run, not yet a registered service** — see below.
- **Isolation confirmed by grep**, not just by design intent: neither
  `rider_team.py` nor `flare/rider_flare.py` references `onchain_swaps`,
  `onchain_divergence`, or `goldsky` anywhere. This is a measurement/logging
  layer only — nothing reads the on-chain-vs-off-chain spread back into
  `price_fn`, a gate, or any entry/exit decision.

### Added (later still — first manual run of the on-chain divergence recorder)

Ran `python -m flare.onchain_divergence_recorder` by hand (not registered as
a scheduled task yet, deliberately — watching the first cycles before
deciding on that) once the `onchain_divergence` table existed. Verified every
claim against the actual data, not just the console/log output:
- **STARTUP row** in `rider_health` (id 4025, 21:53:41 UTC):
  `{symbols: [FLR, FXRP], cycle_sec: 300, env_loaded: true}`.
- **Two clean CYCLE rows**, not one: the loop records immediately on start
  (id 4028, 21:54:12 UTC, ~30s after STARTUP) and again one cadence later
  (id 4039, 21:59:42 UTC, ~5.5 min after the first) — both
  `{rows_written: 2, universe_size: 2, consecutive_failures: 0}`.
- **Confirmed by querying `onchain_divergence` directly**, not by trusting
  the log: 4 rows present (ids 1-4), matching the log line-for-line.

  | ts (UTC) | symbol | offchain (CoinGecko) | onchain (swap) | divergence_bps |
  |---|---|---|---|---|
  | 21:54:12 | FLR  | 0.00607063 | 0.00607337 | +4.52 |
  | 21:54:12 | FXRP | 1.021      | 1.02209    | +10.70 |
  | 21:59:42 | FLR  | 0.0060723  | 0.00607337 | +1.77 |
  | 21:59:42 | FXRP | 1.023      | 1.02241    | -5.76 |

  `timestamp_gap_ms` on these rows runs from ~82K to ~986K ms (up to ~16
  min) — expected, not a bug: it's the gap between the CoinGecko fetch and
  the pool's *most recent* real swap, and these two pools don't swap every
  second.
- Stopped after the second cycle (8-minute watch timeout, a hard stop, not a
  graceful `KeyboardInterrupt` — functionally the same result as Ctrl+C:
  confirmed no orphaned `python.exe` process remained afterward via
  `Get-CimInstance Win32_Process`).

Clean two-for-two. Still not registered as a Windows Task Scheduler service
— that's a separate decision, not automatic from "it worked once."

### Added / Blocked (scheduled-task registration attempt)

Built `install_onchain_divergence_recorder_task.ps1` to register
**PRV1311-OnchainDivergenceRecorder**, deliberately using the module-invocation
fix pattern (`-Execute $PythonExe -Argument "-m flare.onchain_divergence_recorder"
-WorkingDirectory $RepoDir`) that resolved the AnchorWriter sys.path trap
earlier today — not the older by-path pattern. Same shape as
`install_divergence_recorder_task.ps1`: AtStartup, SYSTEM, RestartCount 999,
idempotent unregister-then-recreate, post-registration verification block.

**Registration failed: Access Denied.** `Register-ScheduledTask` requires an
elevated session for a SYSTEM-principal task; this one wasn't — confirmed
directly (`WindowsPrincipal.IsInRole(Administrator)` → `False`), not assumed
from the error alone. No task was created, nothing auto-started, so there is
no new STARTUP row and no new `onchain_divergence` rows from this attempt —
the existing 4 rows and the one STARTUP row (id 4025) are still the manual
test run's, unchanged. Reported the blocker instead of waiting 90 seconds to
check for something that couldn't have happened. Needs to be run from an
elevated PowerShell by a human with that access; the exact command is in the
script's own header. Verification (second STARTUP row, new `onchain_divergence`
row count) still pending that.

### Fixed (AnchorWriter incident response — three findings from today's testing)

**1. Backfilled a real anchor missing from `anchor_log`.** A manual foreground
run (cycle_id `905ec814-a5f2-401e-8444-7ce79a05256b`) anchored ETH/USD
on-chain successfully but was Ctrl+C'd before `_log_anchor_row()` ran, so the
row never landed. Independently re-verified every field against the live
chain before inserting anything — did not just trust the numbers handed over:
fetched the real transaction receipt (status `0x1`, block `0x40141c6`,
`gasUsed=153647`, `effectiveGasPrice=650000000000` wei → computed
`flr_paid=0.09987055`, matching exactly), decoded the actual
`DivergenceRecorded` event from the receipt (not guessed) — `divergenceBps:
-1`, `decisionHash: 85fc2152...`, both matching exactly — and independently
computed `feed_id` via `feed_id_bytes("ETH/USD")`, confirming it matches the
decoded on-chain `feedId` byte-for-byte. `ts` set to the block's real
timestamp (`2026-08-11T22:37:30+00:00`, read from the chain), not the insert
time. Row landed as `anchor_log.id=19`; confirmed via a fresh `select`.
**Honest caveat on the "matches wallet's real spend" check**: it does not,
and structurally shouldn't — `anchor_log` only ever logs successful automated
`recordDivergence()` calls from `anchor_writer.py`'s own loop. Reconciling
today's `anchor_log` total (1.66132135 FLR) against the wallet's actual
on-chain spend today (3.674232463988825 FLR, verified via the wallet's real
transaction history on `flare-explorer.flare.network`) surfaces a ~2.01 FLR
gap made up of 9 transactions that were never supposed to be in `anchor_log`
in the first place: the one-time mainnet contract deployment (`to: null`,
0.9003956 FLR) and 7 calls from an earlier manual `deploy_anchor.py --mainnet`
smoke-testing session (6 successful, 1 already-documented failure — tx
`737cb784...`, the exact out-of-gas revert already explained in
`deploy_anchor.py`'s own module docstring) — `deploy_anchor.py`'s interactive
CLI path never writes to `anchor_log` by design; only `anchor_writer.py`'s
`_run_cycle()` does. Confirmed no other automated-writer transaction is
missing beyond the one now backfilled.

**2. Fixed the daily-cap double-count bug.** Root cause, confirmed exactly
against the live incident: `flare/anchor_writer.py`'s per-symbol loop computed
`spent_today = _flr_spent_today() + flr_spent_this_run`. `_flr_spent_today()`
re-queries `anchor_log` fresh on every call — which, after any successful
write earlier in the SAME run, already includes those writes (Postgres is
read-after-write consistent; each `_log_anchor_row()` insert completes before
the loop reaches the next symbol). Adding `flr_spent_this_run` — the in-memory
tally of those exact same writes — counted them a second time. Matches the
real incident precisely: refused at `spent_today=1.83650155` when the true
total was `1.5614508` (fresh DB total, already including this run's writes)
`+ 0.27505075` (`flr_spent_this_run`, the same writes again) `= 1.83650155`.
~0.24 FLR of real headroom was refused as if it didn't exist. **Fix**: dropped
`+ flr_spent_this_run`; the fresh DB read alone is already authoritative, same
as the cycle-level check a few lines above it (which never had this bug —
it runs once, before this run has written anything). `flr_spent_this_run`
itself is untouched and still used for the end-of-cycle health-row summary.

**3. Diagnosed exit code 78 — root cause traced, not fixed (not asked to be).**
Exit 78 is `anchor_writer.py`'s own deliberate guard: any exception from the
import block at the top of the file (before the logger/health-row system
exists) prints a hint to stderr and exits 78, by design (see the file's own
comment, added when the original by-path-invocation bug was fixed). Two real
findings:
- **The "LastRunTime hours after last boot" premise doesn't hold up**: the
  machine's actual last boot was `2026-08-11 15:30:20` local — matching the
  reported `15:30 local` failure almost to the second. The AtStartup trigger
  fired exactly when it should have; there's no unexplained gap once the real
  boot time is checked instead of assumed.
- **Traced a concrete, plausible cause, not a guess**: `anchor_writer.py`
  imports `flare.deploy_anchor`, which imports `flare.price_adapter`, whose
  module level unconditionally runs `establish_coverage()` — a *live Flare-
  mainnet RPC bisection across 20 candidate symbols* — as an import side
  effect. Timed this import chain just now, on a healthy, long-since-booted
  network: **19.79 of the total 22.49 seconds was this one import.** In the
  first moments after a fresh boot — before DNS/network is fully up, and
  specifically before this machine's antivirus TLS-interception layer is
  ready (a pre-existing, already-documented issue on this exact machine, see
  2026-08-06) — this same RPC-dependent import is a prime candidate to fail
  outright or stall past patience, and either surfaces as exactly this: an
  import exception, caught by the guard, exit 78, nothing logged (the
  rotating file logger is only installed *after* these imports succeed).
- **Could not recover the literal historical exception.** Nothing redirects
  stdout/stderr for a Task-Scheduler-launched process (the guard's
  `print(..., file=sys.stderr)` goes nowhere under SYSTEM/Task Scheduler),
  and this machine's `Microsoft-Windows-TaskScheduler/Operational` event log
  is disabled (`IsEnabled: False`) — no forensic trail beyond what's
  reconstructed above from the boot timestamp and import-chain timing.
- **Bigger finding, in scope of "will this recur unattended overnight":**
  right now **zero PRV1311-prefixed scheduled tasks are registered at all** —
  confirmed against the full list of 208 tasks Task Scheduler currently
  knows about on this machine, not just AnchorWriter. An
  `uninstall_anchor_writer_task.ps1` script (untracked, present before
  today's session started) exists specifically to remove this exact task —
  consistent with it having been deliberately uninstalled to allow today's
  extensive manual/foreground testing without the scheduled service
  double-spending concurrently. Whatever caused the boot-time failure can't
  recur *right now* because nothing is scheduled — but by the same token,
  nothing runs unattended overnight during judging week (Aug 15-21) until
  something is deliberately re-registered.
- **Recommendation, not yet actioned** (diagnosis was asked for, not a fix):
  before re-registering `PRV1311-AnchorWriter`, add a startup delay to the
  AtStartup trigger (`-RandomDelay` or the trigger's `.Delay` property) so it
  doesn't fire in the fragile first seconds after boot — a standard
  mitigation for exactly this class of Windows AtStartup/network-race issue.

### Verified (end-to-end manual cycle, post-fix)

Today's real `anchor_log` total (1.66132135 FLR) left only ~0.139 FLR of
headroom against `MAX_FLR_PER_DAY=1.8` — not enough for a real 5-symbol
cycle. Flagged this to the user before proceeding rather than either running
a doomed-to-partially-refuse cycle or silently deciding; asked to
temporarily raise the cap for this one verification. `MAX_FLR_PER_DAY`
raised `1.8 → 3.0`, explicitly commented as temporary/must-revert. Ran
`python -m flare.anchor_writer` for one real cycle:

- **5/5 anchored, 0 skipped, no premature refusal** — `rider_health` CYCLE
  row (id 4190): `symbols_written` has all 5 (OP, FLR, ARB, BTC, ETH),
  `symbols_skipped: []`. No `REFUSED_DAILY_CAP` row anywhere between this
  cycle's STARTUP (id 4187) and its CYCLE row.
- **Confirmed the double-count fix is actually fixed, with real numbers**:
  `flr_spent_today` in the CYCLE row = `2.1364577`, which equals the prior
  real total (`1.66132135`) plus this run's own spend (`0.47513635`) —
  added *once*, not twice.
- **5 real anchor_log rows landed**, verified by a fresh `select` on both
  `cycle_id` and each individual `tx_hash` (ids 20-24) — not inferred from
  the log.
- Stopped the process afterward; confirmed via `Get-CimInstance
  Win32_Process` that no orphaned `python.exe` remained.
- **Reverted `MAX_FLR_PER_DAY` back to `1.8`** immediately, then ran a second,
  brief pass — stopped right after its STARTUP health row landed, before
  `_run_cycle()` could spend anything more — specifically to get a clean
  confirmation of the restored constants without more real spend. New
  STARTUP row (id 4458): `{cadence_hours: 8.0, max_flr_per_day: 1.8,
  min_wallet_balance: 2.0}` — matches production values exactly. Confirmed
  via a fresh `anchor_log` query that this second pass wrote zero new rows
  (still ids 20-24, nothing beyond).

`PRV1311-AnchorWriter` is verified working correctly (backfill closed, the
double-count bug fixed and proven fixed with real numbers, constants
confirmed restored) but is **still not registered as a scheduled task** —
that's a separate decision from "the code and the ceiling are correct now,"
and the startup-delay mitigation above is still just a recommendation, not
yet applied.

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
