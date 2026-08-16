"""dashboard.py — Streamlit dashboard, management-friendly "glimpse" view with a
neon dark theme.

Run:  streamlit run dashboard.py

Top of page (no clicks needed): a status strip (market regime, actionable count,
data health, last updated), a "what changed since last run" line, trade-plan
cards for the actual Buys (with position sizing from your capital), a "high score
but NOT a Buy — why" panel, and a book-level Portfolio-risk glimpse (total heat +
sector caps, Tier 2 #9). Drill-down tabs below hold the full colour-coded
watchlist, the Portfolio book, the strategy Edge backtest (expectancy / profit
factor / max drawdown / out-of-sample, Tier 2 #8), per-stock charts, history,
news, and reports.
"""

import os
import json
import hashlib

import pandas as pd
import streamlit as st
import plotly.graph_objects as go

import config
import database as db
import data_fetcher
import portfolio_risk
import portfolio_advisor
import backtester
import news_feed

st.set_page_config(page_title="PSX Shariah Engine", layout="wide",
                   page_icon="📈")

# ====================== NEON THEME ========================================
NEON = {"cyan": "#00e5ff", "violet": "#a855f7", "green": "#00ffa3",
        "amber": "#ffd54a", "red": "#ff4d6d", "text": "#e7f0ff",
        "dim": "#9fb3d1"}

# Signal / risk accent colours (neon, high-contrast on the dark background).
NEON_SIG = {"Strong Buy": "#00ffa3", "Buy": "#3ae67f", "Watch": "#ffd54a",
            "Hold": "#9fb3d1", "Avoid": "#ff5d7a", "Exit": "#ff4d6d",
            "No data": "#8aa0c0"}
NEON_RISK = {"Low": "#00ffa3", "Medium": "#ffd54a", "High": "#ff4d6d"}
SIG_RANK = {"Strong Buy": 6, "Buy": 5, "Watch": 4, "Hold": 3, "Avoid": 2,
            "Exit": 1, "No data": 0}
PLOT_LINE = ["#00e5ff", "#a855f7", "#00ffa3", "#ffd54a", "#ff4d6d"]


def _inject_theme():
    st.markdown(
        """
        <style>
        .stApp {
          background:
            radial-gradient(1100px 560px at 10% -12%, rgba(0,229,255,0.13), transparent 60%),
            radial-gradient(1000px 520px at 102% -4%, rgba(168,85,247,0.15), transparent 55%),
            radial-gradient(900px 520px at 50% 118%, rgba(0,255,163,0.10), transparent 55%),
            linear-gradient(180deg,#070b16 0%, #0a1020 48%, #070b16 100%);
          background-attachment: fixed;
        }
        [data-testid="stHeader"] { background: transparent; }
        [data-testid="stToolbar"] { right: 1rem; }
        h1, h2, h3 { color: #eaf6ff !important; letter-spacing:.3px; }
        h1 { text-shadow: 0 0 22px rgba(0,229,255,0.35); }
        h2 { text-shadow: 0 0 16px rgba(0,229,255,0.20); }
        hr { border-color: rgba(0,229,255,0.15) !important; }
        /* glassmorphic bordered containers (tiles, cards) */
        [data-testid="stVerticalBlockBorderWrapper"] {
          background: rgba(16,24,44,0.55);
          border: 1px solid rgba(0,229,255,0.18) !important;
          border-radius: 14px !important;
          box-shadow: 0 8px 30px rgba(0,0,0,0.45), inset 0 0 0 1px rgba(0,229,255,0.03);
          backdrop-filter: blur(7px);
          transition: border-color .2s ease, box-shadow .2s ease;
        }
        [data-testid="stVerticalBlockBorderWrapper"]:hover {
          border-color: rgba(0,229,255,0.40) !important;
          box-shadow: 0 10px 36px rgba(0,0,0,0.5), 0 0 22px -6px rgba(0,229,255,0.5);
        }
        [data-testid="stMetricValue"] {
          color: #00e5ff; text-shadow: 0 0 14px rgba(0,229,255,0.45);
          font-weight: 800;
        }
        [data-testid="stMetricLabel"] { color: #9fb3d1; }
        [data-testid="stMetricDelta"] { color: #00ffa3; }
        /* sidebar */
        [data-testid="stSidebar"] {
          background: linear-gradient(180deg, rgba(11,17,34,0.92), rgba(7,11,22,0.96));
          border-right: 1px solid rgba(0,229,255,0.14);
        }
        /* tabs */
        [data-baseweb="tab-list"] { gap: 6px; border-bottom: 1px solid rgba(0,229,255,0.12); }
        [data-baseweb="tab"] {
          background: rgba(255,255,255,0.03); border-radius: 10px 10px 0 0;
          padding: 7px 15px; color: #cfe0ff;
        }
        [aria-selected="true"][data-baseweb="tab"] {
          background: rgba(0,229,255,0.13);
          box-shadow: inset 0 -2px 0 #00e5ff, 0 0 18px -6px rgba(0,229,255,0.7);
          color: #eaf6ff;
        }
        /* buttons */
        .stButton > button {
          background: linear-gradient(90deg, #00e5ff, #a855f7);
          color: #06101f; font-weight: 700; border: none; border-radius: 10px;
          box-shadow: 0 0 18px -4px rgba(0,229,255,0.6);
        }
        .stButton > button:hover { filter: brightness(1.12); color:#06101f; }
        /* inputs */
        [data-testid="stNumberInput"] input, [data-baseweb="select"] > div {
          background: rgba(10,16,32,0.7) !important;
          border-color: rgba(0,229,255,0.25) !important;
        }
        </style>
        """,
        unsafe_allow_html=True)


# ----------------------------- pills / helpers ----------------------------
def _hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _pill(text, hexc):
    r, g, b = _hex_rgb(hexc)
    return (f'<span style="background:rgba({r},{g},{b},0.13);color:{hexc};'
            f'border:1px solid rgba({r},{g},{b},0.55);border-radius:9px;'
            f'padding:2px 10px;font-size:13px;font-weight:700;white-space:nowrap;'
            f'text-shadow:0 0 7px rgba({r},{g},{b},0.45);'
            f'box-shadow:0 0 12px -3px rgba({r},{g},{b},0.7)">{text}</span>')


def sig_pill(sig):
    return _pill(sig or "—", NEON_SIG.get(sig, "#8aa0c0"))


def accum_pill():
    return _pill("🧲 Accumulating", "#37c6ff")


def risk_pill(level):
    return _pill(f"{level} risk", NEON_RISK.get(level, "#8aa0c0"))


def news_pill(verdict):
    """Compact news verdict chip. verdict is the dict from news_feed.get(sym)."""
    if not verdict:
        return _pill("📰 no fresh news", "#8aa0c0")
    score = verdict.get("score", 50)
    delta = score - 50  # symmetric around neutral
    direction = verdict.get("direction", "neutral")
    mat = verdict.get("materiality", "normal")
    if direction == "positive":
        clr = NEON["green"]
        arrow = "▲"
    elif direction == "negative":
        clr = NEON["red"]
        arrow = "▼"
    else:
        clr = "#8aa0c0"
        arrow = "●"
    star = " ★" if mat in ("material_positive", "material_negative") else ""
    return _pill(f"📰 {arrow} {delta:+d}{star}", clr)


