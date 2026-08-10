"""
================================================================================
PROJECT: Prv1311 — Allocator Dashboard (bucket engine, Coinbase live)
FILE: alloc_dashboard.py
================================================================================
Reads data/alloc_ledger.json (the rotating-bucket engine) and shows: the 5 core
buckets, dip status, rider, on-deck queue, shadow ledger (opportunity cost), and
totals. Low-stress palette -- no red; losses show calm slate.
================================================================================
"""

import streamlit as st
import json
import os
import pandas as pd
from config import STARTING_CAPITAL_USD, QUOTE, NUM_CORE_BUCKETS

DATA_PATH = 'data/alloc_ledger.json'

BG, CARD, BORDER = "#12161A", "#1C2127", "#252b33"
TEXT, MUTED = "#e6e8eb", "#8a919c"
BLUE, GREEN, SLATE = "#5C99E6", "#4AA382", "#626D7A"

st.set_page_config(page_title="Prv1311 Allocator", layout="wide", page_icon="⬡")

st.markdown(f"""
<style>
  .stApp {{ background-color:{BG}; color:{TEXT}; }}
  #MainMenu, footer, header {{ visibility:hidden; }}
  .block-container {{ padding-top:2rem; max-width:1150px; }}
  .prv-title {{ font-size:1.9rem; font-weight:800; letter-spacing:1px; color:#fff; }}
  .prv-sub {{ color:{MUTED}; font-size:0.95rem; margin-bottom:1.4rem; }}
  .card {{ background:{CARD}; border:1px solid {BORDER}; border-radius:14px;
          padding:18px 20px; margin-bottom:14px; }}
  .card-label {{ color:{MUTED}; font-size:0.75rem; text-transform:uppercase;
                letter-spacing:1px; margin-bottom:6px; }}
  .card-value {{ color:#fff; font-size:1.7rem; font-weight:700; }}
  .accent {{ color:{BLUE}; }} .green {{ color:{GREEN}; }} .slate {{ color:{SLATE}; }}
  .bucket {{ background:{CARD}; border:1px solid {BORDER}; border-radius:12px;
            padding:14px 16px; margin-bottom:10px; }}
  .bkt-head {{ display:flex; justify-content:space-between; align-items:center; }}
  .bkt-id {{ color:{MUTED}; font-size:0.8rem; letter-spacing:1px; }}
  .bkt-asset {{ font-size:1.15rem; font-weight:800; color:#fff; }}
  .bkt-free {{ font-size:1.05rem; font-weight:700; color:{MUTED}; }}
  .line {{ display:flex; justify-content:space-between; padding:3px 0; font-size:0.88rem; }}
  .line .k {{ color:{MUTED}; }} .line .v {{ color:{TEXT}; font-weight:600; }}
  .section-h {{ color:#fff; font-size:1.05rem; font-weight:700; margin:16px 0 8px 2px; }}
  .stButton>button {{ background:{BLUE}; color:#fff; border:none; border-radius:10px;
                     padding:8px 20px; font-weight:600; }}
</style>
""", unsafe_allow_html=True)


def tone(x):
    return "green" if x >= 0 else "slate"


def money(x):
    return f"${x:,.2f}"


def top_card(label, value, cls=""):
    return f'<div class="card"><div class="card-label">{label}</div><div class="card-value {cls}">{value}</div></div>'


st.markdown('<div class="prv-title">PRV1311 ⬡ ROTATING ALLOCATOR</div>', unsafe_allow_html=True)

if not os.path.exists(DATA_PATH):
    st.error("Allocator ledger not found. Run: python allocator.py")
    st.stop()

s = json.load(open(DATA_PATH))
cash = s.get('USD_balance', 0)
treasury = s.get('treasury', 0)
buckets = s.get('core_buckets', [])
rider = s.get('rider', {})
queue = s.get('on_deck_queue', [])
shadow = s.get('shadow_ledger', [])
history = s.get('trade_history', [])

st.markdown(f'<div class="prv-sub">Live paper-trading · Coinbase · '
            f'{sum(1 for b in buckets if b.get("status")=="HOLDING")}/{NUM_CORE_BUCKETS} core buckets deployed</div>',
            unsafe_allow_html=True)

