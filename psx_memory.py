#!/usr/bin/env python3
"""
psx_memory.py — the engine remembers what it said and finds out if it was right.

Your v1 was stateless: every scan started from zero and no verdict was ever
graded. This module gives it a memory, in a single SQLite file, with three jobs:

  1. JOURNAL every verdict at the moment it is made, with the price and levels
     that were true at the time.
  2. GRADE it later against what price actually did at +1/+3/+7/+20 sessions,
     using a strict rule for BUY/AVOID and a survival rule for WAIT/HOLD.
  3. FEED THAT BACK as a CONFIDENCE adjustment — never as a change to the
     weights. If your Springs keep failing, confidence falls; the model that
     produced them stays intact so you can see it failing.

Plus a plain positions ledger so the dashboard can show real P/L rather than
a hypothetical.

Small samples are called small. Ten graded signals is not evidence.
"""

import os
import sqlite3
from contextlib import closing
from datetime import datetime

DB = os.environ.get("PSX_DB", "psx_v2.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_time   TEXT NOT NULL,
  symbol     TEXT NOT NULL,
  verdict    TEXT,
  score      REAL,
  confidence REAL,
  price      REAL,
  stop       REAL,
  t1 REAL, t2 REAL,
  wyckoff_phase TEXT,
  wyckoff_structure TEXT,
  regime     TEXT,
  rs_score   REAL,
  risk_level TEXT,
  d1 REAL, d3 REAL, d7 REAL, d20 REAL,
  outcome    TEXT,
  graded_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_signals_sym ON signals(symbol, run_time);

CREATE TABLE IF NOT EXISTS feature_hits (
  feature TEXT NOT NULL,
  symbol  TEXT NOT NULL,
  hits    INTEGER DEFAULT 0,
  misses  INTEGER DEFAULT 0,
  PRIMARY KEY (feature, symbol)
);

CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol   TEXT NOT NULL,
  qty      REAL NOT NULL,
  avg_cost REAL NOT NULL,
  opened   TEXT,
  closed   TEXT,
  exit_px  REAL,
  note     TEXT
);
"""

# BUY and AVOID are graded strictly. WAIT/HOLD are graded on "did it avoid
# damage", which is survival rather than edge — so they are excluded from the
# confidence maths below, or every symbol drifts to the confidence cap.
STRICT = ("BUY", "BUY ON TRIGGER", "AVOID")


def conn():
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def init():
    with closing(conn()) as c:
        c.executescript(SCHEMA)
        c.commit()


# --------------------------------------------------------------------------
# 1. journal
# --------------------------------------------------------------------------

def record(res, wyckoff=None, context=None, risk=None):
    """Store one verdict. `res` is a psx_brain.analyse result."""
    init()
    L = res.get("levels") or {}
    w = wyckoff or {}
    ctx = context or {}
    row = {
        "run_time": datetime.now().isoformat(timespec="seconds"),
        "symbol": res["symbol"], "verdict": res.get("verdict"),
        "score": res.get("score"), "confidence": res.get("confidence"),
        "price": res.get("price"), "stop": L.get("stop"),
        "t1": L.get("t1"), "t2": L.get("t2"),
        "wyckoff_phase": w.get("phase"),
        "wyckoff_structure": w.get("structure"),
        "regime": (ctx.get("regime") or {}).get("regime"),
        "rs_score": (ctx.get("rs") or {}).get("rs_score"),
        "risk_level": (risk or {}).get("risk_level"),
    }
    cols = ",".join(row)
    qs = ",".join("?" * len(row))
    with closing(conn()) as c:
        cur = c.execute(f"INSERT INTO signals ({cols}) VALUES ({qs})",
                        list(row.values()))
        c.commit()
        return cur.lastrowid


def features_seen(symbol, features):
    """Register which features were bullish at signal time, for later grading.

    features: {"spring": True, "sos": True, "above_cloud": False, ...}
    """
    init()
    with closing(conn()) as c:
        for k, v in (features or {}).items():
            if v:
                c.execute("INSERT OR IGNORE INTO feature_hits (feature, symbol) "
                          "VALUES (?,?)", (f"f_{k}", symbol))
        c.commit()


# --------------------------------------------------------------------------
# 2. grade
# --------------------------------------------------------------------------

def pending(max_age_days=60):
    """Signals that still need a forward price filled in."""
    init()
    with closing(conn()) as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM signals WHERE outcome IS NULL "
            "AND julianday('now') - julianday(run_time) <= ? "
            "ORDER BY run_time", (max_age_days,))]


def grade(loader, horizon=7, win_pct=3.0, lose_pct=3.0):
    """Fill forward returns and grade every gradeable signal.

    loader(symbol) -> DataFrame with a close column (pass psx_report.load_from_psx
    wrapped in a lambda). Grading rule:
      BUY family : 'worked' if it gained >= win_pct by the horizon before
                   losing lose_pct; 'failed' otherwise.
      AVOID      : 'worked' if the stock did NOT gain win_pct (staying out was right)
      WAIT/HOLD  : 'survived' if it did not fall more than lose_pct
    """
    init()
    done = {"graded": 0, "skipped": 0, "errors": 0}
    rows = pending()
    cache = {}
    for r in rows:
        sym = r["symbol"]
        try:
            if sym not in cache:
                cache[sym] = loader(sym)
            df = cache[sym]
            after = df.loc[df.index > r["run_time"][:10]]
            if len(after) < horizon:
                done["skipped"] += 1
                continue
            p0 = float(r["price"] or 0)
            if p0 <= 0:
                done["skipped"] += 1
                continue
            fwd = {}
            for h in (1, 3, 7, 20):
                if len(after) >= h:
                    fwd[f"d{h}"] = round(
                        (float(after.close.iloc[h - 1]) / p0 - 1) * 100, 2)
            window = after.head(horizon)
            hi = (float(window.high.max()) / p0 - 1) * 100
            lo = (float(window.low.min()) / p0 - 1) * 100

            v = (r["verdict"] or "").upper()
            if v in ("BUY", "BUY ON TRIGGER"):
                outcome = "worked" if (hi >= win_pct and lo > -lose_pct) else "failed"
            elif v == "AVOID":
                outcome = "worked" if hi < win_pct else "failed"
            else:
                outcome = "survived" if lo > -lose_pct else "failed"

            sets = ", ".join(f"{k}=?" for k in fwd)
            args = list(fwd.values()) + [outcome,
                                         datetime.now().isoformat(timespec="seconds"),
                                         r["id"]]
            with closing(conn()) as c:
                c.execute(f"UPDATE signals SET {sets}{',' if fwd else ''} "
                          f"outcome=?, graded_at=? WHERE id=?", args)
                c.commit()
            done["graded"] += 1
        except Exception:
            done["errors"] += 1
    return done


# --------------------------------------------------------------------------
# 3. learn (confidence only)
# --------------------------------------------------------------------------

def accuracy(symbol=None):
    """Win/loss counts by verdict, strictly-graded only."""
    init()
    q = ("SELECT verdict, outcome, COUNT(*) n FROM signals "
         "WHERE outcome IS NOT NULL")
    a = []
    if symbol:
        q += " AND symbol=?"
        a.append(symbol)
    q += " GROUP BY verdict, outcome"
    with closing(conn()) as c:
        return [dict(r) for r in c.execute(q, a)]


def confidence_adjustment(symbol=None):
    """(points, note). Adjusts CONFIDENCE, never the model's weights."""
    rows = [r for r in accuracy(symbol) if (r["verdict"] or "").upper() in STRICT]
    wins = sum(r["n"] for r in rows if r["outcome"] == "worked")
    losses = sum(r["n"] for r in rows if r["outcome"] == "failed")
    total = wins + losses
    if total == 0:
        return 0.0, ("No strictly-graded history yet, so confidence is the "
                     "model's own number with nothing added.")
    wr = wins / total
    if total < 10:
        return round((wr - 0.5) * 8, 1), (
            f"Only {total} graded signal(s) — far too few to mean anything. "
            f"History is allowed to move confidence by at most 4 points here, "
            f"and you should treat the win rate as noise.")
    adj = float(max(-15, min(15, (wr - 0.5) * 30)))
    return round(adj, 1), (f"{wins}W/{losses}L across {total} strictly-graded "
                           f"BUY/AVOID signals, a {wr:.0%} win rate"
                           + (f" — confidence adjusted {adj:+.0f}."
                              if abs(adj) >= 1 else " — no material adjustment."))


