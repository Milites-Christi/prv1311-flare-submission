"""
================================================================================
PROJECT: Prv1311 — Multi-Asset CORE + RIDER Engine
FILE: dashboard.py  (cleaned: rounded display, "Remaining Capital", tidy log)
================================================================================
Note: the LEDGER stores full-precision numbers; the DASHBOARD rounds for
readability only. Prices show 4 decimals (crypto is sub-dollar), dollars and
unit counts show 2.
================================================================================
"""

import streamlit as st
import json
import os
import pandas as pd
from config import STARTING_CAPITAL_USD, LADDER_SIZES, BASKET

NUM_RUNGS = len(LADDER_SIZES)
DATA_PATH = 'data/ledger.json'

BG, CARD, BORDER = "#12161A", "#1C2127", "#252b33"
TEXT, MUTED = "#e6e8eb", "#8a919c"
BLUE, GREEN, SLATE = "#5C99E6", "#4AA382", "#626D7A"

st.set_page_config(page_title="Prv1311", layout="wide", page_icon="⬡")

st.markdown(f"""
<style>
  .stApp {{ background-color:{BG}; color:{TEXT}; }}
  #MainMenu, footer, header {{ visibility:hidden; }}
  .block-container {{ padding-top:2rem; max-width:1100px; }}
  .prv-title {{ font-size:1.9rem; font-weight:800; letter-spacing:1px; color:#fff; }}
  .prv-sub {{ color:{MUTED}; font-size:0.95rem; margin-bottom:1.4rem; }}
  .card {{ background:{CARD}; border:1px solid {BORDER}; border-radius:14px;
          padding:18px 20px; margin-bottom:14px; }}
  .card-label {{ color:{MUTED}; font-size:0.75rem; text-transform:uppercase;
                letter-spacing:1px; margin-bottom:6px; }}
  .card-value {{ color:#fff; font-size:1.7rem; font-weight:700; }}
  .accent {{ color:{BLUE}; }} .green {{ color:{GREEN}; }} .slate {{ color:{SLATE}; }}
  .asset-card {{ background:{CARD}; border:1px solid {BORDER}; border-radius:14px;
                padding:16px 20px; margin-bottom:18px; }}
  .asset-head {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; }}
  .asset-name {{ font-size:1.25rem; font-weight:800; color:#fff; letter-spacing:1px; }}
  .asset-price {{ font-size:1.1rem; font-weight:700; color:{BLUE}; }}
  .engine-wrap {{ display:flex; gap:24px; }} .engine {{ flex:1; }}
  .engine-title {{ color:{MUTED}; font-size:0.72rem; text-transform:uppercase;
                  letter-spacing:1px; margin-bottom:8px; border-bottom:1px solid {BORDER}; padding-bottom:4px; }}
  .line {{ display:flex; justify-content:space-between; padding:4px 0; }}
  .line .k {{ color:{MUTED}; font-size:0.9rem; }} .line .v {{ color:{TEXT}; font-weight:600; font-size:0.9rem; }}
  .section-h {{ color:#fff; font-size:1.05rem; font-weight:700; margin:14px 0 8px 2px; }}
  .stButton>button {{ background:{BLUE}; color:#fff; border:none; border-radius:10px;
                     padding:8px 20px; font-weight:600; }}
  .stButton>button:hover {{ background:#6ba5ea; }}
</style>
""", unsafe_allow_html=True)


def tone(x):
    return "green" if x >= 0 else "slate"


def money(x):
    return f"${x:,.2f}"


def price(x):
    return f"${x:,.4f}"


def units(x):
    return f"{x:,.2f}"


def top_card(label, value, cls=""):
    return f'<div class="card"><div class="card-label">{label}</div><div class="card-value {cls}">{value}</div></div>'


st.markdown('<div class="prv-title">PRV1311 ⬡ MULTI-ASSET</div>', unsafe_allow_html=True)
st.markdown(f'<div class="prv-sub">Live paper-trading · {", ".join(s.split("/")[0] for s in BASKET)}</div>',
            unsafe_allow_html=True)

if not os.path.exists(DATA_PATH):
    st.error("Ledger not found. Run harness.py to generate it.")
    st.stop()

led = json.load(open(DATA_PATH))
capital = led.get('USD_balance', 0)
treasury = led.get('treasury', 0)
assets = led.get('assets', {})
history = led.get('trade_history', [])

