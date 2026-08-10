"""
token_directory.py — PRV1311 left sidebar: token directory + fetch-on-click profiles.

Drop this file into C:\\Users\\Autry\\accum-flip\\Prv1311 next to rider_dashboard.py.

Wiring (2 lines in rider_dashboard.py, near the top after `import streamlit as st`):

    import token_directory
    token_directory.render_sidebar()

Data source: CoinGecko free API (/search to resolve IDs, /coins/{id} for profiles).
Fetch-on-click only. Profiles cached 1 hour; symbol->id mapping cached to
token_id_cache.json permanently. No key needed.

Profile = 5 sections mapped to the CoinPick-Redo outline:
  1 OVERVIEW    - what it is, in plain language (one-liner + categories)
  2 PRODUCT     - what it actually offers (Ease of Use / Hair-on-Fire /
                  Exclusivity are YOUR judgment - links provided to test it)
  3 LIQUIDITY   - mkt cap, rank, 24h vol, tier estimate + link to the
                  Gecko Markets tab for the +2% depth check
  4 NARRATIVE   - category chips, community signal, socials
  5 TEAM/LINKS  - homepage, whitepaper, github, gecko page for deep dive

NOT price-focused by design. Facts only.
"""

import html
import json
import os
import re

try:
    import truststore
    truststore.inject_into_ssl()  # use the OS cert store (fixes corporate/AV TLS-inspection CAs missing from certifi)
except ImportError:
    pass

import requests
import streamlit as st
import streamlit.components.v1 as components

CG = "https://api.coingecko.com/api/v3"
ID_CACHE_FILE = "token_id_cache.json"

# ---------------------------------------------------------------- palette
BG = "#12161A"
CARD = "#1A2026"
BLUE = "#5C99E6"
GREEN = "#4AA382"
SLATE = "#626D7A"
TEXT = "#E6EAEE"

# ------------------------------------------------------------- watchlist
FALLBACK_WATCHLIST = [
    "XRP", "B3", "HBAR", "FLR", "ZORA", "XYO", "FIL", "PREDI", "NOICE",
    "AVAX", "PROMPT", "SOL", "LINGO", "PAXG", "JASMY", "LCX", "G", "ROSE",
    "KMNO", "BIRB", "BNKRW", "BNKR", "ZBCN", "DOVU", "CLAWNCH", "BTC2",
    "ORCA", "FIS", "STORJ", "ZRO", "ARC", "OPG", "CLAWBANK", "DELU",
    "ODAI", "AXOBOTL", "ALLO", "GWEI", "MORPHO", "FIGHT", "WCHAN",
    "CHIPPY", "GRVT", "TRUMP", "SHIB", "KSM", "RE", "MPLX", "BASED1",
    "GROVE", "ERA", "AAVE", "COTI", "NEAR", "LINK", "VVV", "AERO",
    "EUL", "KAITO",
]


def _watchlist():
    try:
        from config import WATCHLIST  # define WATCHLIST=[...] in config.py to override
        return list(WATCHLIST)
    except Exception:
        return FALLBACK_WATCHLIST


def _active_rider_symbols():
    """Best-effort scan of the rider ledger for held symbols. Fails silent."""
    try:
        from config import RIDER_LEDGER_FILE
        with open(RIDER_LEDGER_FILE, "r") as f:
            ledger = json.load(f)
        syms = set()

        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if isinstance(v, str) and k.lower() in (
                        "symbol", "asset", "coin", "ticker", "pair", "market"
                    ):
                        s = v.upper()
                        for suf in ("/USD", "-USD", "USD"):
                            if s.endswith(suf) and len(s) > len(suf):
                                s = s[: -len(suf)]
                                break
                        syms.add(s.strip("/-"))
                    else:
                        walk(v)
            elif isinstance(o, list):
                for item in o:
                    walk(item)

        walk(ledger)
        return syms
    except Exception:
        return set()


