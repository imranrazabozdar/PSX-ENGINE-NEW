"""database.py — SQLite storage. Every run, signal, and outcome is recorded
so the engine can learn from history. Nothing is ever fabricated; missing
outcomes stay NULL until real prices arrive."""

import sqlite3
import logging
from datetime import datetime, timedelta
from contextlib import contextmanager

import config

log = logging.getLogger("database")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_time TEXT NOT NULL,
    symbol TEXT NOT NULL,
    price REAL, volume REAL,
    technical_score REAL, sentiment_score REAL, macro_news_score REAL,
    final_score REAL, signal TEXT, confidence REAL,
    stop_loss REAL, target1 REAL, target2 REAL,
    support REAL, resistance REAL, risk_level TEXT,
    shariah_status TEXT, data_quality TEXT,
    main_reason TEXT, main_risk TEXT,
    price_next_run REAL, price_1d REAL, price_3d REAL, price_7d REAL,
    outcome TEXT,             -- 'worked' / 'failed' / NULL (pending)
    tech_flags TEXT,          -- JSON: which sub-indicators were bullish (learning loop)
    conviction_streak INTEGER, -- deprecated: was misleading (15-min run counts
                                -- mistaken for independent confirmations), no
                                -- longer written; old rows kept as-is
    confluence INTEGER,       -- 0-4: how many independent signal dimensions agree
    buy_zone_low REAL,        -- pullback buy-zone (band around the reference EMA)
    buy_zone_high REAL,
    accumulation_candidate INTEGER, -- 1 = OBV/CMF/volume signature of quiet buying
    accumulation_reasons TEXT,      -- JSON list of which signals fired
    cmf REAL,                       -- Chaikin Money Flow (needs real daily H/L)
    obv_divergence_bullish INTEGER  -- 1 = price flat/down while OBV rising
);
CREATE INDEX IF NOT EXISTS idx_runs_symbol_time ON runs(symbol, run_time);

CREATE TABLE IF NOT EXISTS prices (
    symbol TEXT, ts TEXT, price REAL, volume REAL, source TEXT,
    PRIMARY KEY (symbol, ts)
);

CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at TEXT, source TEXT, title TEXT UNIQUE, link TEXT,
    published TEXT, sentiment REAL, symbols TEXT
);

CREATE TABLE IF NOT EXISTS sentiment_history (
    run_time TEXT, symbol TEXT, score REAL, bullish INTEGER,
    bearish INTEGER, neutral INTEGER, mentions INTEGER, flags TEXT
);

CREATE TABLE IF NOT EXISTS indicator_accuracy (
    indicator TEXT, symbol TEXT, hits INTEGER DEFAULT 0,
    misses INTEGER DEFAULT 0, PRIMARY KEY (indicator, symbol)
);

