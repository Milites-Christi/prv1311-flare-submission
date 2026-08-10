"""
================================================================================
PROJECT: Prv1311 — Bot Sizing (risk-based position sizing, the shared sizer)
FILE: bot_sizing.py
================================================================================
THIS IS A PIECE — a shared sizing function every engine can import. Ported from
War-Room "Calculate Trade Parameters" (BOT/Holds), with the LIVE-MONEY danger
REMOVED.

WHAT IT DOES: risk-based position sizing.
    risk_amount = equity * risk_pct
    size (units) = risk_amount / |entry - stop|
    a tight stop -> larger size; a wide stop -> smaller size.

SAFETY CHANGES FROM THE ORIGINAL (deliberate):
  - LEVERAGE FORCED TO 1. The source defaulted leverage=3 and flagged it
    "ASSUMPTION, not decided." This system is SPOT, NO LEVERAGE. Never >1 here.
  - NO LIVE ACCOUNT. The source read live Hyperliquid equity and was "staged,
    halted, never armed." This function takes equity as a plain number (paper
    equity from a fleet ledger). It NEVER connects to a live account. Wiring to
    real money is a separate, later, lawyer-gated step — not this file.
  - FAILS CLOSED. Zero/negative equity or a non-positive stop distance -> size 0.
    Never fabricate equity, never return a size on bad input.

HANDLING NEVER-CUT FLEETS (no stop): riders/scavengers/dogs never sell at a loss,
so they have no stop price. For them, pass stop=None and a max_adverse_pct (how
far below entry you treat as the practical risk distance for sizing math only —
this is NOT a real stop, nothing sells there). Markov passes its real stop.

RETURNS a dict: {size, notional, risk_amount, risk_pct, leverage, equity, basis}.
================================================================================
"""

# --- tunable constants ---
DEFAULT_RISK_PCT = 0.01     # risk 1% of equity per trade (from source)
LEVERAGE = 1                # SPOT ONLY. Do not change. No leverage in this system.


def size_position(equity, entry_price, stop_price=None,
                  risk_pct=DEFAULT_RISK_PCT, max_adverse_pct=None,
                  bucket_cap=None):
    """PURE FUNCTION. Risk-based size.

    equity        : account/pool value (paper). <=0 -> size 0 (fails closed).
    entry_price   : intended entry.
    stop_price    : real stop (Markov). None for never-cut fleets.
    risk_pct      : fraction of equity to risk (default 1%).
    max_adverse_pct: for never-cut fleets with no stop, the synthetic risk
                     distance as a fraction of entry (e.g. 0.10 = treat 10%
                     below entry as the risk distance FOR SIZING ONLY — nothing
                     sells there). Ignored if stop_price is given.
    bucket_cap    : optional hard $ ceiling on notional (e.g. RIDER_CEILING).

    Returns {size, notional, risk_amount, risk_pct, leverage, equity, basis}.
    """
    out = {'size': 0.0, 'notional': 0.0, 'risk_amount': 0.0,
           'risk_pct': risk_pct, 'leverage': LEVERAGE,
           'equity': equity, 'basis': None}

    # fail closed on bad equity/price
    try:
        equity = float(equity)
        entry_price = float(entry_price)
    except (TypeError, ValueError):
        out['basis'] = 'invalid_input'
        return out
    if equity <= 0 or entry_price <= 0:
        out['basis'] = 'zero_or_negative_equity'
        return out

    # determine the risk distance
    if stop_price is not None:
        try:
            stop_price = float(stop_price)
        except (TypeError, ValueError):
            out['basis'] = 'invalid_stop'
            return out
        price_diff = abs(entry_price - stop_price)
        basis = 'real_stop'
    elif max_adverse_pct is not None and max_adverse_pct > 0:
        price_diff = entry_price * max_adverse_pct
        basis = f'synthetic_{max_adverse_pct:.2%}'
    else:
        out['basis'] = 'no_stop_or_max_adverse'
        return out

    if price_diff <= 0:
        out['basis'] = 'nonpositive_risk_distance'
        return out

    risk_amount = equity * risk_pct
    size = risk_amount / price_diff              # units
    notional = size * entry_price                # $ deployed (leverage = 1)

    # optional hard cap on dollars deployed
    if bucket_cap is not None and notional > bucket_cap:
        notional = float(bucket_cap)
        size = notional / entry_price

    out.update({
        'size': round(size, 8),
        'notional': round(notional, 2),
        'risk_amount': round(risk_amount, 2),
        'basis': basis,
    })
    return out


if __name__ == "__main__":
    # quick sanity checks
    print("Markov-style (real stop):")
    print("  ", size_position(equity=2000, entry_price=100, stop_price=95))
    print("Never-cut fleet (synthetic 10% risk distance):")
    print("  ", size_position(equity=2000, entry_price=100, max_adverse_pct=0.10,
                              bucket_cap=500))
    print("Fails closed (zero equity):")
    print("  ", size_position(equity=0, entry_price=100, stop_price=95))