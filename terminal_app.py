#!/usr/bin/env python3
"""
terminal_app.py — Research Terminal 2.0 as a Streamlit app.

WHY THIS EXISTS: `psx_pro_v2.py` is a Flask application. Streamlit Community
Cloud can only serve Streamlit scripts, so the Flask terminal cannot run
there — pointing Streamlit Cloud at `psx_pro_v2.py` makes it execute
`app.run()` and hang forever on a port nothing proxies. This module exposes
the same v2.0 capability (Wyckoff structure, composite verdict, market
context, risk layer, signal journal) through Streamlit instead.

It is a SEPARATE entry point. `app.py` / `dashboard.py` — the Shariah engine
dashboard — are untouched and still work exactly as before. Point Streamlit
Cloud at whichever of the two you want to serve.

Nothing here fabricates data. When PSX is unreachable every panel says so
rather than rendering an empty market as a calm one.
"""

import hashlib
import os
import traceback

import pandas as pd
import streamlit as st

import psx_brain
import psx_context
import psx_memory
import psx_report
import psx_risk
import psx_verdict
import psx_wyckoff

st.set_page_config(page_title="PSX Research Terminal 2.0",
                   page_icon="📐", layout="wide")

CAPITAL = float(os.environ.get("CAPITAL", 1_000_000))
DEFAULT_WATCH = ["PSO", "MARI", "OGDC", "MEBL", "LUCK", "FFC", "PPL", "ENGROH"]


# --------------------------------------------------------------------------
# auth — same mechanism as dashboard.py so one password covers both apps
# --------------------------------------------------------------------------

def _password():
    try:
        return st.secrets["DASHBOARD_PASSWORD"]
    except Exception:
        return os.environ.get("DASHBOARD_PASSWORD")


def _require_password():
    pw = _password()
    if not pw:
        return
    token = hashlib.sha256(("psx-dash:" + str(pw)).encode()).hexdigest()[:32]
    if st.session_state.get("auth_ok") or st.query_params.get("k") == token:
        st.session_state["auth_ok"] = True
        return
    st.title("🔒 PSX Research Terminal")
    entered = st.text_input("Enter dashboard password", type="password")
    if entered == pw:
        st.session_state["auth_ok"] = True
        st.query_params["k"] = token
        st.rerun()
    elif entered:
        st.error("Incorrect password.")
    st.stop()


_require_password()


# --------------------------------------------------------------------------
# data access
# --------------------------------------------------------------------------

@st.cache_data(ttl=900, show_spinner=False)
def load_daily(symbol, years=3):
    """Cached OHLCV. Returns (df, error) so the caller can explain a failure."""
    try:
        return psx_report.load_from_psx(symbol, years), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


@st.cache_data(ttl=900, show_spinner=False)
def load_bench():
    try:
        import psxdata
        k = psxdata.indices("KSE100")
        if k is not None and "close" in getattr(k, "columns", []):
            return k.close, None
        return None, "KSE100 returned no close series."
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def watchlist():
    raw = st.session_state.get("watch")
    return raw if raw else DEFAULT_WATCH


def _cell(v):
    """Flatten a nested cell for display.

    Wyckoff event rows carry `criteria` as a list of (label, bool) pairs — the
    per-criterion tick-boxes that justify a High/Medium/Low grade. Arrow cannot
    serialise a column of lists, so render them as text instead of dropping the
    most informative field on the row.
    """
    if isinstance(v, (list, tuple)):
        out = []
        for x in v:
            if isinstance(x, (list, tuple)) and len(x) == 2:
                out.append(("✓ " if x[1] else "✗ ") + str(x[0]))
            else:
                out.append(str(x))
        return " · ".join(out)
    if isinstance(v, dict):
        return " · ".join(f"{k}={v[k]}" for k in v)
    return v


def _show_table(rows):
    """Render tabular module output, falling back rather than blanking a tab."""
    if isinstance(rows, dict):
        rows = [rows]
    if not rows:
        return
    try:
        st.dataframe(pd.DataFrame([{k: _cell(v) for k, v in r.items()}
                                   for r in rows]),
                     width="stretch", hide_index=True)
    except Exception:
        st.json(rows)


def _psx_warning(err):
    st.error(
        f"**PSX is unreachable from this server.** {err}\n\n"
        "PSX blocks datacentre IP ranges, which includes Streamlit Cloud. "
        "Cached bars are used where available; where they are not, the panel "
        "below is empty rather than filled with guesses.")


# --------------------------------------------------------------------------
# panels
# --------------------------------------------------------------------------

