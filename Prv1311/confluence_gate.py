"""
================================================================================
PROJECT: Prv1311 — Confluence Gate (BTC/asset pre-entry filter above Markov)
FILE: confluence_gate.py
================================================================================
Ported from War-Room-Gate 3 "Score Trade Signal." Sits ABOVE the Markov engine:
before Markov fires, this scores whether momentum + flow + on-chain data CONFIRM
or CONFLICT with the price signal.

FOUR COMPONENTS (+/-25 each, clamped 0-100):
  A RSI-aligned          — REAL (computed from Coinbase candles)
  B exchange withdrawals — MISSING (needs on-chain feed; honestly flagged)
  C flow-aligned         — ETF (BTC) MISSING; funding (others) REAL via perp fetch
  D price-vs-MA          — REAL (from Coinbase daily closes)

HONESTY RULE (learned from the OBI dead-gate): components without a real data
source are NOT faked to a neutral default that silently passes. They score 0 and
are listed in `missing_sources`. The gate returns a PARTIAL, clearly-labeled
score. When an on-chain feed is wired later, those components light up
automatically — no rework.

DECISION BANDS: >=75 CONFIRMED/PASS, >=50 ACCUMULATION/CAUTION, >=25 CONFLICT/HOLD,
else DISTRIBUTION_RISK/HOLD. Plus the spec's override branches (only fire when
their required data is actually present).

PURE-ish: score_confluence(inputs) is pure. confluence_gate(sym, direction)
gathers what Coinbase/perp can give and calls it.
CONSTANTS (thresholds): exch_withdrawal_pct=-0.5, exch_deposit_pct=0.5,
  whale_elevated=60, etf_outflow=0, etf_inflow=0.
================================================================================
"""

import math
import ccxt
from config import QUOTE
from screener import exchange
from markov_screener import fetch_hourly

# --- default thresholds (operator-adjustable) ---
TH = {
    'exch_withdrawal_pct': -0.5,
    'exch_deposit_pct': 0.5,
    'whale_elevated': 60,
    'etf_outflow': 0,
    'etf_inflow': 0,
}

# isolated perp handle for funding (Coinbase spot has no funding rate).
# Fails safe: if it can't init or fetch, funding is treated as MISSING.
try:
    _perp = ccxt.binance({'options': {'defaultType': 'swap'}, 'enableRateLimit': True})
except Exception:
    _perp = None


def _rsi(closes, length=14):
    """Wilder RSI on a close series. None if too short."""
    n = len(closes)
    if n <= length:
        return None
    gains = losses = 0.0
    for i in range(1, length + 1):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    ag = gains / length
    al = losses / length
    for i in range(length + 1, n):
        ch = closes[i] - closes[i - 1]
        ag = (ag * (length - 1) + max(ch, 0.0)) / length
        al = (al * (length - 1) + max(-ch, 0.0)) / length
    if al == 0:
        return 100.0
    rs = ag / al
    return 100.0 - 100.0 / (1.0 + rs)


def _price_vs_ma(closes, ma_len=50):
    """Returns (price_vs_ma, ma_direction). 'above'/'below'/'at', 'up'/'down'/'flat'."""
    n = len(closes)
    if n < ma_len + 2:
        return 'at', 'flat'
    ma_now = sum(closes[-ma_len:]) / ma_len
    ma_prev = sum(closes[-ma_len - 1:-1]) / ma_len
    price = closes[-1]
    pvm = 'above' if price > ma_now else ('below' if price < ma_now else 'at')
    mad = 'up' if ma_now > ma_prev else ('down' if ma_now < ma_prev else 'flat')
    return pvm, mad


def _fetch_funding(sym):
    """Funding rate for a perp proxy (e.g. BTC/USDT:USDT). None if unavailable."""
    if _perp is None:
        return None
    base = sym.split('/')[0]
    for market in (f"{base}/USDT:USDT", f"{base}/USD:USD"):
        try:
            fr = _perp.fetch_funding_rate(market)
            rate = fr.get('fundingRate')
            if rate is not None:
                return float(rate)
        except Exception:
            continue
    return None