# live prices for held assets
prices = {}
try:
    from screener import fetch_live_price
    for b in buckets:
        if b.get('status') == 'HOLDING':
            prices[b['asset']] = fetch_live_price(f"{b['asset']}/{QUOTE}")
    if rider.get('holding'):
        prices[rider['asset']] = fetch_live_price(f"{rider['asset']}/{QUOTE}")
except Exception:
    pass

# totals
pos_val = 0.0
for b in buckets:
    if b.get('status') == 'HOLDING' and prices.get(b['asset']):
        pos_val += b['units'] * prices[b['asset']]
if rider.get('holding') and prices.get(rider['asset']):
    pos_val += rider['units'] * prices[rider['asset']]
total = cash + treasury + pos_val

c1, c2, c3 = st.columns(3)
c1.markdown(top_card("Total Value", money(total), tone(total - STARTING_CAPITAL_USD)), unsafe_allow_html=True)
c2.markdown(top_card("Realized (Treasury)", money(treasury), tone(treasury)), unsafe_allow_html=True)
c3.markdown(top_card("Remaining Capital", money(cash)), unsafe_allow_html=True)

# core buckets
st.markdown('<div class="section-h">Core Buckets (5)</div>', unsafe_allow_html=True)
for b in buckets:
    bid = b.get('id', '?')
    if b.get('status') == 'HOLDING':
        asset = b['asset']
        entry = b['entry_price']
        cur = prices.get(asset)
        pnl = (cur - entry) * b['units'] if cur else 0.0
        cur_str = f"${cur:,.4f}" if cur else "—"
        html = f"""
        <div class="bucket">
          <div class="bkt-head"><span class="bkt-asset">{asset}</span>
            <span class="bkt-id">BUCKET {bid}</span></div>
          <div class="line"><span class="k">Entry</span><span class="v">${entry:,.4f}</span></div>
          <div class="line"><span class="k">Current</span><span class="v">{cur_str}</span></div>
          <div class="line"><span class="k">Deployed</span><span class="v">${b['usd_in']:,.2f}</span></div>
          <div class="line"><span class="k">P&amp;L</span><span class="v {tone(pnl)}">${pnl:,.2f}</span></div>
          <div class="line"><span class="k">Dip bucket</span><span class="v">{'filled' if b.get('dip_filled') else 'armed'}</span></div>
        </div>"""
    else:
        html = f"""
        <div class="bucket">
          <div class="bkt-head"><span class="bkt-free">FREE</span>
            <span class="bkt-id">BUCKET {bid}</span></div>
          <div class="line"><span class="k">Status</span><span class="v slate">awaiting top-ranked signal</span></div>
        </div>"""
    st.markdown(html, unsafe_allow_html=True)

# rider
st.markdown('<div class="section-h">Rider</div>', unsafe_allow_html=True)
if rider.get('holding'):
    st.markdown(f'<div class="bucket"><div class="line"><span class="k">{rider["asset"]}</span>'
                f'<span class="v green">IN @ ${rider["entry_price"]:,.4f}</span></div></div>', unsafe_allow_html=True)
else:
    st.markdown('<div class="bucket"><div class="line"><span class="k">Status</span>'
                '<span class="v slate">waiting</span></div></div>', unsafe_allow_html=True)

# on-deck queue
st.markdown('<div class="section-h">On-Deck Queue (waiting for a freed bucket)</div>', unsafe_allow_html=True)
if queue:
    dfq = pd.DataFrame(queue)
    st.dataframe(dfq, use_container_width=True, hide_index=True)
else:
    st.info("Queue empty. Nothing waiting — buckets have capacity.")

# shadow ledger (opportunity cost)
st.markdown('<div class="section-h">Shadow Ledger (hard-locked signals — opportunity cost)</div>', unsafe_allow_html=True)
if shadow:
    dfs = pd.DataFrame(shadow)
    st.dataframe(dfs.iloc[::-1], use_container_width=True, hide_index=True)
else:
    st.info("No shadow entries. No signals have been hard-locked out yet.")

# trade history
st.markdown('<div class="section-h">Trade Log</div>', unsafe_allow_html=True)
if history:
    st.dataframe(pd.DataFrame(history).iloc[::-1], use_container_width=True, hide_index=True)
else:
    st.info("No trades yet.")

st.markdown("<br>", unsafe_allow_html=True)
if st.button("🔄 Refresh"):
    st.rerun()