def _news_window(symbol, nv=None):
    """UNSCORED per-symbol news window for manual cross-verification. Shows the
    auto-fetched last-24h headlines (news_raw_24h.json, refreshed by news.yml on
    a cron — no manual routine). News carries ZERO score weight; this is purely
    so the user can eyeball real, source-linked headlines. Falls back to the
    LLM-judged summary only if it happens to exist."""
    items = news_feed.raw_headlines(symbol, limit=5)
    st.markdown("**📰 News — last 24h (not scored; for your manual check)**")
    if items:
        for it in items:
            pub = it.get("publisher") or "source"
            url, title = it.get("url"), it["title"]
            st.markdown(f"- [{title}]({url}) · _{pub}_" if url
                        else f"- {title} · _{pub}_")
    elif nv and nv.get("summary"):
        st.markdown(f"_{nv['summary']}_")
        for h, u in zip(nv.get("headlines", []), nv.get("sources", [])):
            st.markdown(f"- [{h}]({u})")
    else:
        st.caption("No allowlisted headlines fetched for this symbol in the last "
                   "24h. News never moves the score — this window is informational.")


_GLM_STYLE = {
    "highly_positive": (NEON["green"], "▲▲ GLM highly +ve"),
    "positive":        (NEON["green"], "▲ GLM +ve"),
    "neutral":         ("#8aa0c0",     "● GLM neutral"),
    "negative":        (NEON["red"],   "▼ GLM -ve"),
    "highly_negative": (NEON["red"],   "▼▼ GLM highly -ve"),
}


def glm_pill(rating_dict):
    """Compact GLM news-rating chip. rating_dict is news_feed.glm_rating(sym)."""
    if not rating_dict:
        return _pill("🤖 GLM: —", "#8aa0c0")
    clr, label = _GLM_STYLE.get(rating_dict.get("rating"), ("#8aa0c0", "🤖 GLM: ?"))
    return _pill(f"🤖 {label}", clr)


def whatif_regime_note(actual_regime, assumed_regime, signal):
    """One-line label explaining what the signal WOULD be under an assumed
    regime. Approximation, not a re-run: risk-off soft-downgrades Buy→Watch
    per signal_generator; risk-on relaxes the chase guard and lets some
    regime-downgraded Watches surface as Buys. Actual regime → no note."""
    if not assumed_regime or assumed_regime == actual_regime:
        return ""
    if assumed_regime == "risk-off" and signal in ("Buy", "Strong Buy"):
        return ("🌩 Under **risk-off**: signal would soft-downgrade to Watch "
                "(regime gate) — position size accordingly.")
    if assumed_regime == "risk-on" and signal in ("Buy", "Strong Buy"):
        return "☀ Under **risk-on**: signal holds; chase guard also loosens."
    if assumed_regime == "risk-on" and signal == "Watch":
        return ("☀ Under **risk-on**: if this Watch was regime-downgraded, "
                "it would revert to Buy (check main_reason).")
    return ""


def regime_pill(regime):
    if regime == "risk-on":
        return _pill("● Risk-on", NEON["green"])
    if regime == "risk-off":
        return _pill("● Risk-off", NEON["red"])
    return _pill("● Unknown", "#8aa0c0")


def fmt(x, d=2):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    if isinstance(x, float) and x == float("inf"):
        return "∞"
    return f"{x:,.{d}f}"


def price_row(pairs):
    """Entry/Stop/Target strip as plain markdown. NOT st.metric: that widget
    lives in a lazily-imported JS chunk, and a browser holding a cached page
    shell from an earlier Cloud build 404s it ("Importing a module script
    failed") — hiding the three numbers that matter most on a trade card."""
    cells = "".join(
        f'<div style="flex:1;min-width:72px">'
        f'<div style="opacity:.55;font-size:11px;letter-spacing:.4px;'
        f'text-transform:uppercase">{label}</div>'
        f'<div style="font-size:19px;font-weight:700;color:{color}">{value}</div>'
        f'</div>'
        for label, value, color in pairs)
    return (f'<div style="display:flex;gap:10px;margin:8px 0 10px">{cells}</div>')


def neon_fig(fig, height=None):
    fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(color="#cfe0ff"),
                      margin=dict(l=10, r=10, t=34, b=10),
                      legend=dict(bgcolor="rgba(0,0,0,0)",
                                  bordercolor="rgba(0,229,255,0.15)"))
    fig.update_xaxes(gridcolor="rgba(255,255,255,0.06)", zeroline=False)
    fig.update_yaxes(gridcolor="rgba(255,255,255,0.06)", zeroline=False)
    if height:
        fig.update_layout(height=height)
    return fig


def why_not_buy(reason):
    """Pull the most relevant 'why it isn't a Buy' clause out of main_reason."""
    if not reason:
        return ""
    segs = [s.strip() for s in str(reason).split(";") if s.strip()]
    for kw in ("Downgraded", "breakdown", "negative news", "Shariah", "regime",
               "risk/reward", "manipulation", "confidence", "No usable price"):
        for s in segs:
            if kw.lower() in s.lower():
                return s
    return segs[-1] if segs else ""


def changes_since_last():
    """Symbols whose signal changed vs the previous run cycle."""
    ups, downs = [], []
    for s in config.STOCKS:
        h = db.run_history(s, 2)
        if len(h) >= 2 and h[0]["signal"] != h[1]["signal"]:
            cur, prev = h[0]["signal"], h[1]["signal"]
            (ups if SIG_RANK.get(cur, 0) > SIG_RANK.get(prev, 0) else downs).append(
                (s, prev, cur))
    return ups, downs


# ----------------------------- cached backtests ---------------------------
# fetch_eod hits the network with no cache, so backtests are expensive. Cache
# hard and only run the universe-wide one behind a button.
@st.cache_data(ttl=3600, show_spinner=False)
def bt_symbol(sym):
    return backtester.backtest(sym)


@st.cache_data(ttl=3600, show_spinner=False)
def bt_portfolio():
    return backtester.backtest_portfolio()


def _password_configured():
    try:
        pw = st.secrets["DASHBOARD_PASSWORD"]
    except Exception:
        pw = os.environ.get("DASHBOARD_PASSWORD")
    return pw


def _auth_token(pw):
    """Non-reversible token derived from the password, used to keep a tab logged
    in across reloads. Not the password itself; still a bearer token, so anyone
    with the URL gets in — acceptable for a single-user personal dashboard."""
    return hashlib.sha256(("psx-dash:" + str(pw)).encode()).hexdigest()[:32]


def _auto_refresh():
    """Streamlit Cloud reboots the app when a new commit lands, but a browser
    tab left open keeps rendering whatever it loaded at boot. Reload the whole
    page on a timer so the tab reconnects to the freshly-rebooted server and
    re-reads the committed DB. Works WITH a password now: login stamps a hashed
    token into the URL (see _require_password) that survives the reload and the
    Streamlit Cloud redeploy, so the refresh no longer forces a re-login."""
    secs = int(getattr(config, "DASHBOARD_REFRESH_SECONDS", 300))
    if secs <= 0:
        return
    st.markdown(
        f"<script>setTimeout(function(){{window.parent.location.reload();}},"
        f" {secs * 1000});</script>",
        unsafe_allow_html=True)


def _require_password():
    pw = _password_configured()
    if not pw:
        return
    token = _auth_token(pw)
    # Stay authenticated across the timed reload AND across Streamlit Cloud
    # redeploys (which drop all server-side sessions) by carrying a hashed token
    # in the URL query string — window.location.reload() preserves it, so the
    # reloaded tab re-authenticates itself instead of bouncing to the login box.
    if st.session_state.get("auth_ok") or st.query_params.get("k") == token:
        st.session_state["auth_ok"] = True
        return
    st.title("🔒 PSX Shariah Engine")
    entered = st.text_input("Enter dashboard password", type="password")
    if entered == pw:
        st.session_state["auth_ok"] = True
        st.query_params["k"] = token
        st.rerun()
    elif entered:
        st.error("Incorrect password.")
    st.stop()