# ------------------------------------------------------- CoinGecko layer
def _load_id_cache():
    if os.path.exists(ID_CACHE_FILE):
        try:
            with open(ID_CACHE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_id_cache(cache):
    try:
        with open(ID_CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except Exception:
        pass


def _resolve_id(symbol: str):
    """symbol -> coingecko id via /search. Exact-symbol match, best rank wins."""
    cache = _load_id_cache()
    if symbol in cache:
        return cache[symbol]
    try:
        r = requests.get(f"{CG}/search", params={"query": symbol}, timeout=10)
        r.raise_for_status()
        coins = r.json().get("coins", [])
    except Exception:
        return False  # transient (network/rate-limit) failure, not cached — distinct from "not listed"
    exact = [c for c in coins if c.get("symbol", "").upper() == symbol.upper()]
    pool = exact or coins
    if not pool:
        cache[symbol] = None
        _save_id_cache(cache)
        return None
    pool.sort(key=lambda c: c.get("market_cap_rank") or 10**9)
    cid = pool[0]["id"]
    cache[symbol] = cid
    _save_id_cache(cache)
    return cid


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_profile(coin_id: str):
    r = requests.get(
        f"{CG}/coins/{coin_id}",
        params={
            "localization": "false",
            "tickers": "false",
            "market_data": "true",
            "community_data": "true",
            "developer_data": "false",
            "sparkline": "false",
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


# ------------------------------------------------------- Coinbase price layer
@st.cache_data(ttl=60, show_spinner=False)
def _price_and_change(symbol: str):
    """Current price + 24h % change from Coinbase (ccxt). (None, None) if the
    symbol isn't listed on Coinbase or the fetch fails."""
    try:
        from screener import exchange
        pair = symbol if "/" in symbol else f"{symbol}/USD"
        if not exchange.markets:
            exchange.load_markets()
        if pair not in exchange.markets:
            return None, None
        ohlcv = exchange.fetch_ohlcv(pair, timeframe="1h", limit=25)
        if not ohlcv or len(ohlcv) < 2:
            return None, None
        last = ohlcv[-1][4]
        price_24h_ago = ohlcv[0][4]
        pct = (last - price_24h_ago) / price_24h_ago * 100 if price_24h_ago else None
        return last, pct
    except Exception:
        return None, None


def _fmt_price(p):
    if p >= 100:
        return f"${p:,.2f}"
    if p >= 1:
        return f"${p:,.4f}"
    if p >= 0.01:
        return f"${p:.4f}"
    return f"${p:.6f}"


def _price_html(symbol: str) -> str:
    """Colored price + 24h% block for beside the (unchanged) watchlist button, one line."""
    price, pct = _price_and_change(symbol)
    if price is None:
        return ""
    price_str = _fmt_price(price)
    color = GREEN if (pct is None or pct >= 0) else SLATE
    pct_str = f" {pct:+.1f}%" if pct is not None else ""
    return (
        f'<div style="color:{color};font-size:11px;white-space:nowrap;'
        f'text-align:right;font-weight:600;">{price_str}{pct_str}</div>'
    )


# -------------------------------------------------------------- helpers
def _strip_html(text: str) -> str:
    stripped = re.sub(r"<[^>]+>", "", text or "").strip()
    return html.escape(stripped)


def _sentences(text: str, n: int) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(parts[:n]).strip()


def _usd(x):
    if x is None:
        return "—"
    if x >= 1_000_000_000:
        return f"${x/1_000_000_000:.2f}B"
    if x >= 1_000_000:
        return f"${x/1_000_000:.1f}M"
    if x >= 1_000:
        return f"${x/1_000:.0f}K"
    return f"${x:,.2f}"


def _vol_tier(vol_24h):
    """Volume-based estimate. Final call = +2% depth on the Gecko Markets tab
    (Low <$250K depth / Med $250K-2M / High >$2M per CoinPick-Redo)."""
    if vol_24h is None:
        return "UNKNOWN", SLATE
    if vol_24h < 2_000_000:
        return "LOW (est.)", "#C9A227"
    if vol_24h < 25_000_000:
        return "MEDIUM (est.)", BLUE
    return "HIGH (est.)", GREEN


def _safe_url(url):
    """Only allow http(s) URLs through into an href -- blocks javascript:/data:
    schemes and attribute-breakout via the untrusted CoinGecko response."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return None
    return html.escape(url, quote=True)


def _link_row(links: dict) -> str:
    out = []

    def add(label, url):
        safe = _safe_url(url)
        if safe:
            out.append(f'<a href="{safe}" target="_blank" style="color:{BLUE};'
                       f'text-decoration:none;margin-right:10px;">{label}</a>')

    homepage = (links.get("homepage") or [""])[0]
    add("Website", homepage)
    add("Whitepaper", links.get("whitepaper"))
    gh = (links.get("repos_url", {}) or {}).get("github") or []
    add("GitHub", gh[0] if gh else None)
    tw = links.get("twitter_screen_name")
    add("X/Twitter", f"https://x.com/{tw}" if tw else None)
    return "".join(out) or f'<span style="color:{SLATE};">No links listed</span>'


def _section(title: str, body_html: str):
    st.sidebar.markdown(
        f"""<div style="background:{CARD};border:1px solid #232B33;
        border-radius:10px;padding:10px 12px;margin-bottom:8px;">
        <div style="color:{SLATE};font-size:11px;letter-spacing:1.2px;
        margin-bottom:6px;">{title}</div>
        <div style="color:{TEXT};font-size:13px;line-height:1.45;">{body_html}</div>
        </div>""",
        unsafe_allow_html=True,
    )


# -------------------------------------------------------------- profile
def _render_profile(symbol: str):
    coin_id = _resolve_id(symbol)
    if coin_id is False:
        st.sidebar.warning(f"{symbol}: CoinGecko lookup failed (rate limit?). Try again in a moment.")
        return
    if not coin_id:
        st.sidebar.warning(f"{symbol}: not found on CoinGecko.")
        return
    try:
        d = _fetch_profile(coin_id)
    except Exception:
        st.sidebar.warning(f"{symbol}: profile fetch failed (rate limit? retry in ~60s).")
        return

    name = html.escape(str(d.get("name", symbol)))
    symbol_safe = html.escape(str(symbol))
    desc = _strip_html((d.get("description") or {}).get("en", ""))
    cats = [html.escape(str(c)) for c in (d.get("categories") or []) if c][:6]
    links = d.get("links") or {}
    md = d.get("market_data") or {}
    mcap = (md.get("market_cap") or {}).get("usd")
    vol = (md.get("total_volume") or {}).get("usd")
    rank = d.get("market_cap_rank")
    comm = d.get("community_data") or {}
    tw_followers = comm.get("twitter_followers")
    up_pct = d.get("sentiment_votes_up_percentage")
    gecko_url = _safe_url(f"https://www.coingecko.com/en/coins/{coin_id}") or "https://www.coingecko.com"

    st.sidebar.markdown(
        f"""<div style="margin:6px 0 8px 0;">
        <span style="color:{BLUE};font-size:24px;font-weight:700;">{name}</span>
        <span style="color:{SLATE};font-size:13px;margin-left:6px;">{symbol_safe}</span>
        </div>""",
        unsafe_allow_html=True,
    )

    # 1 OVERVIEW
    overview = _sentences(desc, 2) or "No description listed on CoinGecko."
    _section("1 · OVERVIEW — WHAT IT IS", overview)

    # 2 PRODUCT
    more = _sentences(desc, 5)
    product = more[len(overview):].strip() if len(more) > len(overview) else ""
    product_html = (product or f'<span style="color:{SLATE};">See website — '
                    "test it yourself.</span>")
    product_html += (f'<div style="color:{SLATE};font-size:11px;margin-top:6px;">'
                     "Score it: Ease of Use · Hair-on-Fire · Exclusivity "
                     "(16+ = keep)</div>")
    _section("2 · PRODUCT", product_html)

    # 3 LIQUIDITY
    tier, tier_color = _vol_tier(vol)
    _section(
        "3 · LIQUIDITY",
        f"Mkt cap <b>{_usd(mcap)}</b> · Rank <b>#{rank or '—'}</b> · "
        f"24h vol <b>{_usd(vol)}</b><br>"
        f'Tier: <b style="color:{tier_color};">{tier}</b>'
        f'<div style="color:{SLATE};font-size:11px;margin-top:6px;">'
        f'Confirm with +2% depth → <a href="{gecko_url}#markets" target="_blank" '
        f'style="color:{BLUE};">Gecko Markets tab</a> '
        f"(&lt;$250K low · $250K–2M med · &gt;$2M high)</div>",
    )

    # 4 NARRATIVE
    chips = "".join(
        f'<span style="background:#22303C;color:{BLUE};border-radius:12px;'
        f'padding:2px 9px;font-size:11px;margin:0 4px 4px 0;'
        f'display:inline-block;">{c}</span>'
        for c in cats
    ) or f'<span style="color:{SLATE};">No categories listed</span>'
    signal = []
    if tw_followers:
        signal.append(f"{tw_followers:,} X followers")
    if up_pct:
        signal.append(f'<span style="font-size:17px;font-weight:700;">'
                      f'{up_pct:.0f}%</span> bullish sentiment')
    signal_html = (f'<div style="color:{SLATE};font-size:11px;margin-top:6px;">'
                   + " · ".join(signal) + "</div>") if signal else ""
    _section("4 · NARRATIVE", chips + signal_html)

    # 5 TEAM & LINKS
    _section(
        "5 · TEAM & LINKS",
        _link_row(links)
        + f'<div style="margin-top:6px;"><a href="{gecko_url}" target="_blank" '
        f'style="color:{BLUE};text-decoration:none;">Full CoinGecko page →</a></div>',
    )


# -------------------------------------------------------------- sidebar
def render_sidebar():
    st.markdown(
        f"""<style>
        [data-testid="stSidebar"] {{
            background: {BG};
            width: clamp(340px, 34vw, 520px) !important;
            min-width: 340px; max-width: 520px;
            border-right: 1px solid #232B33;
        }}
        [data-testid="stSidebar"] .stButton button {{
            width: 100%;
            background: transparent;
            color: {TEXT};
            border: none;
            border-radius: 8px;
            text-align: left;
            padding: 5px 10px;
            font-size: 13px;
        }}
        [data-testid="stSidebar"] .stButton button:hover {{
            background: {CARD};
            color: {BLUE};
        }}
        /* keep each watchlist row (name + price/%) on one line, vertically centered */
        [data-testid="stSidebar"] [data-testid="stHorizontalBlock"] {{
            flex-wrap: nowrap !important;
            align-items: center !important;
            gap: 4px;
        }}
        [data-testid="stSidebar"] [data-testid="stColumn"] {{
            min-width: 0 !important;
        }}
        /* open state: replace the double-arrow collapse icon with "« Close".
           Matches both possible DOM shapes (testid on a wrapper OR on the
           button itself) and neutralizes both svg-icon and icon-font glyphs. */
        [data-testid="stSidebarCollapseButton"],
        [data-testid="stSidebarCollapseButton"] button {{
            width: auto !important;
            height: auto !important;
            padding: 6px 16px !important;
            background: {CARD} !important;
            border: 1px solid #232B33 !important;
            border-radius: 8px !important;
            font-size: 0 !important;
        }}
        [data-testid="stSidebarCollapseButton"] svg,
        [data-testid="stSidebarCollapseButton"] button svg,
        [data-testid="stSidebarCollapseButton"] span,
        [data-testid="stSidebarCollapseButton"] button span {{ display: none !important; }}
        [data-testid="stSidebarCollapseButton"]::after,
        [data-testid="stSidebarCollapseButton"] button::after {{
            content: "« Close";
            color: {TEXT};
            font-size: 13px !important;
            font-weight: 700;
            letter-spacing: 0.5px;
        }}
        /* collapsed state: matching pill button reading "» Open" */
        [data-testid="stExpandSidebarButton"],
        [data-testid="stExpandSidebarButton"] button {{
            width: auto !important;
            height: auto !important;
            padding: 6px 16px !important;
            background: {CARD} !important;
            border: 1px solid {BLUE} !important;
            border-radius: 8px !important;
            font-size: 0 !important;
        }}
        [data-testid="stExpandSidebarButton"] svg,
        [data-testid="stExpandSidebarButton"] button svg,
        [data-testid="stExpandSidebarButton"] span,
        [data-testid="stExpandSidebarButton"] button span {{ display: none !important; }}
        [data-testid="stExpandSidebarButton"]::after,
        [data-testid="stExpandSidebarButton"] button::after {{
            content: "» Open";
            color: {BLUE};
            font-size: 13px !important;
            font-weight: 700;
            letter-spacing: 0.5px;
        }}
        </style>""",
        unsafe_allow_html=True,
    )

    st.sidebar.markdown(
        f'<div style="color:{TEXT};font-size:15px;font-weight:700;'
        f'letter-spacing:1px;margin-bottom:2px;">ASSETS</div>'
        f'<div style="color:{SLATE};font-size:11px;margin-bottom:8px;">'
        f"Watchlist directory · click for profile</div>",
        unsafe_allow_html=True,
    )

    selected = st.session_state.get("td_selected")

    # Profile view
    if selected:
        components.html(
            """<script>
            var sb = window.parent.document.querySelector('[data-testid="stSidebar"]');
            if (sb) { sb.scrollTop = 0; }
            </script>""",
            height=0,
        )
        if st.sidebar.button("← Back to list", key="td_back"):
            st.session_state["td_selected"] = None
            st.rerun()
        else:
            _render_profile(selected)
            return

    # List view
    query = st.sidebar.text_input(
        "Search", key="td_search", placeholder="Search token…",
        label_visibility="collapsed",
    )
    wl = sorted(set(s.upper() for s in _watchlist()))
    if query:
        wl = [s for s in wl if query.upper() in s]

    active = _active_rider_symbols()
    pinned = [s for s in wl if s in active]
    rest = [s for s in wl if s not in active]

    if pinned:
        st.sidebar.markdown(
            f'<div style="color:{GREEN};font-size:10px;letter-spacing:1.2px;'
            f'margin:4px 0 2px 0;">● ACTIVE RIDERS</div>',
            unsafe_allow_html=True,
        )
        for s in pinned:
            c1, c2 = st.sidebar.columns([3, 2])
            with c1:
                if st.button(f"● {s}", key=f"td_{s}"):
                    st.session_state["td_selected"] = s
                    st.rerun()
            with c2:
                st.markdown(_price_html(s), unsafe_allow_html=True)
        st.sidebar.markdown(
            f'<div style="color:{SLATE};font-size:10px;letter-spacing:1.2px;'
            f'margin:8px 0 2px 0;">WATCHLIST</div>',
            unsafe_allow_html=True,
        )
    for s in rest:
        c1, c2 = st.sidebar.columns([3, 2])
        with c1:
            if st.button(s, key=f"td_{s}"):
                st.session_state["td_selected"] = s
                st.rerun()
        with c2:
            st.markdown(_price_html(s), unsafe_allow_html=True)