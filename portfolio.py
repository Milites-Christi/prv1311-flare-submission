"""
portfolio.py

MULTI-ASSET portfolio: the proven system (CORE 6-2-1-1 + filtered RIDER) run
on 3 assets at once, equal thirds of capital, ONE shared treasury.

The thesis: when one asset is frozen in a bear, the others keep cycling, so
the whole vehicle keeps breathing. THE HONEST TEST: did the assets freeze
independently (diversification works) or all together (crypto correlation
kills the benefit)? This measures that directly.

Each asset runs sized to 1/3 of the $20k vehicle.
"""

from datetime import datetime, timezone, date as _date
from fetch_history import fetch_hourly_candles
from backtest import (candles_to_daily, window_ending_at, analyze_window,
                      entry_triggered)
from rider import rolling_high, PULLBACK_PCT, TARGET_PCT
from trend_filter import trend_is_up

# Each asset gets 1/3 of the $20k. Its 12 slices are sized off that third.
ASSET_CAPITAL = 20000 / 3           # ~$6,666 per asset
CORE_SLICE = (ASSET_CAPITAL * 10/12) / 10   # 10 core slices share 10/12 of the third
RIDER_SLICE = ASSET_CAPITAL * 1/12          # rider = 1 slice = 1/12 of the third
# (reserve is the remaining 1/12, folded into the ladder)
SLICE = ASSET_CAPITAL / 12          # simplest: one slice = 1/12 of the third

CORE_BITES = [6, 2, 1, 1]
CORE_DEPTHS = [0.00, 0.20, 0.40, 0.60]

ASSETS = ["XLM/USDT", "XRP/USDT", "ALGO/USDT"]


def idx_on_or_after(daily, y, m, d):
    target = _date(y, m, d)
    for idx, row in enumerate(daily):
        if datetime.fromtimestamp(row["timestamp"] / 1000, tz=timezone.utc).date() >= target:
            return idx
    return len(daily) - 1


class AssetEngine:
    """One asset's full CORE+RIDER state, sized to 1/3 of the vehicle."""
    def __init__(self, name):
        self.name = name
        # CORE
        self.core_units = 0.0
        self.core_usd = 0.0
        self.core_holding = False
        self.core_first = None
        self.core_rungs = 0
        # RIDER
        self.r_holding = False
        self.r_entry = 0.0
        self.r_units = 0.0

    def core_avg(self):
        return self.core_usd / self.core_units if self.core_units > 0 else None

    def step(self, daily, i):
        """Run one day for this asset. Returns realized profit booked today."""
        booked = 0.0
        price = daily[i]["close"]
        res = analyze_window(window_ending_at(daily, i))
        if res is None:
            return booked
        floor, top, spread = res["range_floor"], res["range_top"], res["avg_weekly_spread"]

        # ---- CORE 6-2-1-1 ----
        if not self.core_holding:
            if entry_triggered(price, floor):
                usd = SLICE * CORE_BITES[0]
                self.core_units += usd / price
                self.core_usd += usd
                self.core_holding = True
                self.core_first = price
                self.core_rungs = 1
        else:
            avg = self.core_avg()
            target = avg + 0.80 * spread if (avg and spread) else None
            in_profit = avg is not None and price > avg
            if in_profit and target is not None and price >= target:
                booked += self.core_units * price - self.core_usd
                self._reset_core()
            elif in_profit and price > top:
                booked += self.core_units * price - self.core_usd
                self._reset_core()
            elif self.core_rungs < len(CORE_BITES) and self.core_first:
                rung = self.core_first * (1 - CORE_DEPTHS[self.core_rungs])
                if price <= rung:
                    usd = SLICE * CORE_BITES[self.core_rungs]
                    self.core_units += usd / price
                    self.core_usd += usd
                    self.core_rungs += 1

        # ---- RIDER (filtered) ----
        if not self.r_holding:
            hi7 = rolling_high(daily, i)
            drop = (hi7 - price) / hi7 * 100.0 if hi7 > 0 else 0.0
            if drop >= PULLBACK_PCT:
                if not ((floor and price < floor) or (not trend_is_up(daily, i))):
                    self.r_holding = True
                    self.r_entry = price
                    self.r_units = SLICE / price
        else:
            if daily[i]["high"] >= self.r_entry * (1 + TARGET_PCT / 100.0):
                booked += self.r_units * (self.r_entry * (1 + TARGET_PCT/100.0)) - SLICE
                self.r_holding = False

        return booked

    def _reset_core(self):
        self.core_units = 0.0
        self.core_usd = 0.0
        self.core_holding = False
        self.core_first = None
        self.core_rungs = 0

    def open_pl(self, price):
        core = self.core_units * price - self.core_usd if self.core_holding else 0.0
        rider = self.r_units * price - SLICE if self.r_holding else 0.0
        return core + rider

    def is_frozen(self, price):
        """Frozen = holding an underwater CORE position (the bear-freeze state)."""
        return self.core_holding and (self.core_units * price - self.core_usd) < 0