def _inject_compact_css():
    st.markdown(
        """
        <style>
        [data-testid="stVerticalBlockBorderWrapper"] { padding: 2px !important; }
        [data-testid="stVerticalBlockBorderWrapper"] [data-testid="stMarkdownContainer"] p {
          margin-bottom: 2px;
        }
        [data-testid="stMetricValue"] { font-size: 1rem !important; }
        [data-testid="stMetricLabel"] { font-size: 0.72rem !important; }
        h2 { font-size: 1.05rem !important; margin-top: 0.3rem !important; }
        h3, .stMarkdown h3 { font-size: 0.95rem !important; }
        [data-testid="stCaptionContainer"] { font-size: 0.72rem !important; }
        .block-container { padding-top: 1.2rem !important; padding-bottom: 1rem !important; }
        [data-testid="column"] { gap: 0.3rem !important; }
        </style>
        """,
        unsafe_allow_html=True)


# ----------------------------- load ---------------------------------------
_inject_theme()
_require_password()
_auto_refresh()
db.init_db()

rows = []
for sym in config.STOCKS:
    r = db.last_run(sym)
    if r:
        rows.append(r)
if not rows:
    st.title("PSX Shariah Engine")
    st.warning("No runs stored yet. Run `python main.py run` first.")
    st.stop()

latest = pd.DataFrame(rows).sort_values("final_score", ascending=False,
                                        na_position="last")
for col in ("relative_strength", "market_regime", "buy_zone_low", "buy_zone_high"):
    if col not in latest.columns:
        latest[col] = None

# Your saved book (holdings + ready cash) — drives the Portfolio tab + sizing.
_portfolio = portfolio_advisor.load_portfolio()
latest_by_symbol = {r["symbol"]: r for r in rows}

regime = (latest["market_regime"].dropna().iloc[0]
          if latest["market_regime"].notna().any() else "unknown")
# run_time is written by the engine as a naive local timestamp, and the cloud
# runs set TZ=Asia/Karachi, so it is already Pakistan wall-clock (PKT, a fixed
# UTC+5 with no DST). Show it as-is and measure age against PKT now — do NOT
# add another +5h (that double-shifted the time and made age go negative).
_latest_pkt = pd.to_datetime(latest["run_time"].max())
last_updated = _latest_pkt.strftime("%m-%d %H:%M") + " PKT"
# Honest staleness flag: the cloud may pause runs (off-hours, weekends, paused
# Action) — in that case signals here describe yesterday's market, not today's.
# Compare against PKT now so the age matches the stored PKT run_time.
_now_pkt = pd.Timestamp.now(tz="Asia/Karachi").tz_localize(None)
_age_hours = (_now_pkt - _latest_pkt).total_seconds() / 3600
_amber = getattr(config, "DATA_FRESHNESS_AMBER_HOURS", 4)
_red = getattr(config, "DATA_FRESHNESS_RED_HOURS", 24)
if _age_hours >= _red:
    _stale_level, _stale_color, _stale_label = "red", NEON["red"], "STALE"
elif _age_hours >= _amber:
    _stale_level, _stale_color, _stale_label = "amber", NEON["amber"], "aging"
else:
    _stale_level, _stale_color, _stale_label = "fresh", NEON["green"], "fresh"
_last_updated_html = (f'<span style="color:{_stale_color}">{last_updated}</span>'
                      f' <span style="font-size:11px;opacity:.7">'
                      f'({_stale_label}, {_age_hours:.1f}h old)</span>')
good = int((latest["data_quality"] == "good").sum())

# ----------------------------- sidebar ------------------------------------
st.sidebar.header("⚙ Settings")
compact = st.sidebar.toggle("📱 Compact view", value=st.session_state.get("compact", False),
                            help="Denser layout — smaller tiles, fewer clicks, "
                                 "collapsible secondary sections. Good for phones.")
st.session_state["compact"] = compact
if compact:
    _inject_compact_css()
capital = st.sidebar.number_input(
    "Ready cash to deploy (PKR)", min_value=0,
    value=int(_portfolio.get("cash_pkr") or 200_000), step=25_000, format="%d")
st.sidebar.caption(
    f"Per-trade risk {config.RISK['max_risk_per_trade_pct']}% · max "
    f"{config.RISK['max_position_pct']}% per stock.")
st.sidebar.caption(
    f"Book caps: heat {config.PORTFOLIO_RISK['max_portfolio_heat_pct']:.0f}% · "
    f"sector {config.PORTFOLIO_RISK['max_sector_exposure_pct']:.0f}% · "
    f"{config.PORTFOLIO_RISK['max_open_positions']} positions.")
st.sidebar.caption("Defaults to the cash in portfolio.json. The **Portfolio** tab "
                   "builds a strategy from your actual holdings + this cash.")

st.sidebar.caption(news_feed.glm_status_line())

# ----------------------------- portfolio risk (computed once) -------------
buy_cands = [{"symbol": r["symbol"], "score": r["final_score"],
              "signal": r["signal"], "price": r["price"], "stop": r["stop_loss"],
              "sector": config.SECTORS.get(r["symbol"], "Unknown")}
             for _, r in latest.iterrows()
             if r["signal"] in ("Buy", "Strong Buy")]
pf = portfolio_risk.assess(buy_cands, capital=capital)
book = pf["book"]

# ----------------------------- header + status strip ----------------------
st.title("📈 PSX Shariah Engine — Today")
st.caption("⚠ " + config.DISCLAIMER)
_news_wt = int((config.WEIGHTS.get("sentiment", 0)
                + config.WEIGHTS.get("macro_news", 0)) * 100)
if _news_wt == 0:
    st.caption(f"📰 {news_feed.raw_status_line()} News carries **0% weight** — "
               "headlines are shown per stock for manual cross-verification only, "
               "never moved into the score.")
else:
    st.caption(f"📰 {news_feed.status_line()} News carries {_news_wt}% of the "
               "final score.")


def tile(col, label, value_html, sub=""):
    with col:
        box = st.container(border=True)
        box.markdown(
            f'<div style="font-size:12px;opacity:.65">{label}</div>'
            f'<div style="font-size:20px;font-weight:700;margin:3px 0">{value_html}</div>'
            f'<div style="font-size:12px;opacity:.6">{sub}</div>',
            unsafe_allow_html=True)


# Regime what-if — on the MAIN page (was buried in the sidebar), sitting right
# above the Market-regime tile it drives. Purely a DISPLAY overlay; it never
# re-runs the engine or mutates stored signals.
_wf_choice = st.radio(
    "🔀 Regime what-if", ["Actual", "Assume risk-on", "Assume risk-off"],
    index=0, horizontal=True,
    help="Assume risk-on reverses ONLY the risk-off regime gate for display, "
         "surfacing the technical Buys the engine downgraded to Watch. "
         "Approximation, not a re-run — verify manually.")
assumed_regime = {"Assume risk-on": "risk-on",
                  "Assume risk-off": "risk-off"}.get(_wf_choice)


