"""
portfolio_state.py

Piece 2 of the backtest: the state tracker (the "scoreboard").

Two independent games write to this scoreboard:
  - CORE:     the patient accumulator (up to 10 slices + 1 reserve)
  - TACTICAL: the fast flipper (1 slice, cycles in and out)
Both deposit profit into ONE shared pot: the treasury.

This file holds the scoreboard AND the rules for changing it. It does NOT
know about real market data, dates, or exit triggers -- that's Piece 3's job.
Here we only answer: "given a buy or a sell, how does the scoreboard change?"

We build each rule as a small function and test it with FAKE numbers at the
bottom, so we confirm the math before it ever touches real candles.
"""

# --- Tunable constants ---
SLICE_USD = 1666.67          # dollars per slice ($20,000 / 12)
CORE_MAX_SLICES = 10         # CORE deploys all 10 at entry
RESERVE_SLICES = 1           # 1 dip-reserve slice, fires once on a floor break


def new_state():
    """
    Creates a fresh scoreboard with everything flat (no positions open).

    Returns a dict. Reading it in plain English:
      CORE is flat, holds 0 units, deployed $0, average entry undefined,
      reserve not yet used. TACTICAL is flat. Treasury (realized profit) is $0.
    """
    return {
        # ----- CORE game -----
        "core_holding": False,        # are we currently in a CORE position?
        "core_units": 0.0,            # total units (coins) CORE holds
        "core_usd_in": 0.0,           # total dollars CORE has deployed
        "core_slices": 0,             # how many of the 10 slices are deployed
        "core_reserve_used": False,   # has the 1 reserve slice been spent?

        # ----- TACTICAL game -----
        "tac_holding": False,         # are we currently in a TACTICAL position?
        "tac_units": 0.0,             # units TACTICAL holds
        "tac_usd_in": 0.0,            # dollars TACTICAL deployed
        "tac_entry_price": None,      # price TACTICAL bought at (for the +7% target)

        # ----- shared -----
        "treasury": 0.0,              # realized profit, all games pooled
    }


def core_average_entry(state):
    """
    The average price CORE paid per unit = total dollars in / total units held.
    Returns None if CORE holds nothing (avoids dividing by zero).

    This is THE key number: every CORE exit target is measured from here.
    """
    if state["core_units"] <= 0:
        return None
    return state["core_usd_in"] / state["core_units"]


def core_deploy(state, price, num_slices):
    """
    CORE buys `num_slices` slices at `price`.

    THE IMPORTANT MATH -- average entry:
    When you buy more units at a new price, your average entry price shifts.
    We do NOT average the two PRICES together (that would be wrong -- it
    ignores how many units each buy got). Instead we track total-dollars-in
    and total-units, and divide. That naturally weights each buy by its size.

    Example: buy $1000 at $0.10 (=10,000 units), then $1000 at $0.05
    (=20,000 units). Average is NOT (0.10+0.05)/2 = $0.075. It's
    $2000 / 30,000 units = $0.0667 -- lower, because the cheaper buy got
    more units. That's the accumulation edge, and this math captures it.
    """
    usd = SLICE_USD * num_slices          # dollars we're spending now
    units = usd / price                   # units that buys at this price

    state["core_units"] += units          # add to our pile of units
    state["core_usd_in"] += usd           # add to dollars deployed
    state["core_slices"] += num_slices    # track how many slices are in
    state["core_holding"] = True
    return state


def core_deploy_reserve(state, price):
    """
    Deploy the single dip-reserve slice (only ever once). Same math as a
    normal CORE buy -- it just also flips the reserve_used flag so it can't
    fire twice.
    """
    core_deploy(state, price, RESERVE_SLICES)
    state["core_reserve_used"] = True
    return state


def core_sell_all(state, price):
    """
    CORE sells its ENTIRE position at `price`. Realizes the profit (or holds
    the loss -- but by our rules CORE only ever sells in profit), adds it to
    the treasury, and resets CORE back to flat.

    Profit = what we sell for  minus  what we put in.
      sell value = units * price
      cost       = usd deployed
    """
    sell_value = state["core_units"] * price
    profit = sell_value - state["core_usd_in"]

    state["treasury"] += profit

    # reset CORE to flat
    state["core_holding"] = False
    state["core_units"] = 0.0
    state["core_usd_in"] = 0.0
    state["core_slices"] = 0
    state["core_reserve_used"] = False
    return state, profit


def tac_deploy(state, price):
    """
    TACTICAL buys its single slice at `price`. Records the entry price so
    Piece 3 can later check for the +7% target.
    """
    usd = SLICE_USD
    units = usd / price

    state["tac_units"] = units
    state["tac_usd_in"] = usd
    state["tac_entry_price"] = price
    state["tac_holding"] = True
    return state


def tac_sell(state, price):
    """
    TACTICAL sells its slice at `price`, banks the profit to the treasury,
    and resets TACTICAL to flat so it can cycle again.
    """
    sell_value = state["tac_units"] * price
    profit = sell_value - state["tac_usd_in"]

    state["treasury"] += profit

    state["tac_holding"] = False
    state["tac_units"] = 0.0
    state["tac_usd_in"] = 0.0
    state["tac_entry_price"] = None
    return state, profit


# ============================================================
# SELF-TEST with fake numbers. Run this file directly to check
# the math before it's ever wired to real data.
# ============================================================
if __name__ == "__main__":
    print("=== State tracker self-test (fake numbers) ===\n")

    s = new_state()
    print("Fresh state -- core_holding should be False:", s["core_holding"])

    # CORE buys all 10 slices at $0.15
    core_deploy(s, price=0.15, num_slices=10)
    print(f"\nAfter CORE buys 10 slices at $0.15:")
    print(f"  slices deployed: {s['core_slices']} (expect 10)")
    print(f"  usd in: ${s['core_usd_in']:.2f} (expect ~16666.70)")
    print(f"  units: {s['core_units']:.2f}")
    print(f"  avg entry: ${core_average_entry(s):.4f} (expect 0.1500)")

    # Price dips to $0.12 -> deploy the reserve slice
    core_deploy_reserve(s, price=0.12)
    print(f"\nAfter reserve slice at $0.12:")
    print(f"  reserve used: {s['core_reserve_used']} (expect True)")
    print(f"  avg entry: ${core_average_entry(s):.4f} (expect LOWER than 0.1500)")

    # CORE sells everything at $0.18 (in profit)
    s, core_profit = core_sell_all(s, price=0.18)
    print(f"\nAfter CORE sells all at $0.18:")
    print(f"  realized profit: ${core_profit:.2f} (expect positive)")
    print(f"  core_holding reset: {s['core_holding']} (expect False)")
    print(f"  treasury: ${s['treasury']:.2f}")

    # TACTICAL buys at $0.15, sells at +7% (~$0.1605)
    tac_deploy(s, price=0.15)
    s, tac_profit = tac_sell(s, price=0.15 * 1.07)
    print(f"\nAfter TACTICAL +7% flip:")
    print(f"  tac profit: ${tac_profit:.2f} (expect ~116.67, i.e. 7% of 1666.67)")
    print(f"  treasury now: ${s['treasury']:.2f}")

    print("\n=== If those numbers look right, the scoreboard math is sound. ===")