prices = {}
try:
    from screener import fetch_live_price
    for sym in BASKET:
        prices[sym] = fetch_live_price(sym)
except Exception:
    prices = {sym: None for sym in BASKET}

asset_val = 0.0
invested = 0.0
for sym in BASKET:
    a = assets.get(sym, {})
    pr = prices.get(sym)
    if pr:
        asset_val += (a.get('core_asset', 0) + a.get('rider_asset', 0)) * pr
    invested += a.get('core_invested', 0) + a.get('rider_asset', 0) * a.get('rider_entry_price', 0)

total_value = capital + asset_val + treasury
unrealized = (asset_val - invested) if invested > 0 else 0.0

c1, c2, c3 = st.columns(3)
c1.markdown(top_card("Total Portfolio Value", money(total_value), tone(total_value - STARTING_CAPITAL_USD)), unsafe_allow_html=True)
c2.markdown(top_card("Unrealized P&L", money(unrealized), tone(unrealized)), unsafe_allow_html=True)
c3.markdown(top_card("Realized Profit", money(treasury), tone(treasury)), unsafe_allow_html=True)

st.markdown('<div class="section-h">Positions</div>', unsafe_allow_html=True)

for sym in BASKET:
    a = assets.get(sym, {})
    pr = prices.get(sym)
    tag = sym.split('/')[0]
    rung = a.get('current_rung', 0)
    core_avg = a.get('core_average_entry', 0)
    core_held = a.get('core_asset', 0)
    core_dep = a.get('core_invested', 0)
    rider_in = a.get('rider_holding', False)
    rider_entry = a.get('rider_entry_price', 0)
    rider_units = a.get('rider_asset', 0)

    price_str = price(pr) if pr else "—"
    status = '<span class="green">IN POSITION</span>' if rider_in else '<span class="slate">waiting</span>'

    html = f"""
    <div class="asset-card">
      <div class="asset-head">
        <span class="asset-name">{tag}</span>
        <span class="asset-price">{price_str}</span>
      </div>
      <div class="engine-wrap">
        <div class="engine">
          <div class="engine-title">CORE · Accumulator</div>
          <div class="line"><span class="k">Active Rung</span><span class="v accent">{rung} / {NUM_RUNGS}</span></div>
          <div class="line"><span class="k">Avg Cost</span><span class="v">{price(core_avg) if core_avg else '—'}</span></div>
          <div class="line"><span class="k">Units Held</span><span class="v">{units(core_held)}</span></div>
          <div class="line"><span class="k">Deployed</span><span class="v">{money(core_dep)}</span></div>
        </div>
        <div class="engine">
          <div class="engine-title">RIDER · Flipper</div>
          <div class="line"><span class="k">Status</span><span class="v">{status}</span></div>
          <div class="line"><span class="k">Entry</span><span class="v">{price(rider_entry) if rider_in else '—'}</span></div>
          <div class="line"><span class="k">Target +7%</span><span class="v">{price(rider_entry*1.07) if rider_in else '—'}</span></div>
          <div class="line"><span class="k">Units Held</span><span class="v">{units(rider_units) if rider_in else '0'}</span></div>
        </div>
      </div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)

st.markdown(top_card("Remaining Capital", money(capital)), unsafe_allow_html=True)

st.markdown('<div class="section-h">Trade Audit Log</div>', unsafe_allow_html=True)
if history:
    df = pd.DataFrame(history)
    # round for display; ledger keeps full precision
    if 'price' in df: df['price'] = df['price'].round(4)
    if 'amount_usd' in df: df['amount_usd'] = df['amount_usd'].round(2)
    if 'asset_amount' in df: df['asset_amount'] = df['asset_amount'].round(2)
    # tidy column names
    df = df.rename(columns={
        'timestamp': 'Time', 'symbol': 'Asset', 'engine': 'Engine',
        'action': 'Action', 'rung': 'Rung', 'price': 'Price',
        'amount_usd': 'USD', 'asset_amount': 'Units'
    })
    st.dataframe(df.iloc[::-1], use_container_width=True, hide_index=True)
else:
    st.info("No trades yet. Engines are watching the market.")

st.markdown("<br>", unsafe_allow_html=True)
if st.button("🔄 Refresh"):
    st.rerun()