def panel_deep_read(sym, bench):
    df, err = load_daily(sym)
    if df is None:
        _psx_warning(err)
        return
    try:
        v = psx_verdict.analyse(sym, df, bench, capital=CAPITAL,
                                weekly_wyckoff=True, memory=psx_memory)
    except Exception as e:
        st.error(f"Composite read failed: {e}")
        st.code(traceback.format_exc(), language="text")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(sym, f"{v['price']:,.2f}")
    c2.metric("Verdict", v["verdict"],
              delta=None if v["verdict"] == v["verdict_v1"]
              else f"was {v['verdict_v1']}", delta_color="inverse")
    c3.metric("Composite", f"{v['composite']}/100")
    c4.metric("Confidence", f"{v['confidence']}/100")

    if v["verdict"] != v["verdict_v1"]:
        st.warning("**Downgraded from " + v["verdict_v1"] + " to " +
                   v["verdict"] + ".** " + " ".join(v["downgrades"]))

    st.info(v["summary"])

    s = v["scores"]
    st.caption("Section scores (the composite can only downgrade, never upgrade)")
    st.dataframe(pd.DataFrame([{
        "chart": s["chart"], "structure": s["structure"],
        "fundamentals": s["fundamentals"],
        "agreement": v["agreement"]}]), width="stretch", hide_index=True)

    lv = v["levels"]
    st.subheader("Trade plan")
    st.dataframe(pd.DataFrame([lv]), width="stretch", hide_index=True)

    rk = v.get("risk") or {}
    if rk.get("sizing"):
        z = rk["sizing"]
        st.subheader(f"Sizing on Rs {z['capital']:,} — risk level {rk['risk_level']}")
        a, b, c = st.columns(3)
        a.metric("Shares", f"{z['shares']:,}")
        b.metric("Position", f"Rs {z['position_pkr']:,} ({z['position_pct']}%)")
        c.metric("Loss if stop fills",
                 f"Rs {z['max_loss_pkr']:,} ({z['max_loss_pct']}%)")
    if rk.get("vetoes"):
        st.error("Risk vetoes: " + ", ".join(rk["vetoes"]))
    for w in rk.get("warnings") or []:
        st.caption("• " + w)

    for title, key in (("Supporting the trade", "bull"),
                       ("Against the trade", "bear"),
                       ("Watch out", "flags")):
        items = v.get(key) or []
        if items:
            with st.expander(f"{title} ({len(items)})", expanded=(key == "flags")):
                for x in items:
                    st.write("• " + x)

    ctx = v.get("context") or {}
    sh = ctx.get("shariah") or {}
    if sh:
        st.subheader("Shariah")
        st.write(sh.get("status", "unknown"))
        for n in sh.get("notes") or []:
            st.caption("• " + n)

    try:
        psx_memory.record(v, v.get("wyckoff"), ctx, rk)
        st.caption("Journalled to the signal ledger for later grading.")
    except Exception as e:
        st.caption(f"Not journalled: {e}")


def panel_wyckoff(sym, bench):
    df, err = load_daily(sym)
    if df is None:
        _psx_warning(err)
        return
    tf = st.radio("Timeframe", ["daily", "weekly"], horizontal=True,
                  key="wyck_tf")
    bars = df if tf == "daily" else psx_report.to_weekly(df)
    try:
        w = psx_wyckoff.analyse(sym, bars, tf, bench)
    except Exception as e:
        st.error(f"Wyckoff read failed: {e}")
        return

    if not w.get("ok") or not w.get("range"):
        st.info(w.get("read") or
                "No trading range identified. That is a normal output — the "
                "module refuses to label structure that is not there.")
        return

    R = w["range"]
    a, b, c, d = st.columns(4)
    a.metric("Structure", w.get("structure", "?"))
    b.metric("Phase", w.get("phase", "?"))
    c.metric("Range", f"{R['support']:,.2f}–{R['resistance']:,.2f}")
    d.metric("Position in range", f"{w.get('position_in_range', '?')}%")
    st.progress(min(max(float(w.get("position_in_range") or 0) / 100, 0.0), 1.0))
    st.caption(f"Bias {w.get('bias', '?')} · confidence {w.get('confidence', '?')}")

    if w.get("read"):
        st.text(w["read"])

    for label, key in (("Springs", "springs"), ("Upthrusts", "upthrusts"),
                       ("Signs of strength", "sos"), ("Signs of weakness", "sow")):
        rows = w.get(key) or []
        if rows:
            with st.expander(f"{label} ({len(rows)})"):
                _show_table(rows)