# Under an assumed risk-on regime, reverse ONLY the risk-off regime gate: a Watch
# whose reason cites that exact gate was a technical Buy the engine downgraded for
# regime alone, so it surfaces as a Buy. The phrase match is exact, so confluence/
# chase/earnings/rr downgrades are never touched. Buy, never Strong Buy (pre-gate
# tier unknown — take the conservative one).
def _display_signal(sig, reason):
    if (assumed_regime == "risk-on" and regime == "risk-off"
            and sig == "Watch" and "market regime risk-off" in str(reason)):
        return "Buy"
    return sig


latest["display_signal"] = [
    _display_signal(s, mr)
    for s, mr in zip(latest["signal"], latest["main_reason"])]
_whatif_active = bool((latest["display_signal"] != latest["signal"]).any())
buys = latest[latest["display_signal"].isin(["Strong Buy", "Buy"])]
exits = latest[latest["display_signal"] == "Exit"]

t1, t2, t3, t4, t5 = st.columns(5)
tile(t1, "Market regime", regime_pill(regime),
     f"benchmark {config.BENCHMARK_INDEX}")
_act_sub = f"{len(exits)} exits" if len(exits) else "no exits"
if _whatif_active:
    _act_sub += " · 🔀 assume risk-on"
tile(t2, "Actionable now", f"{len(buys)} buys", _act_sub)
top = buys.iloc[0]["symbol"] if not buys.empty else "—"
tile(t3, "Top pick", top,
     f"score {buys.iloc[0]['final_score']:.0f}" if not buys.empty else "no buys")
tile(t4, "Portfolio heat",
     f'<span style="color:{NEON["green"] if book["heat_pct"] <= book["max_heat_pct"] else NEON["red"]}">'
     f'{book["heat_pct"]:.1f}%</span>',
     f"of {book['max_heat_pct']:.0f}% cap · {book['open_positions']} positions")
tile(t5, "Last updated", _last_updated_html,
     "reboot app if stale" if _stale_level == "fresh"
     else f"⚠ data {_age_hours:.0f}h old — signals may not reflect current price")

# Staleness banner — louder than the tile, only shown when data is past amber.
if _stale_level != "fresh":
    if _stale_level == "red":
        st.error(f"⚠ Data is **{_age_hours:.1f} hours old** (over "
                 f"{_red}h threshold). Signals below reflect the LAST RUN, not "
                 "current market action. Re-run the engine before acting.")
    else:
        st.warning(f"⏳ Data is **{_age_hours:.1f} hours old** — past the {_amber}h "
                   "freshness threshold. Verify quotes manually before acting.")

# ----------------------------- GLM news read ------------------------------
# Second opinion from GLM-4.5-flash on the last-24h headlines. ZERO score
# weight — a manual cross-check of whether the LLM's read agrees with the
# engine. Shown here for EVERY rated symbol, independent of whether it has a
# Buy signal (the per-card 🤖 pill only appears on actionable cards, which are
# empty in a risk-off market — this panel is where the ratings always live).
_glm_ratings, _glm_meta = news_feed.load_glm_ratings()
if _glm_meta.get("status") == "ok" and _glm_ratings:
    with st.expander(f"🤖 GLM news read — {len(_glm_ratings)} symbols "
                     "(second opinion, unweighted)", expanded=False):
        st.caption("Zero score weight — informational cross-check only, never "
                   "moved into the engine's Buy/Avoid.")
        _order = {"highly_positive": 0, "positive": 1, "neutral": 2,
                  "negative": 3, "highly_negative": 4}
        for sym in sorted(_glm_ratings,
                          key=lambda s: (_order.get(_glm_ratings[s].get("rating"), 9), s)):
            gv = _glm_ratings[sym]
            st.markdown(
                f'<div style="margin:3px 0">{glm_pill(gv)} '
                f'<b>{sym}</b> <span style="opacity:.7;font-size:12px">'
                f'{gv.get("reason", "")}</span></div>',
                unsafe_allow_html=True)
        st.caption(news_feed.glm_status_line())
elif _glm_meta.get("status") in ("absent", "stale"):
    st.caption(f"🤖 {news_feed.glm_status_line()}")

# ----------------------------- what changed -------------------------------
ups, downs = changes_since_last()
if ups or downs:
    parts = []
    for s, p, c in ups:
        parts.append(f"🔼 **{s}** {p}→{c}")
    for s, p, c in downs:
        parts.append(f"🔽 **{s}** {p}→{c}")
    st.markdown("**Since last run:** " + " · ".join(parts))
else:
    st.caption("No signal changes since the last run.")

st.divider()

# ----------------------------- ACTION TODAY -------------------------------
st.subheader("🎯 Action today")
if _whatif_active:
    st.info("🔀 **What-if: Assume risk-on** — the market is really risk-off, so "
            "the Buys below are technical signals the engine downgraded to Watch "
            "via the regime gate. Shown as Buys under the assumption only. "
            "Approximation, not a re-run — verify manually before acting.")
action = latest[latest["display_signal"].isin(["Strong Buy", "Buy", "Exit"])]
if action.empty:
    st.info(f"No Buy or Exit signals right now — nothing to act on. "
            f"(Market regime: {regime}.)")
elif compact:
    st.caption("Manual confirmation required before any order. Toggle off "
               "**Compact view** for full trade-plan cards.")
    act_show = action[["symbol", "display_signal", "price", "stop_loss", "target1",
                       "confidence", "relative_strength"]].copy()
    act_show.columns = ["Symbol", "Signal", "Price", "Stop", "Target", "Conf%", "RS"]
    st.dataframe(
        act_show.style
        .map(lambda v: f"color:{NEON_SIG.get(v, '')};font-weight:700", subset=["Signal"])
        .format({"Price": "{:.2f}", "Stop": "{:.2f}", "Target": "{:.2f}",
                 "Conf%": "{:.0f}", "RS": "{:.0f}"}, na_rep="—"),
        width="stretch", hide_index=True)
