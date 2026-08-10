# Site Handoff — Base44

Everything Base44's agent needs to build against this Supabase project, handed
over as text because it has no filesystem access to this repo and cannot see
this Supabase project directly. Every value below was pulled live from the
database tonight (PostgREST's OpenAPI schema endpoint for column definitions,
direct queries for row data and distinct-value counts) — not from memory, not
from how any table was originally designed.

---

## 1. Exact column lists

### `rider_decisions` — one row per candidate symbol, per evaluation cycle
The per-symbol decision log: what the engine looked at, what it measured, and
why it did or didn't trade.

| column | type | nullable | meaning |
|---|---|---|---|
| id | int64 | no | internal row id, not meaningful to display |
| ts | timestamptz | no | when this decision was logged — **the freshness signal for this table** |
| cycle_id | uuid | no | groups every decision from one evaluation pass; joins to `rider_cycles.cycle_id` |
| symbol | text | no | the trading pair evaluated, e.g. `BTC/USD` |
| source | text | yes | `market_scan` (found by the live liquidity screen) or `watchlist` (forced in regardless of the scan) |
| price | double precision | yes | the live price read for this symbol at evaluation time |
| rolling_7d_high | double precision | yes | the 7-day high used as this symbol's pullback reference point |
| pullback_pct | double precision | yes | how far below the 7-day high the current price sits, as a percent |
| floor_value | double precision | yes | the computed long-window floor price for this symbol |
| floor_buffer_ok | boolean | yes | whether price cleared the floor safety margin; **null means this gate was never reached, not false** |
| candle_count | int32 | yes | how many daily candles were available (maturity check input) |
| maturity_ok | boolean | yes | whether the symbol has enough trading history to be considered safe |
| volume_24h | double precision | yes | 24h volume observed during the market scan; null if this symbol came from the watchlist without a scan |
| liquidity_ok | boolean | yes | whether 24h volume cleared the minimum bar |
| regime_label | text | yes | the market regime classification for this symbol; **null means the regime gate was never reached** |
| regime_ok | boolean | yes | whether the regime gate allowed this symbol through |
| obi_value | double precision | yes | the order-book-imbalance reading at evaluation time |
| obi_ok | boolean | yes | whether the order-book gate allowed this symbol through |
| flow_verdict | text | yes | the order-flow gate's classification of recent trade activity |
| flow_reason | text | yes | human-readable reason behind the flow verdict |
| fired | boolean | no | whether this candidate actually resulted in a (paper) trade being opened |
| block_reason | text | yes | which gate stopped this candidate; **null if and only if it fired** (confirmed: 18/18 null rows have `fired=true`) |
| fleet | text | no | which engine produced this row — see distinct values below |
| limit_price | double precision | yes | limit price set for this attempt — **only ever populated by the `scav` fleet**; null for every other fleet |
| polled_price | double precision | yes | secondary live-polled price re-check — same fleet scope as `limit_price` |
| pct_below_high | double precision | yes | **dead column — 0 non-null rows across all fleets, all time.** Do not render this; `pullback_pct` is the field actually in use |
| user_id | text | yes | which end user this decision belongs to. **Null for `rider`/`scav`/`rider_flare` by design** (shared fleets, not per-user); populated only for `solo_rider` |

### `rider_cycles` — one row per evaluation pass (a "cycle")
The per-cycle summary: how big the candidate pool was, whether the cycle
stopped early, and current allocation state.

| column | type | nullable | meaning |
|---|---|---|---|
| id | int64 | no | internal row id |
| cycle_id | uuid | no | matches `rider_decisions.cycle_id` for this same pass |
| ts | timestamptz | no | when this cycle completed — **the freshness signal for this table** |
| universe_size | int32 | yes | how many symbols were in the candidate pool this cycle |
| symbols_evaluated | int32 | yes | how many were actually evaluated before the cycle stopped (may be less than universe_size — see halt_reason) |
| halt_reason | text | yes | why evaluation stopped early — e.g. `team_full`, `cash_floor` — null if it ran the full pool |
| halt_at_symbol | text | yes | which symbol evaluation was at when it halted |
| deployable_cash | double precision | yes | cash available to deploy at the end of this cycle, after reserve is set aside |
| bucket_usd | double precision | yes | the position size used for a single new entry this cycle |
| riders_open | int32 | yes | how many positions were open at the end of this cycle |
| fleet | text | no | same fleet scope as `rider_decisions.fleet` |
| user_id | text | yes | same scope as `rider_decisions.user_id` — null except for `solo_rider` |

