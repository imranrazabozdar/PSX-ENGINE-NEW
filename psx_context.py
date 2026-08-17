#!/usr/bin/env python3
"""
psx_context.py — the layers your engine was missing above the chart.

Four jobs, all ported from the public engine's approach but rebuilt on your
own data plumbing (psx_report / psx_live) instead of its config.py:

  1. REGIME GATE      — is the index above its own 50-EMA? Buying stocks in a
                        risk-off tape is the single most reliable way to lose
                        money with a good chart, so the gate softens verdicts
                        rather than letting them stand alone.
  2. RELATIVE STRENGTH— blended 1m/3m/6m excess return vs the index, scored
                        0-100. Your psx_brain only looked at 3 months.
  3. SHARIAH SCREEN   — KMI-30 / KMI All-Share membership, with an explicit
                        "needs manual verification" state. Never assumed.
  4. FUNDAMENTALS     — P/E, ROE, D/E, yield, EPS growth scored against SECTOR
                        PEERS rather than absolute thresholds, because a P/E of
                        4 means different things for a bank and a pharma name.

Everything fails soft and says so. A missing input produces None and a note,
never a fabricated number.
"""

import json
import os
from datetime import datetime

import numpy as np
import pandas as pd

import psx_live

# --------------------------------------------------------------------------
# configuration you will want to edit
# --------------------------------------------------------------------------

REGIME_EMA = 50
RS_LOOKBACKS = {"1m": 21, "3m": 63, "6m": 126}
RS_WEIGHTS = {"1m": 0.25, "3m": 0.40, "6m": 0.35}

FUND_FILE = os.environ.get("FUND_FILE", "fundamentals.json")
SHARIAH_FILE = os.environ.get("SHARIAH_FILE", "shariah.json")

# KMI-30 snapshot. UPDATE THIS from the PSX notification after each
# semi-annual recomposition — the module warns once it goes stale.
KMI30 = {
    "MEBL", "LUCK", "FFC", "OGDC", "MARI", "PPL", "POL", "SYS", "ENGROH",
    "HUBC", "PSO", "SNGP", "SEARL", "AGP", "MTL", "INDU", "PIOC", "KOHC",
    "MLCF", "FCCL", "ATRL", "NRL", "EFERT", "FATIMA", "TRG", "AIRLINK",
    "GHNI", "THALL", "NATF", "ILP",
}
KMI30_AS_OF = "2025-12-31"
KMI30_SOURCE = "PSX KMI-30 recomposition notification"
STALE_DAYS = 200

# Names verified compliant by a route other than KMI-30 membership.
OTHER_COMPLIANT = {
    "FABL": {"reason": "converted to a full Islamic bank",
             "verify": "re-check the conversion status and latest KMI All-Share list"},
}

SCREENING_CRITERIA = [
    "core business must be halal",
    "interest-bearing debt / total assets below 37%",
    "illiquid assets / total assets above 25%",
    "net liquid assets per share below the market price",
    "non-compliant investments / total assets below 33%",
    "non-compliant income / total revenue below 5%",
]


# --------------------------------------------------------------------------
# 1. regime gate
# --------------------------------------------------------------------------

def assess_regime(bench=None, span=REGIME_EMA):
    """Index vs its own EMA. Fails OPEN: unknown regime never blocks anything."""
    if bench is None or len(bench) < span:
        return {"regime": "unknown", "level": None, "ema": None,
                "pct_above": None, "gate": "off",
                "note": "Benchmark unavailable, so the regime gate is off for "
                        "this run. Verdicts are unmodified — treat them as "
                        "having no market context behind them."}
    c = pd.Series(bench).astype(float).dropna()
    ema = c.ewm(span=span, adjust=False).mean()
    level, e = float(c.iloc[-1]), float(ema.iloc[-1])
    pct = (level / e - 1) * 100
    regime = "risk-on" if level >= e else "risk-off"
    return {
        "regime": regime, "level": round(level, 2), "ema": round(e, 2),
        "pct_above": round(pct, 2), "gate": "on",
        "note": (f"The index at {level:,.0f} is {abs(pct):.1f}% "
                 f"{'above' if regime == 'risk-on' else 'below'} its {span}-EMA "
                 f"({e:,.0f}), so the tape is {regime}. "
                 + ("Breadth is behind you: normal position sizes are defensible."
                    if regime == "risk-on" else
                    "In a risk-off tape most breakouts fail regardless of how "
                    "good the individual chart looks. Buys are softened to "
                    "Watch and sizes cut.")),
    }