else:
    st.caption("Manual confirmation required before any order. See the "
               "**🛡 Portfolio** tab for sizing against your actual holdings + cash.")
    cards = list(action.iterrows())
    for i in range(0, len(cards), 2):
        cols = st.columns(2)
        for col, (_, r) in zip(cols, cards[i:i + 2]):
            with col:
                box = st.container(border=True)
                disp = r["display_signal"]
                sec = config.SECTORS.get(r["symbol"], "")
                box.markdown(
                    f'<div style="display:flex;justify-content:space-between;'
                    f'align-items:center">'
                    f'<div><span style="font-size:20px;font-weight:700">'
                    f'{r["symbol"]}</span> '
                    f'<span style="opacity:.6;font-size:13px">{sec}</span></div>'
                    f'{sig_pill(disp)} '
                    f'{accum_pill() if r.get("accumulation_candidate") else ""}</div>',
                    unsafe_allow_html=True)
                box.markdown(price_row([
                    ("Entry", fmt(r["price"]), "#e8f0ff"),
                    ("Stop", fmt(r["stop_loss"]), NEON["red"]),
                    ("Target", fmt(r["target1"]), NEON["green"]),
                ]), unsafe_allow_html=True)
                bzl, bzh = r.get("buy_zone_low"), r.get("buy_zone_high")
                if pd.notna(bzl) and pd.notna(bzh):
                    box.markdown(
                        f'<span style="background:rgba(0,255,163,0.14);'
                        f'color:{NEON["green"]};padding:2px 8px;border-radius:6px;'
                        f'font-size:13px;font-weight:700">🎯 Buy-zone '
                        f'{bzl:.2f}–{bzh:.2f}</span> '
                        f'<span style="opacity:.6;font-size:12px">pullback to '
                        f'{config.PULLBACK_EMA_SPAN}-EMA</span>', unsafe_allow_html=True)
                rs = r.get("relative_strength")
                rs_txt = f"RS {rs:.0f}" if pd.notna(rs) else "RS —"
                conf_val = r.get("confluence")
                conf_dots = (("●" * conf_val + "○" * (4 - conf_val))
                             if conf_val is not None else "")
                conf_html = (f'<span style="opacity:.7;font-size:12px">'
                             f'confluence {conf_dots} {conf_val}/4</span>'
                             if conf_val is not None else "")
                box.markdown(
                    f'{risk_pill(r["risk_level"])} &nbsp; '
                    f'<span style="opacity:.75;font-size:13px">conf '
                    f'{fmt(r["confidence"], 0)}% · {rs_txt} · '
                    f'R:R {fmt((r["target1"] - r["price"]) / (r["price"] - r["stop_loss"]), 1) if r["price"] and r["stop_loss"] and r["price"] > r["stop_loss"] else "—"}'
                    f'</span>',
                    unsafe_allow_html=True)
                if conf_html:
                    box.markdown(conf_html, unsafe_allow_html=True)
                nv = news_feed.get(r["symbol"])
                gv = news_feed.glm_rating(r["symbol"])
                box.markdown(news_pill(nv) + " " + glm_pill(gv) +
                             (f' <span style="opacity:.75;font-size:12px">'
                              f'{nv["summary"][:120]}</span>' if nv else ''),
                             unsafe_allow_html=True)
                if gv and gv.get("reason"):
                    box.caption(f"🤖 GLM: {gv['reason']}")
                if disp != r["signal"]:
                    box.warning("🔀 What-if (assume risk-on): engine's REAL signal "
                                "is **Watch** — downgraded by the risk-off regime "
                                "gate. Verify manually before acting.")
                else:
                    _wf = whatif_regime_note(regime, assumed_regime, r["signal"])
                    if _wf:
                        box.markdown(_wf)
                box.caption(str(r["main_reason"])[:240])
                with box.expander("📋 Full detail"):
                    st.write("**Full reason:**", r["main_reason"])
                    st.write("**Main risk:**", r["main_risk"])
                    st.write("**Shariah:**", r["shariah_status"], " · "
                             "**Market regime:**", r.get("market_regime") or "—")
                    st.write("**Support / Resistance:**",
                             f"{fmt(r.get('support'))} / {fmt(r.get('resistance'))}")
                    bzl2, bzh2 = r.get("buy_zone_low"), r.get("buy_zone_high")
                    if pd.notna(bzl2) and pd.notna(bzh2):
                        st.write(f"**Buy-zone ({config.PULLBACK_EMA_SPAN}-EMA pullback):**",
                                 f"{bzl2:.2f}–{bzh2:.2f}")
                    _news_window(r["symbol"], nv)
                    st.caption("For the price/volume chart and a per-stock "
                               "backtest, open the 📈 Stock detail tab.")

def _why_not_buy_section():
    why = latest[(latest["final_score"] >= config.SIGNAL_THRESHOLDS["buy"]) &
                 (~latest["signal"].isin(["Strong Buy", "Buy"]))]
    if why.empty:
        return
    st.caption("These scored in Buy range but a safety rule held them back. "
               "No need to dig — the reason is shown.")
    for _, r in why.iterrows():
        st.markdown(
            f'{sig_pill(r["signal"])} &nbsp;**{r["symbol"]}** '
            f'(score {r["final_score"]:.0f}) — '
            f'<span style="opacity:.8">{why_not_buy(r["main_reason"])}</span>',
            unsafe_allow_html=True)


def _portfolio_glimpse_section():
    if not buy_cands:
        return
    g1, g2, g3, g4 = st.columns(4)
    tile(g1, "Total heat",
         f'<span style="color:{NEON["green"] if book["heat_pct"] <= book["max_heat_pct"] else NEON["red"]}">'
         f'{book["heat_pct"]:.2f}%</span>',
         f"cap {book['max_heat_pct']:.0f}% · {book['heat_room_pct']:.1f}% room")
    tile(g2, "Capital deployed", f'{book["deployed_pct"]:.0f}%',
         f"{book['cash_pct']:.0f}% cash")
    tile(g3, "Open positions", f'{book["open_positions"]}',
         f"max {book['max_open_positions']}")
    tile(g4, "Deferred by caps", f'{book["deferred"]}',
         "see Portfolio tab")
    if pf["deferred"]:
        st.caption("⚠ " + " · ".join(f"**{d['symbol']}** {d['reason']}"
                                     for d in pf["deferred"][:4]))


def _early_watch_section():
    ew = latest[latest.get("early_watch").fillna(0) == 1] if "early_watch" in latest.columns \
        else latest.iloc[0:0]
    if ew.empty:
        st.caption("No early-watch names right now. This tier only fires on real "
                   "money-flow build-up (CMF) below the Buy band.")
        return
    st.caption("Lead-time tier: money flow building BEFORE the score confirms. "
               "NOT a buy signal — it exists so a move can be prepared for "
               "instead of chased. Graded on the 7-day horizon; treat as "
               "unproven until that history accumulates.")
    for _, r in ew.sort_values("cmf", ascending=False).iterrows():
        st.markdown(
            f'<span style="background:rgba(122,162,255,0.16);color:#7aa2ff;'
            f'padding:2px 8px;border-radius:6px;font-size:12px;font-weight:700">'
            f'🔭 EARLY</span> &nbsp;**{r["symbol"]}** · price {fmt(r["price"])} · '
            f'score {fmt(r["final_score"], 0)} · CMF {fmt(r.get("cmf"), 2)} · '
            f'RS {fmt(r.get("relative_strength"), 0)}'
            f'<br><span style="opacity:.75;font-size:13px">'
            f'{r.get("early_reason") or ""}</span>',
            unsafe_allow_html=True)


def _accumulation_section():
    rows = db.accumulating_now(lookback=10, min_streak=1)
    if not rows:
        st.caption("No accumulation candidates flagged right now.")
        return
    st.caption("Quiet-buying signature (OBV trend, OBV/price divergence, "
               "volume spikes, CMF) — not yet a Buy signal. ⚠ Measured on "
               "graded history this tag showed NO forward edge (47% beat rate "
               "at 3 days, 53% at 7); the OBV divergence component measured "
               "NEGATIVE. Kept for context only — prefer the Early watch tier "
               "above, which uses the one component (CMF) that did work.")
    for r in rows:
        reasons = json.loads(r["reasons"] or "[]")
        st.markdown(
            f'{accum_pill()} &nbsp;**{r["symbol"]}** · '
            f'signal {sig_pill(r["signal"])} · price {fmt(r["price"])} · '
            f'score {fmt(r["final_score"], 0)}'
            + (f' · CMF {r["cmf"]:+.2f}' if r.get("cmf") is not None else '')
            + f'<br><span style="opacity:.75;font-size:13px">{"; ".join(reasons)}</span>',
            unsafe_allow_html=True)


# ----------------------------- WHY NOT A BUY ------------------------------
_why = latest[(latest["final_score"] >= config.SIGNAL_THRESHOLDS["buy"]) &
              (~latest["signal"].isin(["Strong Buy", "Buy"]))]
if not _why.empty:
    if compact:
        with st.expander("⚠ High score, but NOT a Buy — here's why"):
            _why_not_buy_section()
    else:
        st.subheader("⚠ High score, but NOT a Buy — here's why")
        _why_not_buy_section()