-- Real daily OHLC banked from the intraday feed (PSX EOD has no High/Low).
-- Accumulates over time so true ATR/ADX/candles become possible later.
CREATE TABLE IF NOT EXISTS daily_ohlc (
    symbol TEXT, date TEXT, open REAL, high REAL, low REAL,
    close REAL, volume REAL, source TEXT,
    PRIMARY KEY (symbol, date)
);
"""


@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db():
    with conn() as c:
        c.executescript(SCHEMA)
        # Lightweight migrations: add newer columns to `runs` if they're missing
        # (CREATE TABLE IF NOT EXISTS won't add columns to a pre-existing table).
        existing = {r[1] for r in c.execute("PRAGMA table_info(runs)")}
        for col, decl in (("relative_strength", "REAL"), ("market_regime", "TEXT"),
                          ("tech_flags", "TEXT"), ("conviction_streak", "INTEGER"),
                          ("confluence", "INTEGER"),
                          ("buy_zone_low", "REAL"), ("buy_zone_high", "REAL"),
                          ("accumulation_candidate", "INTEGER"),
                          ("accumulation_reasons", "TEXT"),
                          ("cmf", "REAL"), ("obv_divergence_bullish", "INTEGER"),
                          ("early_watch", "INTEGER"), ("early_reason", "TEXT"),
                          ("outcome_7d", "TEXT")):
            if col not in existing:
                c.execute(f"ALTER TABLE runs ADD COLUMN {col} {decl}")
    log.info("Database initialised at %s", config.DB_PATH)


def save_run(row: dict) -> int:
    cols = ",".join(row.keys())
    ph = ",".join("?" * len(row))
    with conn() as c:
        cur = c.execute(f"INSERT INTO runs ({cols}) VALUES ({ph})",
                        list(row.values()))
        return cur.lastrowid


def save_price(symbol, ts, price, volume, source):
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO prices VALUES (?,?,?,?,?)",
                  (symbol, ts, price, volume, source))


def save_daily_ohlc(symbol, date, o, h, l, c, volume, source):
    """Bank one real daily OHLC bar (from intraday). REPLACE so re-runs on the
    same day refine the high/low as more ticks arrive."""
    with conn() as cx:
        cx.execute("""INSERT OR REPLACE INTO daily_ohlc
            (symbol, date, open, high, low, close, volume, source)
            VALUES (?,?,?,?,?,?,?,?)""",
                   (symbol, date, o, h, l, c, volume, source))


def daily_ohlc_count(symbol=None):
    q = "SELECT COUNT(*) n FROM daily_ohlc"
    args = []
    if symbol:
        q += " WHERE symbol=?"; args.append(symbol)
    with conn() as c:
        return c.execute(q, args).fetchone()["n"]


def get_daily_ohlc(symbol, limit=90):
    """Real banked daily OHLC bars for a symbol, oldest-first (list of dicts:
    date, open, high, low, close, volume). Used for true ATR/ADX once enough
    bars accumulate; empty/short -> caller falls back to the EOD-close proxies."""
    with conn() as c:
        rows = c.execute(
            """SELECT date, open, high, low, close, volume FROM daily_ohlc
               WHERE symbol=? ORDER BY date DESC LIMIT ?""",
            (symbol, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def momentum_bursts(symbols, min_pct=5.0):
    """Today's biggest 1-day price movers (e.g. upper/lower circuit hits),
    purely informational — NOT a trading signal and NOT fed back into
    scoring/signal_generator. The score-based pipeline deliberately ignores
    single-day spikes (see CLAUDE.md); this surfaces them anyway so a circuit
    move is never silently invisible on the dashboard. Read-only: uses the
    latest stored run price + locally banked daily_ohlc, no live fetch.
    Returns a list sorted by |pct_move| descending."""
    out = []
    for sym in symbols:
        r = last_run(sym)
        if not r or not r.get("price"):
            continue
        bars = get_daily_ohlc(sym, limit=22)
        if not bars:
            continue
        today = datetime.now().strftime("%Y-%m-%d")
        closed_bars = [b for b in bars if b["date"] != today]
        if not closed_bars:
            continue
        prev_close = closed_bars[-1]["close"]
        if not prev_close:
            continue
        pct_move = (r["price"] / prev_close - 1) * 100
        if abs(pct_move) < min_pct:
            continue
        vols = [b["volume"] for b in closed_bars[-20:] if b.get("volume")]
        avg_vol = sum(vols) / len(vols) if vols else None
        out.append({"symbol": sym, "price": r["price"], "prev_close": prev_close,
                    "pct_move": round(pct_move, 1), "today_vol": r.get("volume"),
                    "avg_vol": avg_vol, "signal": r.get("signal"),
                    "final_score": r.get("final_score")})
    return sorted(out, key=lambda x: abs(x["pct_move"]), reverse=True)


def save_news(items):
    with conn() as c:
        for it in items:
            c.execute("""INSERT OR IGNORE INTO news
                (fetched_at, source, title, link, published, sentiment, symbols)
                VALUES (?,?,?,?,?,?,?)""",
                (it["fetched_at"], it["source"], it["title"], it["link"],
                 it.get("published"), it.get("sentiment"),
                 ",".join(it.get("symbols", []))))


def recent_news(hours=48, symbol=None):
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    q = "SELECT * FROM news WHERE fetched_at > ?"
    args = [cutoff]
    if symbol:
        q += " AND symbols LIKE ?"
        args.append(f"%{symbol}%")
    with conn() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY fetched_at DESC", args)]


def save_sentiment(run_time, symbol, score, bullish, bearish, neutral,
                   mentions, flags):
    with conn() as c:
        c.execute("INSERT INTO sentiment_history VALUES (?,?,?,?,?,?,?,?)",
                  (run_time, symbol, score, bullish, bearish, neutral,
                   mentions, ",".join(flags)))


def previous_sentiment(symbol):
    with conn() as c:
        r = c.execute("""SELECT score FROM sentiment_history WHERE symbol=?
                         ORDER BY run_time DESC LIMIT 1""", (symbol,)).fetchone()
        return r["score"] if r else None


def last_run(symbol):
    with conn() as c:
        r = c.execute("""SELECT * FROM runs WHERE symbol=?
                         ORDER BY run_time DESC LIMIT 1""", (symbol,)).fetchone()
        return dict(r) if r else None


def run_history(symbol=None, limit=500):
    q = "SELECT * FROM runs"
    args = []
    if symbol:
        q += " WHERE symbol=?"
        args.append(symbol)
    q += " ORDER BY run_time DESC LIMIT ?"
    args.append(limit)
    with conn() as c:
        return [dict(r) for r in c.execute(q, args)]


def pending_outcomes():
    """Runs whose forward prices are not yet fully recorded."""
    with conn() as c:
        return [dict(r) for r in c.execute(
            """SELECT * FROM runs WHERE price_7d IS NULL
               ORDER BY run_time ASC""")]


def update_outcome(run_id, field, value):
    assert field in ("price_next_run", "price_1d", "price_3d", "price_7d",
                     "outcome")
    with conn() as c:
        c.execute(f"UPDATE runs SET {field}=? WHERE id=?", (value, run_id))


def bump_indicator(indicator, symbol, hit: bool):
    with conn() as c:
        c.execute("""INSERT INTO indicator_accuracy (indicator, symbol, hits, misses)
                     VALUES (?,?,?,?)
                     ON CONFLICT(indicator, symbol) DO UPDATE SET
                     hits = hits + ?, misses = misses + ?""",
                  (indicator, symbol, int(hit), int(not hit),
                   int(hit), int(not hit)))


def indicator_stats(symbol=None):
    q = "SELECT * FROM indicator_accuracy"
    args = []
    if symbol:
        q += " WHERE symbol=?"
        args.append(symbol)
    with conn() as c:
        return [dict(r) for r in c.execute(q, args)]


def signal_accuracy(symbol=None):
    """Win rate per signal type from completed runs."""
    q = """SELECT signal, outcome, COUNT(*) n FROM runs
           WHERE outcome IS NOT NULL"""
    args = []
    if symbol:
        q += " AND symbol=?"
        args.append(symbol)
    q += " GROUP BY signal, outcome"
    with conn() as c:
        return [dict(r) for r in c.execute(q, args)]


def signal_accuracy_summary(symbol=None, min_reliable_n=20):
    """Per-signal win rate with sample size + reliability flag.
    Returns one row per signal type with:
      * n_total / n_worked / n_failed
      * win_rate_pct
      * n_confidence: 'high' (n>=min_reliable_n), 'medium' (n>=min_reliable_n/2),
                      'low' (anything less) — small samples are noise, not edge.
    This is the honest cousin of signal_accuracy(): same data, but explicitly
    flagged when N is too small to trust. Use this for any UI that displays the
    win rate to a human about to risk money."""
    rows = signal_accuracy(symbol=symbol)
    agg = {}
    for r in rows:
        s = r["signal"] or "?"
        a = agg.setdefault(s, {"signal": s, "n_total": 0, "n_worked": 0,
                                "n_failed": 0})
        a["n_total"] += r["n"]
        if r["outcome"] == "worked":
            a["n_worked"] += r["n"]
        elif r["outcome"] == "failed":
            a["n_failed"] += r["n"]
    out = []
    for a in agg.values():
        graded = a["n_worked"] + a["n_failed"]
        a["win_rate_pct"] = (round(a["n_worked"] / graded * 100, 1)
                              if graded else None)
        if a["n_total"] >= min_reliable_n:
            a["n_confidence"] = "high"
        elif a["n_total"] >= max(5, min_reliable_n // 2):
            a["n_confidence"] = "medium"
        else:
            a["n_confidence"] = "low"
        out.append(a)
    return sorted(out, key=lambda r: -r["n_total"])


def gradeable_runs():
    """Every run that has enough forward data to grade (price + 3-day price),
    oldest-first so cohort_forward_move sees peers already filled."""
    with conn() as c:
        return [dict(r) for r in c.execute(
            """SELECT * FROM runs
               WHERE price IS NOT NULL AND price_3d IS NOT NULL AND price > 0
               ORDER BY run_time ASC""")]


def reset_indicator_accuracy():
    """Wipe the learned hit/miss tallies so they can be rebuilt from scratch
    (used by backtester.regrade_all after a grading-rule change)."""
    with conn() as c:
        c.execute("DELETE FROM indicator_accuracy")


def cohort_forward_move(date_str, exclude_symbol=None, days=3):
    """Median forward % change over `days` (3 or 7) across every stock scored on
    `date_str` (the calendar date of run_time) that already has that forward
    price filled. This is the engine's own universe acting as the 'market'
    benchmark — used to grade RELATIVELY (did it underperform the market?)
    instead of on absolute move, which is meaningless in a trending tape.
    Returns None when too few peers have completed to form a benchmark."""
    col = "price_7d" if days == 7 else "price_3d"
    with conn() as c:
        rows = c.execute(
            f"""SELECT symbol, price, {col} AS fwd FROM runs
                WHERE date(run_time)=? AND price IS NOT NULL
                  AND {col} IS NOT NULL AND price > 0""",
            (date_str,)).fetchall()
    moves = [(r["fwd"] / r["price"] - 1) * 100 for r in rows
             if exclude_symbol is None or r["symbol"] != exclude_symbol]
    if len(moves) < 5:          # need a real cross-section, not 1-2 names
        return None
    moves.sort()
    mid = len(moves) // 2
    return (moves[mid] if len(moves) % 2 else (moves[mid - 1] + moves[mid]) / 2)


def _runs_has_accum_column():
    """Whether the runs table has the accumulation_candidate column. Older or
    read-only deployments (where the ALTER TABLE migration could not run) may
    not — the accumulation views then degrade to empty instead of crashing."""
    with conn() as c:
        return "accumulation_candidate" in {
            r[1] for r in c.execute("PRAGMA table_info(runs)")}


def accumulation_streak(symbol, lookback=10):
    """How many of the most recent runs (consecutively, from now backward)
    were flagged accumulation_candidate=1. 0 if the latest run wasn't flagged."""
    if not _runs_has_accum_column():
        return 0
    with conn() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT accumulation_candidate FROM runs WHERE symbol=?
               ORDER BY run_time DESC LIMIT ?""", (symbol, lookback))]
    streak = 0
    for r in rows:
        if r["accumulation_candidate"]:
            streak += 1
        else:
            break
    return streak


def accumulating_now(lookback=10, min_streak=1):
    """Symbols currently flagged as accumulation candidates, each with how many
    consecutive recent runs (sessions) the flag has held — the 'last few
    sessions' view. Only counts symbols flagged in their MOST RECENT run."""
    out = []
    if not _runs_has_accum_column():
        return out
    with conn() as c:
        latest_ids = [r["symbol"] for r in c.execute(
            """SELECT symbol FROM runs r WHERE r.id =
               (SELECT MAX(id) FROM runs WHERE symbol = r.symbol)
               AND r.accumulation_candidate = 1""")]
    for sym in latest_ids:
        streak = accumulation_streak(sym, lookback)
        if streak >= min_streak:
            row = last_run(sym)
            out.append({"symbol": sym, "streak": streak,
                        "reasons": row.get("accumulation_reasons"),
                        "price": row.get("price"), "signal": row.get("signal"),
                        "final_score": row.get("final_score"),
                        "cmf": row.get("cmf")})
    return sorted(out, key=lambda x: x.get("final_score") or 0, reverse=True)


