# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Two related but independent Python projects for the same "never-cut accumulate + flip" crypto
strategy, at two different stages of maturity:

- **Root (`/`)** — the backtest research lab. Builds and measures the strategy against **historical**
  hourly candles (Binance.US, via `ccxt`). Nothing here trades or touches live money; everything is a
  standalone script you run to print a report.
- **`Prv1311/`** — the live-data evolution. Reimplements the same CORE/RIDER ideas against **live**
  Coinbase data, runs as long-lived paper-trading loops that persist state to JSON ledgers, and has
  Streamlit dashboards to watch them. It does **not** import from the root scripts — it has its own
  `config.py`, `screener.py`, etc. Treat the two directories as separate codebases that happen to share
  a strategy lineage, not a shared library.

## Commands

### Root

No package manifest exists at repo root — dependencies (`ccxt`, `requests`) are installed ad hoc, not
pinned. There is no test framework; verification happens two ways:
- Every file is runnable standalone (`python <file>.py`) and its `if __name__ == "__main__"` block is
  both a demo and a self-test — e.g. `python portfolio_state.py` runs a scoreboard math check against
  hand-computed expected values, printing "(expect ...)" next to each result.
- Measurement scripts derive tunables from real data rather than asserting pass/fail:
  `python measure_drawdown.py`, `python measure_pullbacks.py`, `python cheap_window.py`.

Typical invocations:
```
python scanner.py            # CoinGecko eligibility screen (which coins qualify)
python pipeline.py           # scanner eligibility + Binance.US data-availability, combined
python backtest.py           # the core day-by-day engine on XLM
python rider.py              # standalone RIDER engine on XLM
python portfolio.py          # CORE+RIDER on XLM+XRP+ALGO at once, shared treasury
python final_scorecard.py    # the capstone: locked config across 4 fixed market windows
python robustness_test.py    # same tuned config on XLM/XRP/ALGO, unmodified -- overfit check
```

### Prv1311

```
pip install -r Prv1311/requirements.txt   # streamlit, requests, truststore, ccxt, pandas
```

Scripts use plain `from config import ...` and relative paths (`data/ledger.json`), so **you must run
them with `Prv1311/` as the working directory**, not from repo root:
```
cd Prv1311
python harness.py            # live paper-trading loop (Ctrl+C to stop safely)
python allocator.py          # alternate live paper-trading loop
python rider_team.py         # alternate live paper-trading loop
streamlit run dashboard.py         # reads data/ledger.json
streamlit run alloc_dashboard.py   # reads data/alloc_ledger.json
streamlit run rider_dashboard.py   # reads data/rider_ledger.json
```
The three engines (`harness.py`, `allocator.py`, `rider_team.py`) are **parallel, independent**
allocation strategies over the same CORE/RIDER logic — they are not meant to run together, and each
writes to its own ledger file under `Prv1311/data/`.

## Architecture — root (backtest research)

Built as numbered "pieces" that stack on each other; later files import earlier ones:

- `fetch_history.py` — Piece 1: paginated hourly OHLCV pull from Binance.US via `ccxt`.
- `range_percentiles.py` — `percentile()` + `analyze_range`: 5th/95th percentile of closes over a
  window → `range_floor` / `range_top`.
- `weekly_spread.py` — `analyze_weekly_spread`: average weekly high-low spread → the exit-target
  offset (80% of spread).
- `backtest.py` — Piece 3: the day-by-day engine. `window_ending_at`/`analyze_window` roll a strict
  trailing 90-day window with no lookahead (the file calls this "the cardinal rule of backtesting").
  `entry_triggered` gates entries; `run_backtest` is the reference simulation loop wiring window logic
  to the state tracker.
- `portfolio_state.py` — Piece 2: the scoreboard. Pure state-mutation functions for CORE and TACTICAL
  slices (deploy/sell, average-entry math). Has no awareness of dates or market data — that separation
  is deliberate so the money math can be verified with fake numbers first.
- `rider.py` — the standalone RIDER strategy: buy on a pullback from the rolling 7-day high (if still
  above the range floor), flip at a fixed target, never sell at a loss.
- `trend_filter.py` — `trend_is_up`: 200-day moving-average regime gate, used to filter RIDER entries.
- `scout.py` — an alternate/experimental regime gate: `floor_is_falling` samples the 90-day floor over
  a recent window and checks for a monotonic decline (v2 fix for a v1 two-point comparison that lagged
  bears and false-triggered on recoveries).