# ----------------------------- PORTFOLIO GLIMPSE --------------------------
if buy_cands:
    if compact:
        with st.expander("🛡 Portfolio risk — does the book fit?"):
            _portfolio_glimpse_section()
    else:
        st.subheader("🛡 Portfolio risk — does the book fit?")
        _portfolio_glimpse_section()

# ----------------------------- ACCUMULATION WATCH --------------------------
if compact:
    with st.expander("🔭 Early watch — building before the Buy band"):
        _early_watch_section()
else:
    st.subheader("🔭 Early watch — building before the Buy band")
    _early_watch_section()

if compact:
    with st.expander("🧲 Accumulation watch — stocks being quietly bought"):
        _accumulation_section()
else:
    st.subheader("🧲 Accumulation watch — stocks being quietly bought")
    _accumulation_section()

st.divider()

# ----------------------------- tabs (drill-down) --------------------------
(tab_watch, tab_port, tab_edge, tab_stock, tab_hist,
 tab_news, tab_reports) = st.tabs(
    ["📋 Watchlist", "🛡 Portfolio", "🧪 Edge", "🔍 Stock detail",
     "📈 History", "📰 News", "📋 Reports"])

with tab_watch:
    st.caption("Full ranking — colour-coded. Sort by clicking a column header.")
    show = latest[["symbol", "final_score", "relative_strength", "signal",
                   "risk_level", "confidence", "price", "stop_loss", "target1",
                   "buy_zone_low", "buy_zone_high",
                   "data_quality", "shariah_status"]].copy()
    show["buy_zone"] = [f"{lo:.2f}–{hi:.2f}" if pd.notna(lo) and pd.notna(hi) else "—"
                        for lo, hi in zip(show["buy_zone_low"], show["buy_zone_high"])]
    show = show.drop(columns=["buy_zone_low", "buy_zone_high"])
    show.columns = ["Symbol", "Score", "RS", "Signal", "Risk", "Conf%",
                    "Price", "Stop", "Target", "Data", "Shariah", "Buy-zone"]

    def _sig_css(v):
        c = NEON_SIG.get(v)
        if not c:
            return ""
        r, g, b = _hex_rgb(c)
        return f"background-color:rgba({r},{g},{b},0.16);color:{c};font-weight:700"

    def _risk_css(v):
        c = NEON_RISK.get(v)
        if not c:
            return ""
        r, g, b = _hex_rgb(c)
        return f"background-color:rgba({r},{g},{b},0.16);color:{c};font-weight:700"

    styled = (show.style
              .map(_sig_css, subset=["Signal"])
              .map(_risk_css, subset=["Risk"])
              .format({"Score": "{:.1f}", "RS": "{:.0f}", "Conf%": "{:.0f}",
                       "Price": "{:.2f}", "Stop": "{:.2f}", "Target": "{:.2f}"},
                      na_rep="—"))
    st.dataframe(styled, width="stretch", hide_index=True, height=560)

    bursts = db.momentum_bursts(config.STOCKS, min_pct=5.0)
    if bursts:
        with st.expander(f"🚀 Momentum-burst watchlist ({len(bursts)}) — "
                          "informational only, NOT a signal", expanded=False):
            st.caption("Today's biggest 1-day movers (e.g. circuit hits). The "
                       "scoring engine deliberately does NOT chase these — "
                       "multi-day indicators won't move on one bar, and "
                       "buying into/after a circuit is usually the worst entry. "
                       "This list doesn't feed back into any signal; it's here "
                       "so a big move is never invisible.")
            for b in bursts:
                arrow = "🔺" if b["pct_move"] > 0 else "🔻"
                clr = NEON["green"] if b["pct_move"] > 0 else NEON["red"]
                vol_txt = ""
                if b.get("avg_vol"):
                    ratio = (b["today_vol"] or 0) / b["avg_vol"]
                    vol_txt = f" · vol {ratio:.1f}× 20d avg"
                st.markdown(
                    f'{arrow} **{b["symbol"]}** '
                    f'<span style="color:{clr};font-weight:700">'
                    f'{b["pct_move"]:+.1f}%</span> '
                    f'(prev close {b["prev_close"]:.2f} → {b["price"]:.2f})'
                    f'{vol_txt} · current signal {sig_pill(b["signal"])} '
                    f'(score {fmt(b["final_score"], 0)})',
                    unsafe_allow_html=True)