def panel_scan(bench):
    syms = watchlist()
    st.caption(f"Screening {len(syms)} names. Each row is a full indicator "
               f"read; PSX rate-limits, so this is not instant.")
    if not st.button("Run scan", type="primary"):
        return
    rows, failed = [], []
    bar = st.progress(0.0)
    for i, s in enumerate(syms, start=1):
        df, err = load_daily(s, 2)
        if df is None:
            failed.append(s)
        else:
            try:
                r = psx_brain.analyse(s, df, bench, "drop")
                rows.append({"sym": r["symbol"], "price": r["price"],
                             "verdict": r["verdict"], "SCORE": r["score"],
                             "conf": r["confidence"],
                             "dTrend": r["state"]["dTrend"],
                             "cloud": r["state"]["cloud"],
                             "dVol": r["state"]["dVol"],
                             "wVol": r["state"]["wVol"],
                             "trigger": r["levels"]["trigger"],
                             "stop": r["levels"]["stop"]})
            except Exception:
                failed.append(s)
        bar.progress(i / len(syms))
    bar.empty()

    if not rows:
        st.error("Nothing could be read. PSX is very likely blocking this host "
                 "— check that a cache exists.")
        return
    rows.sort(key=lambda r: r["SCORE"], reverse=True)
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    st.text(psx_report.market_breadth(rows))
    if failed:
        st.caption("No data for: " + ", ".join(failed))
    st.session_state["scan_rows"] = rows


def panel_book(bench):
    rows = st.session_state.get("scan_rows")
    if not rows:
        st.info("Run a scan first — the book sizes whatever the scan ranked.")
        return
    cands = [{"symbol": r["sym"], "score": r["SCORE"], "verdict": r["verdict"],
              "price": r["price"], "stop": r["stop"], "sector": "Unknown"}
             for r in rows if r["verdict"] in ("BUY", "BUY ON TRIGGER")]
    if not cands:
        st.info("No BUY candidates in the last scan, so there is no book to "
                "size. An empty book is a position.")
        return
    out = psx_risk.book(cands, capital=CAPITAL)
    st.text(out["book"]["text"])
    if out["admitted"]:
        st.subheader(f"Admitted ({len(out['admitted'])})")
        st.dataframe(pd.DataFrame(out["admitted"]), width="stretch",
                     hide_index=True)
    if out["deferred"]:
        st.subheader(f"Deferred by a cap ({len(out['deferred'])})")
        st.dataframe(pd.DataFrame(out["deferred"]), width="stretch",
                     hide_index=True)
    if out["unsizable"]:
        st.subheader(f"Unsizable ({len(out['unsizable'])})")
        st.dataframe(pd.DataFrame(out["unsizable"]), width="stretch",
                     hide_index=True)


def panel_track():
    try:
        psx_memory.init()
        hist = psx_memory.history(limit=300)
        acc = psx_memory.accuracy()
    except Exception as e:
        st.error(f"Journal unavailable: {e}")
        return

    if st.button("Grade pending verdicts"):
        try:
            r = psx_memory.grade(lambda s: psx_report.load_from_psx(s, 1))
            st.success(f"Graded {r.get('graded')}, skipped {r.get('skipped')} "
                       f"(not enough forward bars yet).")
        except Exception as e:
            st.error(f"Grading failed: {e}")

    if acc:
        st.subheader("Verdict accuracy")
        _show_table(acc)
        st.caption("Anything under 20 observations is noise, not edge.")
    if hist:
        st.subheader(f"Journal ({len(hist)} entries)")
        _show_table(hist)
    else:
        st.info("The journal is empty. Open a Deep Read to record a verdict. "
                "On Render's or Streamlit's ephemeral disk it resets on every "
                "redeploy unless you attach persistent storage.")


# --------------------------------------------------------------------------
# layout
# --------------------------------------------------------------------------

st.title("📐 PSX Research Terminal 2.0")
st.caption("Chart read + Wyckoff structure + market context + risk layer + "
           "signal journal. Decision support only — confirm every level "
           "manually before placing an order.")

st.warning(
    "**The verdict thresholds in `psx_brain` are unvalidated.** The indicators "
    "are standard published formulas and are arithmetically correct, but the "
    "weights and cutoffs that turn them into BUY/AVOID have no backtest behind "
    "them. See `DEPLOY_TERMINAL.md`.", icon="⚠️")

bench, bench_err = load_bench()
with st.sidebar:
    st.header("Watchlist")
    txt = st.text_area("One symbol per line",
                       value="\n".join(watchlist()), height=220)
    st.session_state["watch"] = [x.strip().upper()
                                 for x in txt.splitlines() if x.strip()]
    st.metric("Capital", f"Rs {CAPITAL:,.0f}")
    st.caption("Set CAPITAL in the environment to change it.")
    if bench is None:
        st.error("KSE100 benchmark unavailable — regime gate and relative "
                 "strength are OFF for this session.")
        st.caption(bench_err or "")
    else:
        reg = psx_context.assess_regime(bench)
        st.metric("Regime", reg["regime"])
        st.caption(reg["note"])

sym = st.selectbox("Symbol", watchlist(), index=0)

t1, t2, t3, t4, t5 = st.tabs(["🔬 Deep read", "📐 Wyckoff", "🔎 Scan",
                              "⚖️ Book risk", "📓 Track record"])
with t1:
    panel_deep_read(sym, bench)
with t2:
    panel_wyckoff(sym, bench)
with t3:
    panel_scan(bench)
with t4:
    panel_book(bench)
with t5:
    panel_track()