- `scanner.py` — the CoinGecko-based **eligibility** screener (liquidity, stablecoin, low-float,
  manual denylist). Answers "which coins are quality enough to run this on", independent of timing.
- `pipeline.py` — joins `scanner.py`'s eligible basket with actual Binance.US data availability to
  produce the basket that's really backtestable.
- `portfolio.py` — runs CORE 6-2-1-1 + filtered RIDER on XLM/XRP/ALGO simultaneously with one shared
  treasury, specifically to test whether the three assets freeze independently (diversification works)
  or together (correlation kills it).
- Scorecards/experiments, each an alternate configuration measured across the same four fixed windows
  (bear→bull Oct'20–May'21, bull→bear May'21–Dec'21, calm bear 2022, calm bull/recovery Oct'24–Jan'25):
  `scorecard.py`, `scorecard_filtered.py`, `ladder_test.py`, `ladder_sweep.py`, `robustness_test.py`,
  `scout_test.py`, `final_scorecard.py` (the locked-in configuration).

**Load-bearing invariant:** nothing in this codebase ever sells at a loss. Every exit path in
`backtest.py` is guarded by an `in_profit` check; CORE and RIDER positions that go underwater simply
hold (a "bag") rather than cut. This is the strategy's core thesis, not a missing feature.

## Architecture — Prv1311 (live paper-trading)

Same CORE/RIDER concepts ported from Binance.US backtest data to **live** Coinbase data via `ccxt`.
Binance.US was abandoned for this stage due to near-zero live volume; Coinbase quotes in USD (not
USDT) and has no 4h candle granularity (6h substitutes for it throughout).

- `config.py` — central control panel: capital, ladder sizes/drops, RIDER pullback/target %,
  stablecoin/denylist excludes, liquidity floor, and the dashboard sidebar watchlist.
- `screener.py` — live data primitives: `fetch_live_price`, `calculate_90_day_floor` (the CORE trigger,
  gated by a 180-day maturity check that bans unseasoned coins system-wide), `rolling_7_day_high` (the
  RIDER reference), and `run_triple_confirmation` (the discovery gate, below).
- `dynamic_rsi.py`, `taker_absorption.py`, `vwap_bands.py` — the three "institutional" discovery
  signals feeding triple-confirmation: regime-adaptive RSI thresholds, order-flow absorption
  (reconstructed from individual trade sides, since Coinbase's klines lack a taker-volume column), and
  VWAP-band overextension.
- `rsi_scanner.py` — scans the full Coinbase `/USD` universe for RSI + taker-ratio + Bollinger-touch
  triple confirmation; "fires" feed into `ranking.py`.
- `ranking.py` — filters scanner fires through eligibility (liquidity/price/denylist/stablecoin) and
  scores survivors by a composite of RSI depth, taker ratio, band touch, and floor proximity.
- Three **parallel, independent** live engines sharing the CORE/RIDER logic but different allocation
  shapes — pick one per run, they are not designed to run concurrently against the same capital:
  - `harness.py` — pure mechanical CORE 6-2-1-1 ladder (ungated) across every liquid asset in the live
    universe; RIDER gated by pullback depth + floor buffer + a global concurrency cap. → `data/ledger.json`
  - `allocator.py` — rotating 12-bucket allocator (5 CORE + 5 dip-fill + 1 RIDER + 1 reserve) consuming
    `ranking.filter_and_rank()`; hard-locks when buckets are saturated, queues overflow candidates, and
    tracks a shadow ledger of what it would have made. → `data/alloc_ledger.json`
  - `rider_team.py` — a decoupled, RIDER-only engine: up to 10 independent riders across the live
    universe, with 2 buckets always held back as untouchable reserve. → `data/rider_ledger.json`
- Matching Streamlit dashboards, each read-only against one ledger: `dashboard.py`, `alloc_dashboard.py`,
  `rider_dashboard.py` (+ `token_directory.py`, a CoinGecko-backed sidebar with fetch-on-click token
  profiles, cached to `token_id_cache.json`).
- `probe2.py`, `probe_coinbase.py`, `probe_data.py` — throwaway scripts used to decide Binance.US vs.
  Coinbase data availability; not part of the live strategy.
- `rider_dashboard.backup.py` — a snapshot backup, not imported anywhere.