with tab_port:
    st.subheader("💼 My portfolio — profit/loss & strategy")
    st.caption("Enter your holdings (symbol, shares, average buy price). Ready cash "
               f"comes from the sidebar (**PKR {capital:,}**). Your book + the "
               "engine's latest signals → a per-position action and a cash plan.")

    _seed = (pd.DataFrame(_portfolio["holdings"]) if _portfolio["holdings"]
             else pd.DataFrame([{"symbol": "", "qty": 0, "avg_cost": 0.0}]))
    _seed = _seed.reindex(columns=["symbol", "qty", "avg_cost"])
    edited = st.data_editor(
        _seed, num_rows="dynamic", hide_index=True, width="stretch", key="pf_editor",
        column_config={
            "symbol": st.column_config.TextColumn("Symbol", help="PSX ticker e.g. LUCK"),
            "qty": st.column_config.NumberColumn("Shares", min_value=0, step=1),
            "avg_cost": st.column_config.NumberColumn("Avg cost (PKR)",
                                                      min_value=0.0, format="%.2f")})
    _holds = []
    for _rec in edited.to_dict("records"):
        _sym = str(_rec.get("symbol") or "").strip().upper()
        try:
            _qty, _avg = float(_rec.get("qty") or 0), float(_rec.get("avg_cost") or 0)
        except (TypeError, ValueError):
            continue
        if _sym and _qty > 0:
            _holds.append({"symbol": _sym, "qty": _qty, "avg_cost": _avg})

    adv = portfolio_advisor.advise({"cash_pkr": capital, "holdings": _holds},
                                   latest_by_symbol)
    tot = adv["totals"]
    _plc = NEON["green"] if tot["pl"] >= 0 else NEON["red"]
    p1, p2, p3, p4 = st.columns(4)
    tile(p1, "Equity (holdings+cash)", f'PKR {tot["equity"]:,.0f}',
         f'{tot["deployed_pct"]:.0f}% deployed')
    tile(p2, "Holdings value", f'PKR {tot["market_value"]:,.0f}',
         f'cost PKR {tot["invested_cost"]:,.0f}')
    tile(p3, "Unrealised P/L",
         f'<span style="color:{_plc}">PKR {tot["pl"]:,.0f}</span>',
         (f'{tot["pl_pct"]:+.1f}%' if tot["pl_pct"] is not None else "—"))
    tile(p4, "Ready cash", f'PKR {tot["cash"]:,.0f}',
         f'PKR {tot["cash_after_plan"]:,.0f} left after plan')

    def _act_css(v):
        c = (NEON["green"] if v in ("AVERAGE DOWN", "ADD", "NEW POSITION")
             else NEON["red"] if ("EXIT" in str(v) or "TRIM" in str(v))
             else NEON["dim"])
        return f"color:{c};font-weight:700"

    def _sig_css_p(v):
        c = NEON_SIG.get(v)
        return f"color:{c};font-weight:700" if c else ""

    def _pl_css(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ""
        return f"color:{NEON['green'] if v >= 0 else NEON['red']};font-weight:700"

    if adv["holdings"]:
        st.markdown("##### Your positions")
        hd = pd.DataFrame([{
            "Symbol": h["symbol"], "Shares": h["qty"], "Avg": h["avg_cost"],
            "Price": h["price"], "Value": h["value"], "P/L PKR": h["pl"],
            "P/L %": h["pl_pct"], "Signal": h["signal"], "Action": h["action"],
            "Why": h["detail"]} for h in adv["holdings"]])
        st.dataframe(
            hd.style.map(_act_css, subset=["Action"])
            .map(_sig_css_p, subset=["Signal"])
            .map(_pl_css, subset=["P/L PKR", "P/L %"])
            .format({"Avg": "{:.2f}", "Price": "{:.2f}", "Value": "{:,.0f}",
                     "P/L PKR": "{:,.0f}", "P/L %": "{:+.1f}"}, na_rep="—"),
            width="stretch", hide_index=True)
    else:
        st.info("Add your holdings above to see profit/loss and a per-position strategy.")

    st.markdown("##### 💵 How to deploy your ready cash")
    if adv["deploy"]:
        st.caption("Best-conviction first; cash allocated in order, respecting the "
                   f"{config.RISK['max_position_pct']:.0f}% per-stock cap and "
                   f"{config.RISK['max_risk_per_trade_pct']}% per-trade risk. "
                   "AVERAGE DOWN/ADD = your existing stocks; NEW POSITION = fresh.")
        dd = pd.DataFrame([{
            "Action": d["kind"], "Symbol": d["symbol"], "Signal": d["signal"],
            "Shares": d["shares"], "≈PKR": d["value"], "Price": d["price"],
            "Score": d["score"]} for d in adv["deploy"]])
        st.dataframe(dd.style.map(_act_css, subset=["Action"])
                     .map(_sig_css_p, subset=["Signal"])
                     .format({"≈PKR": "{:,.0f}", "Price": "{:.2f}", "Score": "{:.1f}"}),
                     width="stretch", hide_index=True)
    else:
        st.caption("No Buy/Strong-Buy ideas to deploy into right now (or no spare cash).")

    with st.expander("💾 Save these holdings (persist to portfolio.json)"):
        st.caption("Streamlit Cloud can't write to the repo. Copy this into "
                   "`portfolio.json` and commit it (or send it to Claude) to persist.")
        st.code(json.dumps({"cash_pkr": capital, "holdings": _holds}, indent=2),
                language="json")

    st.divider()
    st.subheader("🛡 Book-level risk of current Buy signals")
    st.caption("Per-trade sizing caps one loss; this caps CORRELATED loss across "
               "the whole book. Buys are admitted best-score-first until a cap "
               "binds (total heat, sector exposure, or position count).")
    if not buy_cands:
        st.info("No Buy signals to assemble into a book right now.")
    else:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total heat", f'{book["heat_pct"]:.2f}%',
                  f'cap {book["max_heat_pct"]:.0f}%', delta_color="off")
        m2.metric("Deployed", f'{book["deployed_pct"]:.0f}%',
                  f'{book["cash_pct"]:.0f}% cash', delta_color="off")
        m3.metric("Positions", f'{book["open_positions"]}',
                  f'max {book["max_open_positions"]}', delta_color="off")
        m4.metric("Deferred", f'{book["deferred"]}', "capped out",
                  delta_color="off")

        cga, cgb = st.columns([1, 1.3])
        with cga:
            gmax = max(book["max_heat_pct"] * 1.6, book["heat_pct"] * 1.2, 1)
            gauge = go.Figure(go.Indicator(
                mode="gauge+number",
                value=book["heat_pct"],
                number={"suffix": "%", "font": {"color": NEON["cyan"], "size": 34}},
                title={"text": "Total portfolio heat", "font": {"color": NEON["dim"]}},
                gauge={
                    "axis": {"range": [0, gmax], "tickcolor": NEON["dim"]},
                    "bar": {"color": NEON["cyan"]},
                    "bgcolor": "rgba(0,0,0,0)",
                    "borderwidth": 0,
                    "steps": [
                        {"range": [0, book["max_heat_pct"]],
                         "color": "rgba(0,255,163,0.18)"},
                        {"range": [book["max_heat_pct"], gmax],
                         "color": "rgba(255,77,109,0.18)"}],
                    "threshold": {"line": {"color": NEON["red"], "width": 3},
                                  "thickness": 0.8, "value": book["max_heat_pct"]}}))
            st.plotly_chart(neon_fig(gauge, height=260), width="stretch")
        with cgb:
            secs = book["sector_exposure"]
            if secs:
                names = list(secs.keys())
                vals = [secs[s]["pct"] for s in names]
                colors = [NEON["red"] if v > book["max_sector_pct"] else NEON["violet"]
                          for v in vals]
                bar = go.Figure(go.Bar(x=vals, y=names, orientation="h",
                                       marker=dict(color=colors),
                                       text=[f"{v:.0f}%" for v in vals],
                                       textposition="outside"))
                bar.add_vline(x=book["max_sector_pct"], line_dash="dash",
                              line_color=NEON["red"],
                              annotation_text=f"cap {book['max_sector_pct']:.0f}%",
                              annotation_font_color=NEON["red"])
                bar.update_layout(title="Sector exposure (% of capital)")
                st.plotly_chart(neon_fig(bar, height=260), width="stretch")

        st.markdown("##### ✅ Fits the book now")
        adf = pd.DataFrame(pf["admitted"])
        if len(adf):
            adf = adf[["symbol", "signal", "sector", "shares", "value",
                       "weight_pct", "risk", "heat_pct", "score"]]
            adf.columns = ["Symbol", "Signal", "Sector", "Shares", "Value PKR",
                           "Weight%", "Risk PKR", "Heat%", "Score"]
            st.dataframe(adf.style.format(
                {"Value PKR": "{:,.0f}", "Risk PKR": "{:,.0f}",
                 "Weight%": "{:.1f}", "Heat%": "{:.2f}", "Score": "{:.1f}"}),
                width="stretch", hide_index=True)
        else:
            st.caption("None could be admitted within the caps.")

        if pf["deferred"]:
            st.markdown("##### ⏸ Deferred — a cap would be breached")
            for d in pf["deferred"]:
                st.markdown(f'{sig_pill(d["signal"])} &nbsp;**{d["symbol"]}** '
                            f'({d["sector"]}) — '
                            f'<span style="opacity:.8">{d["reason"]}</span>',
                            unsafe_allow_html=True)
        if pf["unsizable"]:
            st.caption("Un-sizable (no usable price/stop): "
                       + ", ".join(u["symbol"] for u in pf["unsizable"]))