def feature_scores(symbol=None):
    """Per-feature track record, for the tab that shows what is actually working."""
    init()
    q = ("SELECT feature, SUM(hits) hits, SUM(misses) misses FROM feature_hits")
    a = []
    if symbol:
        q += " WHERE symbol=?"
        a.append(symbol)
    q += " GROUP BY feature"
    out = []
    with closing(conn()) as c:
        for r in c.execute(q, a):
            tot = (r["hits"] or 0) + (r["misses"] or 0)
            out.append({"feature": r["feature"][2:], "hits": r["hits"] or 0,
                        "misses": r["misses"] or 0, "n": tot,
                        "win_rate": round((r["hits"] or 0) / tot * 100, 1) if tot else None,
                        "reliable": tot >= 20})
    return sorted(out, key=lambda x: -(x["n"] or 0))


def history(symbol=None, limit=300):
    init()
    q = "SELECT * FROM signals"
    a = []
    if symbol:
        q += " WHERE symbol=?"
        a.append(symbol)
    q += " ORDER BY run_time DESC LIMIT ?"
    a.append(limit)
    with closing(conn()) as c:
        return [dict(r) for r in c.execute(q, a)]


# --------------------------------------------------------------------------
# 4. positions ledger
# --------------------------------------------------------------------------

def open_position(symbol, qty, avg_cost, note=""):
    init()
    with closing(conn()) as c:
        cur = c.execute("INSERT INTO positions (symbol, qty, avg_cost, opened, note) "
                        "VALUES (?,?,?,?,?)",
                        (symbol.upper(), float(qty), float(avg_cost),
                         datetime.now().isoformat(timespec="seconds"), note))
        c.commit()
        return cur.lastrowid