def apply_regime_gate(verdict, regime):
    """Soften long verdicts in a risk-off tape. Returns (verdict, note|None)."""
    if regime not in ("risk-off",):
        return verdict, None
    if verdict in ("BUY", "BUY ON TRIGGER"):
        return "WAIT", ("Regime gate: downgraded from " + verdict +
                        " because the index is below its 50-EMA. The setup is "
                        "not wrong — the tape is against it.")
    return verdict, None


# --------------------------------------------------------------------------
# 2. relative strength
# --------------------------------------------------------------------------

def relative_strength(close, bench):
    """Blended excess return over 1m/3m/6m, mapped to a 0-100 score.

    50 means the stock tracks the index. Above 50 is leadership. None when
    there isn't enough overlapping history — never a guessed 50.
    """
    if bench is None or close is None or len(close) < 30:
        return None
    s = pd.Series(close).astype(float)
    b = pd.Series(bench).astype(float).reindex(s.index).ffill().dropna()
    s = s.reindex(b.index).dropna()
    if len(s) < 30:
        return None

    rel, num, wsum = {}, 0.0, 0.0
    for name, w in RS_LOOKBACKS.items():
        if len(s) <= w:
            continue
        sr = float(s.iloc[-1]) / float(s.iloc[-1 - w]) - 1
        br = float(b.iloc[-1]) / float(b.iloc[-1 - w]) - 1
        ex = sr - br
        rel[name] = round(ex * 100, 1)
        wt = RS_WEIGHTS.get(name, 0)
        num += wt * ex
        wsum += wt
    if wsum == 0:
        return None
    blend = num / wsum
    score = float(np.clip(50 + blend * 200, 0, 100))
    if score >= 70:
        word = "clear leadership"
    elif score >= 55:
        word = "mild outperformance"
    elif score > 45:
        word = "tracking the index"
    elif score > 30:
        word = "lagging"
    else:
        word = "severe relative weakness"
    return {
        "rs_score": round(score, 1), "blended_pct": round(blend * 100, 1),
        "rel": rel, "outperforming": blend > 0, "word": word,
        "note": (f"Relative strength {score:.0f}/100 — {word}. Excess return vs "
                 f"the index: " + ", ".join(f"{k} {v:+.1f}%" for k, v in rel.items())
                 + ". Money is made owning leaders; a great chart on a laggard "
                   "usually means the sector is doing the work, not the company."),
    }


# --------------------------------------------------------------------------
# 3. shariah screen
# --------------------------------------------------------------------------