with tab_edge:
    st.subheader("🧪 Strategy edge — backtest")
    st.caption("Replays EOD history with the technical module and reports the "
               "metrics that predict profit: expectancy, profit factor, max "
               "drawdown, plus an OUT-OF-SAMPLE verdict. Evidence, not proof.")

    def _metric_cards(m, cols):
        pf_val = m.get("profit_factor")
        cols[0].metric("Expectancy/trade", f'{m.get("expectancy_pct", 0):.2f}%')
        cols[1].metric("Profit factor",
                       "∞" if pf_val == float("inf") else fmt(pf_val, 2))
        cols[2].metric("Win rate", f'{m.get("win_rate_pct", 0):.0f}%')
        cols[3].metric("Max drawdown", f'{m.get("max_drawdown_pct", 0):.1f}%')
        cols[4].metric("Trades", f'{m.get("trades", 0)}')

    if st.button("▶ Run universe backtest (network-heavy, ~20-40s)"):
        st.session_state["run_bt"] = True
    if st.session_state.get("run_bt"):
        with st.spinner("Replaying EOD history across the universe…"):
            res = bt_portfolio()
        agg = res["aggregate"]
        if not agg.get("trades"):
            st.warning("No qualifying setups across the universe in the window.")
        else:
            st.markdown(f"**Aggregate across {res['symbols_traded']} symbols** "
                        f"— total return {agg['total_return_pct']:.1f}% over "
                        f"{agg['trades']} trades")
            _metric_cards(agg, st.columns(5))
            curve = agg.get("equity_curve") or []
            if curve:
                eq = go.Figure(go.Scatter(
                    y=[(v - 1) * 100 for v in curve], mode="lines",
                    line=dict(color=NEON["cyan"], width=2),
                    fill="tozeroy", fillcolor="rgba(0,229,255,0.10)",
                    name="Equity"))
                eq.update_layout(title="Compounded equity curve (% return)",
                                 xaxis_title="trade #", yaxis_title="cumulative %")
                st.plotly_chart(neon_fig(eq, height=320), width="stretch")

            per = res["per_symbol"]
            if per:
                pdf = pd.DataFrame(per).T.reset_index().rename(
                    columns={"index": "Symbol"})
                pdf = pdf.sort_values("expectancy_pct", ascending=False)
                pdf = pdf[["Symbol", "trades", "win_rate_pct", "expectancy_pct",
                           "profit_factor", "max_drawdown_pct",
                           "total_return_pct", "verdict"]]
                pdf.columns = ["Symbol", "Trades", "Win%", "Exp%", "PF",
                               "MaxDD%", "TotRet%", "Out-of-sample verdict"]
                st.markdown("##### Per-symbol edge (sorted by expectancy)")
                st.dataframe(pdf.style.format(
                    {"Win%": "{:.0f}", "Exp%": "{:.2f}", "PF": "{:.2f}",
                     "MaxDD%": "{:.1f}", "TotRet%": "{:.1f}"}, na_rep="—"),
                    width="stretch", hide_index=True, height=460)
        st.caption("⚠ " + res["warning"])
    else:
        st.info("Click the button to run the backtest. Results are cached for an "
                "hour. You can also backtest a single stock in the Stock detail tab.")

with tab_stock:
    sym = st.selectbox("Stock", config.STOCKS)
    r = db.last_run(sym)
    if r:
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Signal", r["signal"], f"{fmt(r['confidence'], 0)}% conf")
        c2.metric("Final score", fmt(r["final_score"], 1))
        c3.metric("Rel. strength", fmt(r.get("relative_strength"), 0))
        c4.metric("Price", fmt(r["price"]))
        c5.metric("Risk", r["risk_level"])
        st.write("**Why:**", r["main_reason"])
        st.write("**Main risk:**", r["main_risk"])
        st.write("**Shariah:**", r["shariah_status"], " · **Market regime:**",
                 r.get("market_regime") or "—")
        _news_window(sym, news_feed.get(sym))

    eod, meta = data_fetcher.fetch_eod(sym)
    if eod is not None:
        st.caption(f"Source: {meta['source']} (as of {meta['as_of']})")
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=eod["date"], y=eod["close"], name="Close",
                                 line=dict(color=NEON["cyan"], width=2)))
        fig.add_trace(go.Scatter(x=eod["date"], y=eod["close"].ewm(span=20).mean(),
                                 name="EMA20",
                                 line=dict(color=NEON["amber"], dash="dot")))
        fig.add_trace(go.Scatter(x=eod["date"], y=eod["close"].ewm(span=50).mean(),
                                 name="EMA50",
                                 line=dict(color=NEON["violet"], dash="dash")))
        if r:
            for lvl, nm, clr in ((r["support"], "Support", NEON["green"]),
                                 (r["resistance"], "Resistance", NEON["red"]),
                                 (r["stop_loss"], "Stop", NEON["red"])):
                if lvl:
                    fig.add_hline(y=lvl, line_dash="dot", line_color=clr,
                                  annotation_text=nm,
                                  annotation_font_color=clr)
        fig.update_layout(title=f"{sym} — price & moving averages")
        st.plotly_chart(neon_fig(fig, height=420), width="stretch")
        volf = go.Figure(go.Bar(x=eod["date"], y=eod["volume"], name="Volume",
                                marker=dict(color="rgba(0,229,255,0.5)")))
        volf.update_layout(title="Volume")
        st.plotly_chart(neon_fig(volf, height=220), width="stretch")
    else:
        st.error(meta.get("warning", "No price data."))

    with st.expander("🧪 Backtest this stock (expectancy / profit factor / OOS)"):
        if st.button(f"Run backtest for {sym}", key="bt_one"):
            with st.spinner(f"Backtesting {sym}…"):
                res = bt_symbol(sym)
            if res.get("error") or not res.get("trades"):
                st.warning(res.get("note") or res.get("error")
                           or "No qualifying setups.")
            else:
                w = res["window"]
                st.caption(f"{w['bars']} bars · {w['from']} → {w['to']}")
                cc = st.columns(5)
                cc[0].metric("Expectancy/trade", f'{res["expectancy_pct"]:.2f}%')
                cc[1].metric("Profit factor",
                             "∞" if res["profit_factor"] == float("inf")
                             else fmt(res["profit_factor"], 2))
                cc[2].metric("Win rate", f'{res["win_rate_pct"]:.0f}%')
                cc[3].metric("Max drawdown", f'{res["max_drawdown_pct"]:.1f}%')
                cc[4].metric("Trades", f'{res["trades"]}')
                verdict_clr = (NEON["green"] if "HOLDS" in res["verdict"]
                               else NEON["red"] if "does NOT" in res["verdict"]
                               else NEON["amber"])
                st.markdown(
                    f'**Out-of-sample:** <span style="color:{verdict_clr}">'
                    f'{res["verdict"]}</span>', unsafe_allow_html=True)
                oos, is_ = res["out_of_sample"], res["in_sample"]
                st.caption(
                    f"In-sample exp {is_.get('expectancy_pct', '—')}% "
                    f"(PF {is_.get('profit_factor', '—')}) vs "
                    f"out-of-sample exp {oos.get('expectancy_pct', '—')}% "
                    f"(PF {oos.get('profit_factor', '—')})")
                wf = res.get("walk_forward") or []
                if wf:
                    wdf = pd.DataFrame(wf)
                    st.markdown("Walk-forward folds:")
                    st.dataframe(wdf, width="stretch", hide_index=True)
                st.caption("⚠ " + res["warning"])

with tab_hist:
    sym = st.selectbox("Stock ", config.STOCKS, key="hist")
    hist = pd.DataFrame(db.run_history(sym, 300))
    if len(hist):
        hist["run_time"] = pd.to_datetime(hist["run_time"])
        cols = [c for c in ["final_score", "technical_score", "relative_strength"]
                if c in hist.columns]
        st.line_chart(hist.set_index("run_time")[cols])
        st.subheader("Signal history")
        st.dataframe(hist[["run_time", "signal", "confidence", "price", "outcome"]],
                     width="stretch", hide_index=True)

with tab_news:
    for n in db.recent_news(72)[:40]:
        tag = f" `[{n['symbols']}]`" if n["symbols"] else ""
        st.markdown(f"- **{n['source']}** — {n['title']}{tag}")

with tab_reports:
    if os.path.isdir(config.REPORT_DIR):
        files = sorted(os.listdir(config.REPORT_DIR), reverse=True)[:10]
        pick = st.selectbox("Saved reports", files) if files else None
        if pick:
            with open(os.path.join(config.REPORT_DIR, pick), encoding="utf-8") as f:
                st.markdown(f.read())
    else:
        st.info("No reports saved yet.")
