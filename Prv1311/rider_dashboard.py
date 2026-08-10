"""
================================================================================
PROJECT: Prv1311 — Rider Team Dashboard
FILE: rider_dashboard.py
================================================================================
Reads data/rider_ledger.json. All 10 rider slots as uniform bubble cards.
Filled: Rider# / TOTAL CAPITAL / amount / asset / stats. Empty: Rider# /
TOTAL CAPITAL / bucket amount / "waiting for opportunity". Low-stress palette.
================================================================================
"""

import streamlit as st
import json
import os
import token_directory
from config import (STARTING_CAPITAL_USD, QUOTE, BUCKET_USD,
                    MAX_ACTIVE_RIDERS, RESERVE_BUCKETS, RIDER_LEDGER_FILE,
                    RIDER_TARGET_PCT)

RESERVE_USD = BUCKET_USD * RESERVE_BUCKETS

BG, CARD, BORDER = "#12161A", "#1C2127", "#252b33"
TEXT, MUTED = "#e6e8eb", "#8a919c"
BLUE, GREEN, SLATE = "#5C99E6", "#4AA382", "#626D7A"
NEON_GREEN, BROWN = "#3ECF8E", "#C08552"

st.set_page_config(page_title="Prv1311 Rider Team", layout="wide", page_icon="⬡")

token_directory.render_sidebar()

st.markdown(f"""
<style>
  .stApp {{ background-color:{BG}; color:{TEXT}; }}
  #MainMenu, footer, header {{ visibility:hidden; }}
  .block-container {{ padding-top:2rem; max-width:1200px; margin-left:auto; margin-right:auto; }}
  [data-testid="stMain"] [data-testid="stHorizontalBlock"] {{
    flex-wrap: nowrap !important;
    gap: 14px;
  }}
  [data-testid="stMain"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {{
    flex: 1 1 0 !important;
    min-width: 0 !important;
    width: auto !important;
  }}
  .prv-title {{ font-size:2.1rem; font-weight:800; letter-spacing:1px; color:#fff; text-align:center; }}
  .prv-hex {{ font-size:3rem; color:#0A6CFF; text-shadow:0 0 14px rgba(10,108,255,0.65); }}
  .prv-letters {{ color:#FFB84D; }}
  .prv-numbers {{ color:{NEON_GREEN}; }}
  .prv-sub {{ color:{MUTED}; font-size:0.95rem; margin-bottom:1.2rem; }}
  .card {{ background:{CARD}; border:1px solid {BORDER}; border-radius:14px;
          padding:16px 20px; margin-bottom:12px; box-shadow:0 6px 16px rgba(0,0,0,0.35); }}
  .card-label {{ color:{MUTED}; font-size:0.72rem; text-transform:uppercase;
                letter-spacing:1px; margin-bottom:6px; }}
  .card-value {{ color:#fff; font-size:1.6rem; font-weight:700; }}
  .accent {{ color:{BLUE}; }} .green {{ color:{GREEN}; }} .slate {{ color:{SLATE}; }}
  .neon-green {{ color:{NEON_GREEN}; }} .brown {{ color:{BROWN}; }}
  .reserve-badge {{ display:inline-block; background:rgba(92,153,230,0.12);
                   border:1px solid {BLUE}; color:{BLUE}; border-radius:20px;
                   padding:6px 16px; font-size:0.85rem; font-weight:600; }}
  .bubble {{ background:{CARD}; border:1px solid {BORDER}; border-radius:18px;
            padding:16px 18px; margin-bottom:14px; min-height:210px;
            box-shadow:0 6px 16px rgba(0,0,0,0.35); }}
  .bubble-empty {{ background:{CARD}; border:1px dashed {BORDER}; opacity:0.75;
                  border-radius:18px; padding:16px 18px; margin-bottom:14px; min-height:210px;
                  box-shadow:0 4px 12px rgba(0,0,0,0.25); }}
  .bub-head {{ font-size:0.8rem; color:{MUTED}; text-transform:uppercase;
              letter-spacing:1px; margin-bottom:10px; }}
  .bub-cap-label {{ font-size:0.68rem; color:{MUTED}; text-transform:uppercase;
                   letter-spacing:1px; margin-bottom:2px; }}
  .bub-cap {{ font-size:1.4rem; font-weight:800; color:{GREEN}; margin-bottom:12px; }}
  .bub-asset {{ font-size:1.15rem; font-weight:700; color:{BLUE}; margin-bottom:2px; }}
  .bub-row {{ display:flex; justify-content:space-between; padding:3px 0; font-size:0.85rem; }}
  .bub-row .k {{ color:{MUTED}; }} .bub-row .v {{ color:{TEXT}; font-weight:600; }}
  .bub-waiting {{ color:{SLATE}; font-size:1.0rem; font-weight:600; margin-top:6px; }}
  .stButton>button {{ background:{BLUE}; color:#fff; border:none; border-radius:10px;
                     padding:8px 20px; font-weight:600; }}
</style>
""", unsafe_allow_html=True)