def _load_json(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default


def _stale_note():
    try:
        age = (datetime.now() - datetime.fromisoformat(KMI30_AS_OF)).days
    except Exception:
        return "The KMI-30 verification date could not be parsed — re-verify."
    if age > STALE_DAYS:
        return (f"The KMI-30 snapshot in psx_context.py is {age} days old. The "
                f"index is recomposed semi-annually, so re-verify against the "
                f"latest PSX notification before relying on this.")
    return None


def shariah(symbol):
    """Compliant / needs-verification, with the source stated. Never assumed."""
    sym = symbol.upper()
    notes = []
    stale = _stale_note()
    if stale:
        notes.append(stale)

    extra = _load_json(SHARIAH_FILE, {})
    if sym in {k.upper() for k in extra.get("compliant", [])}:
        return {"symbol": sym, "status": "Compliant (your own verified list)",
                "compliant": True, "source": SHARIAH_FILE, "notes": notes}

    if sym in KMI30:
        return {"symbol": sym, "status": "Compliant (KMI-30 constituent)",
                "compliant": True,
                "source": f"{KMI30_SOURCE}, effective {KMI30_AS_OF}",
                "notes": notes}

    if sym in OTHER_COMPLIANT:
        e = OTHER_COMPLIANT[sym]
        notes.append(e["verify"])
        return {"symbol": sym, "status": "Compliant (non-KMI-30 route)",
                "compliant": True, "source": e["reason"], "notes": notes}

    notes.append("Not found in the KMI-30 snapshot or your own verified list. "
                 "That is NOT a finding of non-compliance — it means this engine "
                 "has no evidence either way. Check the current KMI All-Share "
                 "list before treating it as either.")
    return {"symbol": sym, "status": "Needs manual verification",
            "compliant": None, "source": "none", "notes": notes}


# --------------------------------------------------------------------------
# 4. fundamentals, scored against sector peers
# --------------------------------------------------------------------------

def _peer_percentile(values, x, lower_is_better):
    vals = [v for v in values if v is not None and np.isfinite(v)]
    if len(vals) < 3 or x is None or not np.isfinite(x):
        return None
    pct = float(np.mean([x <= v for v in vals]) * 100)
    return pct if lower_is_better else 100 - pct


def fundamentals(symbol, sector_peers=None, live=True):
    """Peer-relative fundamental read.

    sector_peers: list of symbols in the same sector, so P/E and yield are
    judged against comparable companies rather than a market-wide average.
    Returns score 0-100 with `low_confidence` set whenever the inputs are thin.
    """
    sym = symbol.upper()
    cache = _load_json(FUND_FILE, {})
    data = cache.get("data", cache) if isinstance(cache, dict) else {}
    row = dict(data.get(sym) or {})

    src = [FUND_FILE] if row else []
    if live:
        y = psx_live.yields(sym)
        if y:
            src.append("psxterminal /api/yields")
            for k_local, k_api in [("pe", "pe"), ("div_yield", "dividendYield"),
                                   ("mcap", "marketCap"),
                                   ("free_float", "freeFloat"),
                                   ("price", "price"),
                                   ("volume30Avg", "volume30Avg")]:
                v = y.get(k_api)
                if v is not None:
                    row.setdefault(k_local, v)

    if not row:
        return {"symbol": sym, "score": None, "low_confidence": True,
                "metrics": {}, "sources": [], "notes": [
                    "No fundamental data available for this symbol from either "
                    "the local cache or the live feed. The technical read stands "
                    "on its own — do not read absence as a negative."]}

    def num(k):
        try:
            v = float(row.get(k))
            return v if np.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    pe, roe, de = num("pe"), num("roe"), num("de")
    dy, eg = num("div_yield"), num("eps_growth")
    metrics = {"pe": pe, "roe": roe, "de": de, "div_yield": dy,
               "eps_growth": eg, "market_cap": num("mcap"),
               "free_float": num("free_float")}

    peers = {}
    if sector_peers:
        for p in sector_peers:
            if p.upper() == sym:
                continue
            pr = data.get(p.upper())
            if isinstance(pr, dict):
                peers[p.upper()] = pr

    parts, notes = [], []

    def add(label, val, key, lower_better, lo, hi, weight):
        """Score a metric peer-relatively where possible, absolutely otherwise."""
        if val is None:
            return
        pct = _peer_percentile([_safe(peers[p].get(key)) for p in peers],
                              val, lower_better) if peers else None
        if pct is not None:
            parts.append((pct, weight))
            notes.append(f"{label} {val:g} sits at the {pct:.0f}th percentile of "
                         f"{len(peers)} sector peers")
        else:
            span = (hi - lo) or 1
            raw = float(np.clip((val - lo) / span, 0, 1)) * 100
            sc = 100 - raw if lower_better else raw
            parts.append((sc, weight))
            notes.append(f"{label} {val:g} scored on an absolute scale "
                         f"({lo:g}-{hi:g}) — no peer data to compare against")

    add("P/E", pe, "pe", True, 3, 30, 1.2)
    add("ROE %", roe, "roe", False, 0, 30, 1.2)
    add("Debt/Equity", de, "de", True, 0, 2.5, 1.0)
    add("Dividend yield %", dy, "div_yield", False, 0, 12, 0.8)
    add("EPS growth %", eg, "eps_growth", False, -20, 60, 1.0)

    if not parts:
        return {"symbol": sym, "score": None, "low_confidence": True,
                "metrics": metrics, "sources": src,
                "notes": ["Fundamental fields present but all unusable."]}

    wsum = sum(w for _, w in parts)
    score = round(sum(v * w for v, w in parts) / wsum, 1)
    thin = len(parts) < 3 or not peers

    if pe is not None and pe < 0:
        notes.append("Negative P/E means the company is loss-making. A cheap "
                     "multiple on no earnings is not cheap.")
    if de is not None and de > 2:
        notes.append(f"Debt/equity of {de:g} is heavy. Leveraged names move "
                     "further than the market in both directions.")
    if thin:
        notes.append("Thin inputs or no sector peers, so treat this score as "
                     "indicative only.")
    notes.append("Sources: " + ", ".join(src) +
                 ". Verify against the company's own filing before acting.")

    return {"symbol": sym, "score": score, "low_confidence": thin,
            "metrics": metrics, "peers_used": len(peers),
            "sources": src, "notes": notes}


def _safe(v):
    try:
        f = float(v)
        return f if np.isfinite(f) else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# combined context for one symbol
# --------------------------------------------------------------------------

def for_symbol(symbol, daily, bench=None, sector_peers=None, regime=None):
    """One call for everything above. `regime` can be passed in to avoid
    recomputing it for every symbol in a scan."""
    reg = regime if regime is not None else assess_regime(bench)
    return {
        "regime": reg,
        "rs": relative_strength(daily.close if daily is not None else None, bench),
        "shariah": shariah(symbol),
        "fundamentals": fundamentals(symbol, sector_peers),
    }