def score_confluence(inputs):
    """PURE FUNCTION. Faithful port of the JS scorer. inputs is a dict; any
    field that is None is treated as a MISSING source (scores 0, listed).
    Returns the full decision dict."""
    asset = (inputs.get('asset') or 'BTC').upper()
    flow_mode = inputs.get('flow_mode') or ('etf' if asset == 'BTC' else 'funding')
    direction = (inputs.get('direction') or 'long').lower()
    is_long = direction == 'long'

    missing = []

    def need(v, name, default):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            missing.append(name)
            return default
        return v

    rsi = float(need(inputs.get('rsi'), 'rsi', 50))
    ma_dir = str(need(inputs.get('ma_direction'), 'ma_direction', 'flat')).lower()
    price_vs_ma = str(need(inputs.get('price_vs_ma'), 'price_vs_ma', 'at')).lower()

    etf_flow = None
    funding = None
    if flow_mode == 'etf':
        etf_flow = need(inputs.get('etf_net_flow'), 'etf_net_flow', None)
        etf_flow = float(etf_flow) if etf_flow is not None else None
    else:
        funding = need(inputs.get('funding_rate'), 'funding_rate', None)
        funding = float(funding) if funding is not None else None

    exch_bal_chg = need(inputs.get('exchange_balance_pct'), 'exchange_balance_pct', None)
    exch_bal_chg = float(exch_bal_chg) if exch_bal_chg is not None else None
    whale_wd = need(inputs.get('whale_withdrawals'), 'whale_withdrawals', None)
    whale_wd = float(whale_wd) if whale_wd is not None else None

    # derived flags — only true when their data is actually present
    exch_withdrawals_high = (exch_bal_chg is not None and exch_bal_chg <= TH['exch_withdrawal_pct'])
    exch_deposits_rising = (exch_bal_chg is not None and exch_bal_chg >= TH['exch_deposit_pct'])
    whale_wd_high = (whale_wd is not None and whale_wd >= TH['whale_elevated'])

    if flow_mode == 'etf':
        flow_inflow = (etf_flow is not None and etf_flow > TH['etf_inflow'])
        flow_outflow = (etf_flow is not None and etf_flow < TH['etf_outflow'])
    else:
        flow_inflow = (funding is not None and funding > 0)
        flow_outflow = (funding is not None and funding < 0)

    # --- Component A: RSI momentum aligned ---
    rsi_aligned = (rsi >= 50) if is_long else (rsi <= 50)
    A = 25 if rsi_aligned else 0

    # --- Component B: exchange withdrawals elevated ---
    B = 25 if exch_withdrawals_high else 0

    # --- Component C: flow aligned (can go negative) ---
    if is_long:
        C = 25 if flow_inflow else (-25 if flow_outflow else 0)
    else:
        C = 25 if flow_outflow else (-25 if flow_inflow else 0)

    # --- Component D: price above/below MA in trade direction ---
    price_aligned = ((price_vs_ma == 'above' or ma_dir == 'up') if is_long
                     else (price_vs_ma == 'below' or ma_dir == 'down'))
    D = 25 if price_aligned else 0

    confluence_score = max(0, min(100, A + B + C + D))

    # --- decision bands ---
    if confluence_score >= 75:
        decision, gate = 'CONFIRMED', 'PASS'
    elif confluence_score >= 50:
        decision, gate = 'ACCUMULATION', 'CAUTION'
    elif confluence_score >= 25:
        decision, gate = 'CONFLICT', 'HOLD'
    else:
        decision, gate = 'DISTRIBUTION_RISK', 'HOLD'

    note = None
    # override branches — only fire when their data is present
    if flow_inflow and exch_deposits_rising:
        decision, gate = 'DISTRIBUTION_RISK', 'HOLD'
        note = 'Flow inflows + rising exchange deposits: distribution risk, no long entries.'
    elif flow_outflow and exch_deposits_rising:
        decision, gate = 'CONFLICT', 'HOLD'
        note = 'Flow outflows while coins entering exchanges: signal conflict, hold.'
    elif flow_outflow and (exch_withdrawals_high or whale_wd_high):
        decision, gate = 'ACCUMULATION', 'CAUTION'
        note = 'Flow outflows but coins leaving exchanges: cautious long bias only.'
    elif confluence_score >= 75 and rsi_aligned and price_aligned:
        decision, gate = 'CONFIRMED', 'PASS'
        note = 'All available signals aligned.'
    if not note:
        note = 'Decision from confluence score band.'

    return {
        'asset': asset, 'flow_mode': flow_mode, 'decision': decision,
        'gate': gate, 'confluence_score': confluence_score, 'direction': direction,
        'note': note,
        'components': {'rsi_aligned': A, 'exch_withdrawals': B,
                       'flow_aligned': C, 'price_vs_ma': D},
        'flags': {'exch_withdrawals_high': exch_withdrawals_high,
                  'exch_deposits_rising': exch_deposits_rising,
                  'whale_wd_high': whale_wd_high,
                  'flow_inflow': flow_inflow, 'flow_outflow': flow_outflow},
        'inputs_seen': {'rsi': rsi, 'ma_dir': ma_dir, 'price_vs_ma': price_vs_ma,
                        'etf_flow': etf_flow, 'funding': funding,
                        'exch_bal_chg': exch_bal_chg, 'whale_wd': whale_wd},
        'missing_sources': missing,
    }


def confluence_gate(sym, direction='long'):
    """Gathers the data Coinbase/perp CAN give for `sym`, calls score_confluence.
    On-chain components stay missing (honestly). Returns the decision dict."""
    base = sym.split('/')[0]
    inputs = {'asset': base, 'direction': direction}

    # daily closes -> RSI + price-vs-MA (real)
    pair = sym if '/' in sym else f"{sym}/{QUOTE}"
    try:
        ohlcv = exchange.fetch_ohlcv(pair, timeframe='1d', limit=120)
        closes = [row[4] for row in ohlcv] if ohlcv else []
    except Exception:
        closes = []
    if len(closes) > 14:
        inputs['rsi'] = _rsi(closes)
        pvm, mad = _price_vs_ma(closes)
        inputs['price_vs_ma'] = pvm
        inputs['ma_direction'] = mad

    # funding rate (real, for non-BTC via perp) — BTC uses etf mode (missing)
    if base != 'BTC':
        inputs['funding_rate'] = _fetch_funding(sym)

    # on-chain components left as None -> honestly flagged missing
    return score_confluence(inputs)


if __name__ == "__main__":
    for s in ['BTC/USD', 'ETH/USD', 'SOL/USD']:
        r = confluence_gate(s, 'long')
        print(f"{r['asset']:<5} score {r['confluence_score']:>3}  {r['decision']:<18} "
              f"gate {r['gate']:<8} missing={r['missing_sources']}")