def run_portfolio(data, start_map, end_map):
    engines = {sym: AssetEngine(sym) for sym in ASSETS}
    treasury = 0.0

    # align on a shared calendar: step by the union of dates present in all assets
    # simple approach: iterate each asset over its own window, but track a shared
    # "all frozen at once" flag by date.
    # We build a date->frozen-count map to detect simultaneous freezes.
    frozen_by_date = {}

    for sym in ASSETS:
        daily = data[sym]
        s, e = start_map[sym], end_map[sym]
        eng = engines[sym]
        for i in range(max(s, 400), e):
            treasury += eng.step(daily, i)
            price = daily[i]["close"]
            d = datetime.fromtimestamp(daily[i]["timestamp"]/1000, tz=timezone.utc).date()
            if eng.is_frozen(price):
                frozen_by_date[d] = frozen_by_date.get(d, 0) + 1

    # open P&L at end of each asset's window
    open_total = 0.0
    per_asset = {}
    for sym in ASSETS:
        daily = data[sym]
        e = end_map[sym]
        last = daily[e-1]["close"]
        op = engines[sym].open_pl(last)
        open_total += op
        per_asset[sym] = op

    # how often were ALL THREE frozen simultaneously?
    all_frozen_days = sum(1 for c in frozen_by_date.values() if c == len(ASSETS))
    any_frozen_days = len(frozen_by_date)

    return {
        "realized": treasury,
        "open": open_total,
        "per_asset_open": per_asset,
        "all_frozen_days": all_frozen_days,
        "any_frozen_days": any_frozen_days,
    }


WINDOWS = [
    ("Bear->Bull (Oct'20-May'21)", (2020, 10, 1), (2021, 5, 20)),
    ("Bull->Bear (May'21-Dec'21)", (2021, 5, 20), (2021, 12, 15)),
    ("Calm Bear  (Jan'22-Dec'22)", (2022, 1, 1),  (2022, 12, 31)),
    ("Calm Bull  (Oct'24-Jan'25)", (2024, 10, 1), (2025, 1, 31)),
]


if __name__ == "__main__":
    data = {}
    for sym in ASSETS:
        print(f"Loading {sym} ...")
        data[sym] = candles_to_daily(fetch_hourly_candles(symbol=sym, since_year=2019))
    print()

    print("=" * 84)
    print("MULTI-ASSET PORTFOLIO -- XLM+XRP+ALGO, equal thirds, shared treasury")
    print("proven system unchanged (CORE 6-2-1-1 + filtered RIDER)")
    print("=" * 84)

    for label, s, e in WINDOWS:
        start_map = {sym: idx_on_or_after(data[sym], *s) for sym in ASSETS}
        end_map = {sym: idx_on_or_after(data[sym], *e) for sym in ASSETS}
        r = run_portfolio(data, start_map, end_map)

        print(f"\n{label}")
        print("-" * 84)
        print(f"  Realized (banked, all 3 pooled): ${r['realized']:>10.2f}")
        print(f"  Open (paper, all 3):             ${r['open']:>10.2f}")
        print(f"  TOTAL:                           ${r['realized'] + r['open']:>10.2f}")
        print(f"  Per-asset open: " + "  ".join(
            f"{sym.split('/')[0]} ${v:>8.0f}" for sym, v in r['per_asset_open'].items()))
        print(f"  Days ANY asset frozen: {r['any_frozen_days']}   "
              f"Days ALL THREE frozen at once: {r['all_frozen_days']}")

    print("\n" + "=" * 84)
    print("THE DIVERSIFICATION TEST: 'ALL THREE frozen at once' near 0 = they froze")
    print("independently, diversification WORKS. If it's high = they bear together,")
    print("crypto correlation, diversification helps less than hoped. Read it straight.")
    print("=" * 84)