def close_position(pos_id, exit_px):
    init()
    with closing(conn()) as c:
        c.execute("UPDATE positions SET closed=?, exit_px=? WHERE id=?",
                  (datetime.now().isoformat(timespec="seconds"),
                   float(exit_px), pos_id))
        c.commit()


def delete_position(pos_id):
    init()
    with closing(conn()) as c:
        c.execute("DELETE FROM positions WHERE id=?", (pos_id,))
        c.commit()


def holdings():
    init()
    with closing(conn()) as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM positions WHERE closed IS NULL ORDER BY symbol")]


def portfolio(price_lookup):
    """Mark the book to market. price_lookup(symbol) -> float or None."""
    rows = holdings()
    out, cost_t, val_t = [], 0.0, 0.0
    for h in rows:
        px = None
        try:
            px = price_lookup(h["symbol"])
        except Exception:
            px = None
        cost = h["qty"] * h["avg_cost"]
        val = h["qty"] * px if px else None
        pl = (val - cost) if val is not None else None
        out.append({**h, "price": px, "cost_pkr": round(cost),
                    "value_pkr": round(val) if val else None,
                    "pl_pkr": round(pl) if pl is not None else None,
                    "pl_pct": round(pl / cost * 100, 2) if pl is not None and cost else None})
        cost_t += cost
        if val:
            val_t += val
    total = {"cost_pkr": round(cost_t), "value_pkr": round(val_t),
             "pl_pkr": round(val_t - cost_t) if val_t else None,
             "pl_pct": round((val_t - cost_t) / cost_t * 100, 2) if cost_t and val_t else None,
             "positions": len(out),
             "unpriced": sum(1 for r in out if r["price"] is None)}
    return {"rows": out, "total": total}


def realised():
    init()
    with closing(conn()) as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM positions WHERE closed IS NOT NULL ORDER BY closed DESC")]
    for r in rows:
        r["pl_pkr"] = round((r["exit_px"] - r["avg_cost"]) * r["qty"])
        r["pl_pct"] = round((r["exit_px"] / r["avg_cost"] - 1) * 100, 2) \
            if r["avg_cost"] else None
    wins = [r for r in rows if (r["pl_pkr"] or 0) > 0]
    losses = [r for r in rows if (r["pl_pkr"] or 0) < 0]
    gross_w = sum(r["pl_pkr"] for r in wins) or 0
    gross_l = abs(sum(r["pl_pkr"] for r in losses)) or 0
    stats = {
        "closed": len(rows), "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / len(rows) * 100, 1) if rows else None,
        "net_pkr": round(gross_w - gross_l),
        "profit_factor": round(gross_w / gross_l, 2) if gross_l else None,
        "expectancy_pkr": round((gross_w - gross_l) / len(rows)) if rows else None,
        "avg_win_pkr": round(gross_w / len(wins)) if wins else None,
        "avg_loss_pkr": round(gross_l / len(losses)) if losses else None,
    }
    if rows and len(rows) < 20:
        stats["caveat"] = (f"{len(rows)} closed trades. Profit factor and "
                           f"expectancy need dozens of trades before they mean "
                           f"anything — read these as a record, not an edge.")
    return {"rows": rows, "stats": stats}