### `rider_health` — service liveness / status events
Not a per-cycle log for every engine — see Section 4 for exactly which
components write here every cycle vs. only once at startup.

| column | type | nullable | meaning |
|---|---|---|---|
| id | int64 | no | internal row id |
| ts | timestamptz | no | when this health event was recorded — the freshness signal, but only meaningful per-component (Section 4) |
| component | text | no | which service/subsystem wrote this row — see distinct values below |
| status | text | no | the event type — see distinct values below |
| detail | jsonb | yes | free-form payload, shape varies by component/status — see sample rows |
| consecutive_failures | int32 | no | running failure counter at the time of this event; 0 on a clean event |

### `oracle_divergence` — FTSO oracle vs. Coinbase venue price comparison
Continuous recorder output, one row per symbol per recorder cycle (~60s).

| column | type | nullable | meaning |
|---|---|---|---|
| id | int64 | no | internal row id |
| ts | timestamptz | no | when this row was written — the freshness signal for this table |
| symbol | text | no | the asset compared, e.g. `BTC/USD` |
| feed_id | text | yes | the Flare FTSOv2 feed identifier (hex) this row read from |
| oracle_value | double precision | yes | the price FTSOv2 reported |
| oracle_timestamp | int64 | yes | unix seconds, the oracle's own native timestamp unit — **not normalized to venue_timestamp's unit on purpose**, see `timestamp_gap_ms` |
| venue_value | double precision | yes | the price Coinbase reported |
| venue_timestamp | int64 | yes | unix **milliseconds** — as of tonight, this is the moment the batch was fetched, not an exchange-reported tick time (Coinbase's bulk ticker endpoint returns no per-ticker timestamp at all; confirmed live) |
| timestamp_gap_ms | int64 | yes | `|oracle_timestamp*1000 - venue_timestamp|` — the one column that normalizes the two units above into a single comparable gap |
| divergence_bps | double precision | yes | `(oracle_value - venue_value) / venue_value * 10000` — how far apart the two prices are, in basis points |

### `rider_state` / `scav_state` — full ledger snapshot, one row each
Single-row blob tables (`id=1`), the current complete paper-trading ledger for
the shared `rider` and `scav` fleets respectively.

| column | type | nullable | meaning |
|---|---|---|---|
| id | int32 | no | always `1` — this is a single-row table |
| ledger | jsonb | no | the full ledger: open positions, cash balance, treasury, fees, and trade history. See sample rows — do not attempt to summarize this shape from the schema alone, it's a nested object |
| updated_at | timestamptz | no | **NOT a reliable freshness signal — see Section 4, this is a real, confirmed gap** |

### `solo_rider_config` — per-user dials (asset + capital)
One row per Solo-Rider user; currently 1 row exists.

| column | type | nullable | meaning |
|---|---|---|---|
| user_id | text | no | primary key. Currently the literal string `"unknown"` for the one existing row — see Section 2, this is a real intake gap, not a display bug |
| asset | text | no | the single asset this user's Solo-Rider trades |
| capital | double precision | no | this user's capital allocation, bounded $25–$1000 |
| status | text | no | `active` is the only value observed so far |
| updated_at | timestamptz | no | reliable — set explicitly on every write, unlike `rider_state`/`scav_state` above |

### `solo_rider_state` — per-user ledger snapshot
One row per Solo-Rider user, same ledger shape as `rider_state`/`scav_state`
but scoped to one user's own capital.

| column | type | nullable | meaning |
|---|---|---|---|
| user_id | text | no | primary key, matches `solo_rider_config.user_id` |
| ledger | jsonb | yes | same shape as `rider_state.ledger` |
| updated_at | timestamptz | no | **NOT reliable — same gap as `rider_state`/`scav_state`, see Section 4** |

---

## 2. Which values actually appear

**`rider_decisions.fleet`** (n=25,598): `rider` 18,906 · `scav` 3,325 ·
`rider_flare` 3,280 · `solo_rider` 87

**`rider_decisions.block_reason`** (n=25,598): `pullback_insufficient`
18,139 · `floor_fetch_failed` 3,831 · `already_held` 1,354 · `anomaly_veto`
1,259 · `floor_buffer_fail` 693 · `price_fetch_failed` 123 · `flow_veto` 107 ·
`below_catch_band` 35 · `regime_gate_blocked` 24 · **null 18** (these are the
fired=true rows) · `obi_gate_blocked` 10 · `high7_fetch_failed` 5

**`rider_health.component`** (n=277): `solo_rider_engine_unknown` 91 ·
`footprint_worker` 90 · `divergence_recorder` 76 · `rider_engine` 7 ·
`universe_fetch` 7 · `run_all` 2 · `rider_flare_engine` 2 ·
`solo_rider_service` 2

**`rider_health.status`** (n=277): `CYCLE_PAPER` 91 · `CYCLE` 72 · `OK` 71 ·
`STARTUP` 20 · `RECONNECT` 13 · `DEGRADED` 7 · `STARTUP_PAPER` 2 ·
`ZERO_ROWS_FATAL` 1

**`oracle_divergence.symbol`** (n=1,056 and growing): all 16 tracked
symbols, evenly represented — `AAVE/USD` `ADA/USD` `ARB/USD` `AVAX/USD`
`BTC/USD` `ETH/USD` `FLR/USD` `HBAR/USD` `LINK/USD` `NEAR/USD` `ONDO/USD`
`OP/USD` `SOL/USD` `UNI/USD` `XLM/USD` `XRP/USD`

**`solo_rider_config.status`** (n=1): `active` 1. Only one row exists right
now, and its `user_id` is the literal string `"unknown"` — **a real,
confirmed intake gap, worth knowing before building a per-user view**: Base44
SoloOrder submissions without a real `created_by`/`user_id` field all
collapse onto the same `"unknown"` config row and silently overwrite each
other. A second real order will clobber the current one until intake is
fixed to require a real user identifier.

**Null-carries-meaning columns, explicit list**: `floor_buffer_ok`,
`maturity_ok`, `liquidity_ok`, `regime_label`, `regime_ok`, `obi_value`,
`obi_ok`, `flow_verdict`, `flow_reason` on `rider_decisions` are all null
when their gate was never reached (an earlier gate already blocked the
candidate) — **not false, not "checked and failed."** A site rendering
these as red/failed rather than gray/not-reached would misstate what the
engine actually did. `block_reason` null is the one exception where null is
unambiguously good news (it means fired).

---

## 3. RLS state, as built tonight

| table | RLS enabled | policy | command | roles | anon-covered |
|---|---|---|---|---|---|
| rider_decisions | yes | `public read rider_decisions` | SELECT | `{public}` | yes |
| rider_cycles | yes | *(fix handed over tonight — see below)* | — | — | **unconfirmed** |
| rider_health | yes | `public read rider_health` | SELECT | `{public}` | yes |
| oracle_divergence | yes | `public read oracle_divergence` | SELECT | `{public}` | yes |
| rider_state | yes | `public read` | SELECT | `{anon}` | yes |
| scav_state | yes | `public read scav` | SELECT | `{anon}` | yes |
| solo_rider_config | yes | `public read solo_rider_config` | SELECT | `{public}` | yes |
| solo_rider_state | yes | `public read solo_rider_state` | SELECT | `{public}` | yes |

**Flag, stated plainly**: `rider_cycles` was found tonight with RLS enabled
and **zero policies** — locked to every role, not just anon. Fix SQL was
handed over in this same session:
```sql
create policy "public read rider_cycles" on public.rider_cycles
    for select using (true);
notify pgrst, 'reload schema';
```
I have not independently re-confirmed this was applied — I don't have direct
`pg_policies` access from my own tools, only the earlier connector-based
check did. **Verify this one specifically before treating `rider_cycles` as
readable by the site** — a policy that looks fine in the catalog but doesn't
cover `anon` shows the site nothing, silently.

`{anon}` vs `{public}` are both fine — `anon` is the more precise target
(the exact role a public anon-key client authenticates as), `public` is the
broader pseudo-role that includes it. Functionally equivalent here.

---

## 4. Freshness signal per table

| table | true freshness column | expected cadence |
|---|---|---|
| rider_decisions | `ts` | per candidate, per cycle — bursty, many rows per cycle |
| rider_cycles | `ts` | one row per cycle: `rider`/`rider_flare` ~15 min, `scav` ~10 min, `solo_rider` ~60s |
| rider_health | `ts` | see per-component breakdown below — **not uniform across components** |
| oracle_divergence | `ts` | one row per symbol per ~60s recorder cycle |
| **rider_state** | **none reliable** | `push_ledger()` never sets `updated_at` explicitly — it only reflects the row's original creation, not its last write. The underlying `rider` engine is genuinely live (confirmed via `rider_cycles`/`rider_decisions`); this column just can't prove it. **Do not build a staleness indicator on this column.** |
| **scav_state** | **none reliable** | same gap, same reason, same fix needed |
| solo_rider_config | `updated_at` | reliable — set explicitly on every intake write |
| **solo_rider_state** | **none reliable** | same `push_ledger()` gap as `rider_state`/`scav_state` — this table is written by the same function |

**Correct proxy for `rider_state`/`scav_state`/`solo_rider_state` liveness**:
join against the most recent `rider_cycles.ts` for the matching `fleet` (and
`user_id`, for solo_rider), not the state table's own `updated_at`.

**`rider_health` per-cycle vs. startup-only, by component** (this determines
what a "service is alive" indicator can honestly claim):

| component | writes | what "stale" would mean |
|---|---|---|
| `solo_rider_engine_<user_id>` | every cycle (~60s) | a real, current liveness signal |
| `divergence_recorder` | every cycle (~60s), as of tonight's fix | a real, current liveness signal |
| `footprint_worker` | appears to write regularly (90 rows, mixed `OK`/`RECONNECT`/`DEGRADED`) | reasonably current, not verified as strictly per-cycle |
| `rider_engine` | **STARTUP only** — 7 rows total, all-time | cannot detect a live-but-silent failure; only proves the process started at some point in the past |
| `rider_flare_engine` | **STARTUP only** — 2 rows total | same limitation |
| `run_all` | **STARTUP only** — 2 rows total | same limitation |
| `solo_rider_service` | **STARTUP_PAPER only** — the top-level service row; per-user cycling is tracked separately via `solo_rider_engine_<user_id>` | same limitation at the service level |
| `universe_fetch` | only on a specific degraded-fallback condition, not every cycle | absence of rows is good news, not staleness |

For `rider_engine`/`rider_flare_engine`/`run_all`: **`rider_cycles.ts` for
that fleet is the only honest liveness signal available today** — their own
`rider_health` rows cannot detect a hang, only a crash-and-restart.

---

## 5. Sample rows

### rider_decisions
```json
{
  "id": 25599, "ts": "2026-08-10T03:36:09.382635+00:00",
  "cycle_id": "623dcada-0a9f-42bb-aae4-3aca16a56c74",
  "symbol": "OP/USD", "source": "watchlist", "price": 0.089,
  "rolling_7d_high": 0.089, "pullback_pct": 0, "floor_value": 0.08745,
  "floor_buffer_ok": false, "candle_count": 300, "maturity_ok": true,
  "volume_24h": null, "liquidity_ok": null, "regime_label": null,
  "regime_ok": null, "obi_value": null, "obi_ok": null,
  "flow_verdict": null, "flow_reason": null, "fired": false,
  "block_reason": "pullback_insufficient", "fleet": "solo_rider",
  "limit_price": null, "polled_price": null, "pct_below_high": null,
  "user_id": "unknown"
}
```

### rider_cycles
```json
{
  "id": 904, "cycle_id": "623dcada-0a9f-42bb-aae4-3aca16a56c74",
  "ts": "2026-08-10T03:36:09.296622+00:00", "universe_size": 1,
  "symbols_evaluated": 1, "halt_reason": null, "halt_at_symbol": null,
  "deployable_cash": 250, "bucket_usd": 250, "riders_open": 0,
  "fleet": "solo_rider", "user_id": "unknown"
}
```

### rider_health
```json
{
  "id": 278, "ts": "2026-08-10T03:36:09.016555+00:00",
  "component": "divergence_recorder", "status": "CYCLE",
  "detail": {"rows_written": 16, "universe_size": 16},
  "consecutive_failures": 0
}
{
  "id": 279, "ts": "2026-08-10T03:36:09.634561+00:00",
  "component": "solo_rider_engine_unknown", "status": "CYCLE_PAPER",
  "detail": {"mode": "paper", "asset": "OP/USD", "capital": 250.0,
             "user_id": "unknown", "riders_open": 0, "total_value": 250.0},
  "consecutive_failures": 0
}
```

### oracle_divergence
```json
{
  "id": 1072, "ts": "2026-08-10T03:36:08.304932+00:00",
  "symbol": "XRP/USD",
  "feed_id": "0x015852502f55534400000000000000000000000000",
  "oracle_value": 1.032506, "oracle_timestamp": 1786332965,
  "venue_value": 1.0324, "venue_timestamp": 1786332966442,
  "timestamp_gap_ms": 1442, "divergence_bps": 1.0267338240986
}
```

### rider_state (trimmed — real `ledger` is much larger; shape shown, not truncated dishonestly)
```json
{
  "id": 1,
  "ledger": {
    "system": "Prv1311-rider-team",
    "USD_balance": 10794.19, "treasury": 727.89, "fees_wallet": 66.30,
    "riders": {
      "AAVE/USD": {"asset": "AAVE/USD", "units": 18.54, "usd_in": 1666.67,
                   "entry_price": 89.88, "current_price": 91.42, "current_value": 1695.22}
    },
    "trade_history": [
      {"ts": "2026-08-01 14:42:34", "asset": "AAVE", "action": "BUY",
       "engine": "RIDER", "price": 89.88, "units": 18.54, "amount_usd": 1666.67}
    ]
  },
  "updated_at": "2026-08-04T03:24:03.583617+00:00"
}
```

### scav_state
```json
{
  "id": 1,
  "ledger": {
    "system": "Prv1311-scavengers",
    "USD_balance": 500.0, "treasury": 0.0, "fees_wallet": 0.0,
    "riders": {
      "UNI/USD": {"asset": "UNI/USD", "units": 128.84, "usd_in": 500.0,
                  "entry_price": 3.8809, "current_price": 3.91, "current_value": 503.75}
    },
    "trade_history": [
      {"ts": "2026-08-04 02:52:27", "asset": "UNI", "action": "BUY",
       "engine": "SCAVENGER", "price": 3.8809, "units": 128.84, "amount_usd": 500.0}
    ]
  },
  "updated_at": "2026-08-04T06:52:37.665968+00:00"
}
```

### solo_rider_config (only 1 row exists)
```json
{
  "user_id": "unknown", "asset": "OP/USD", "capital": 250,
  "status": "active", "updated_at": "2026-08-10T01:50:24.192359+00:00"
}
```

### solo_rider_state (only 1 row exists)
```json
{
  "user_id": "unknown",
  "ledger": {"riders": {}, "system": "Prv1311-rider-team", "treasury": 0.0,
             "USD_balance": 250.0, "fees_wallet": 0.0, "trade_history": []},
  "updated_at": "2026-08-10T01:50:25.505406+00:00"
}
```

Nothing credential-shaped appeared anywhere in this data — no redaction was
needed.

---

## 6. What the site must not do

> This app is a read-only mirror. The engine runs off-site as supervised
> services; nothing here computes a trading decision. No gate is ever
> evaluated in the browser. Measured outputs — prices, percentages, counts,
> verdicts already decided by the engine — are data, and may be displayed
> as-is. The constants that produce them — any threshold, percentage,
> lookback window, cutoff, or weighting used to make a gate decision — may
> not appear anywhere in visible copy, regardless of how that number is
> sourced or how confident the description sounds.
>
> The existing Undertow page mirrors gate thresholds client-side. That
> pattern is a known issue, not a template — it must not be extended to any
> new page.

---

*Compiled from live queries against the `cetoxlctgztvakobsojp` Supabase
project on 2026-08-10. No file in this repo, no running service, and no
Base44 platform state was touched to produce this document.*