def money(x):
    return f"${x:,.2f}"


st.markdown(
    '<div class="prv-title">'
    '<span class="prv-letters">PRV</span><span class="prv-numbers">1311</span> '
    '<span class="prv-hex">⬡</span> RIDER TEAM</div>',
    unsafe_allow_html=True,
)

if not os.path.exists(RIDER_LEDGER_FILE):
    st.error("Rider ledger not found. Run: python rider_team.py")
    st.stop()

s = json.load(open(RIDER_LEDGER_FILE))
cash = s.get('USD_balance', 0)
treasury = s.get('treasury', 0)
riders = s.get('riders', {})

prices = {}
try:
    from screener import fetch_live_price
    for sym in riders:
        prices[sym] = fetch_live_price(sym)
except Exception:
    pass

held_val = 0.0
for sym, r in riders.items():
    pr = prices.get(sym)
    if pr:
        held_val += r['units'] * pr
total = cash + held_val + treasury

st.markdown(f'<div class="prv-sub">Live paper-trading · Coinbase · '
            f'{len(riders)}/{MAX_ACTIVE_RIDERS} riders active · '
            f'<span class="reserve-badge">🛡 Reserve {money(RESERVE_USD)} protected</span></div>',
            unsafe_allow_html=True)

c1, c2, c3 = st.columns(3)
c1.markdown(f'<div class="card"><div class="card-label">Total Value</div>'
            f'<div class="card-value accent">{money(total)}</div></div>', unsafe_allow_html=True)
c2.markdown(f'<div class="card"><div class="card-label">Realized (Treasury)</div>'
            f'<div class="card-value accent">{money(treasury)}</div></div>', unsafe_allow_html=True)
c3.markdown(f'<div class="card"><div class="card-label">Cash</div>'
            f'<div class="card-value neon-green">{money(cash)}</div></div>', unsafe_allow_html=True)

st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)

rider_list = list(riders.items())
cols_per_row = 5

for row_start in range(0, MAX_ACTIVE_RIDERS, cols_per_row):
    cols = st.columns(cols_per_row)
    for i in range(cols_per_row):
        slot = row_start + i
        if slot >= MAX_ACTIVE_RIDERS:
            break
        with cols[i]:
            if slot < len(rider_list):
                sym, r = rider_list[slot]
                tag = sym.split('/')[0]
                entry = r['entry_price']
                units = r['units']
                usd_in = r['usd_in']
                pr = prices.get(sym)
                cur_val = units * pr if pr else usd_in
                pnl = cur_val - usd_in
                target = entry * (1 + RIDER_TARGET_PCT / 100.0)
                cur_str = f"${pr:,.4f}" if pr else "—"
                html = f"""
                <div class="bubble">
                  <div class="bub-head">Rider {slot+1}</div>
                  <div class="bub-cap-label">Total Capital</div>
                  <div class="bub-cap">{money(cur_val)}</div>
                  <div class="bub-asset">{tag}</div>
                  <div class="bub-row"><span class="k">Entry</span><span class="v neon-green">${entry:,.4f}</span></div>
                  <div class="bub-row"><span class="k">Current</span><span class="v green">{cur_str}</span></div>
                  <div class="bub-row"><span class="k">Units</span><span class="v brown">{units:,.2f}</span></div>
                  <div class="bub-row"><span class="k">Target +{RIDER_TARGET_PCT:.0f}%</span><span class="v neon-green">${target:,.4f}</span></div>
                  <div class="bub-row"><span class="k">P&amp;L</span><span class="v accent">{money(pnl)}</span></div>
                </div>"""
                st.markdown(html, unsafe_allow_html=True)
            else:
                html = f"""
                <div class="bubble-empty">
                  <div class="bub-head">Rider {slot+1}</div>
                  <div class="bub-cap-label">Total Capital</div>
                  <div class="bub-cap">{money(BUCKET_USD)}</div>
                  <div class="bub-waiting">waiting for opportunity</div>
                </div>"""
                st.markdown(html, unsafe_allow_html=True)

st.markdown('<div style="height:12px"></div>', unsafe_allow_html=True)
if st.button("🔄 Refresh"):
    st.rerun()