#!/usr/bin/env python3
"""
psx_report.py — data loading, resampling and the written report.

NEWLY WRITTEN MODULE. The original psx_report.py was not supplied with the
v2.0 bundle; this is a reimplementation against the call surface the rest of
the terminal expects (load_from_psx / to_weekly / report / market_breadth /
CACHE_DIR). Behaviour is equivalent in shape, not necessarily in detail.

Data comes from the `psxdata` package, which scrapes dps.psx.com.pk. Every
fetch is mirrored to a CSV in CACHE_DIR so the terminal still runs when PSX
is unreachable — that happens routinely from datacentre IPs, which PSX
blocks. A cache hit is reported as such; stale data is never presented as
live.
"""

import os
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

CACHE_DIR = os.environ.get("PSX_CACHE_DIR", ".psx_cache")
CACHE_MAX_AGE_HOURS = float(os.environ.get("PSX_CACHE_HOURS", "12"))

COLS = ["open", "high", "low", "close", "volume"]


def _cache_path(symbol):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{symbol.upper()}.csv")


def _read_cache(symbol):
    p = _cache_path(symbol)
    if not os.path.exists(p):
        return None
    try:
        df = pd.read_csv(p, parse_dates=["date"]).set_index("date")
        return df[COLS].astype(float) if not df.empty else None
    except Exception:
        return None


def _write_cache(symbol, df):
    try:
        df.reset_index().rename(columns={df.index.name or "index": "date"}) \
          .to_csv(_cache_path(symbol), index=False)
    except Exception:
        pass


def _normalise(raw):
    """psxdata returns a date column and lowercase OHLCV. Index it, sort it."""
    if raw is None or len(raw) == 0:
        return None
    df = raw.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
    else:
        df.index = pd.to_datetime(df.index)
    missing = [c for c in COLS if c not in df.columns]
    if missing:
        raise ValueError(f"PSX data is missing columns: {missing}")
    df = df[COLS].apply(pd.to_numeric, errors="coerce").dropna(subset=["close"])
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df if len(df) else None


def _drop_partial(df, partial):
    """The current session's bar is incomplete until the close.

    Acting on a half-formed candle is how you get a signal that evaporates by
    3:30pm, so `drop` is the default everywhere in the terminal.
    """
    if partial != "drop" or df is None or len(df) < 2:
        return df
    last = df.index[-1].date()
    now = datetime.now()
    if last == now.date() and now.hour < 16:
        return df.iloc[:-1]
    return df


def load_from_psx(symbol, years=3, partial="keep", cache=True):
    """Daily OHLCV for one symbol, newest last. Falls back to the CSV cache.

    Raises only when neither PSX nor the cache can produce usable bars —
    never returns a fabricated or partially-synthesised frame.
    """
    symbol = symbol.upper()
    start = (date.today() - timedelta(days=int(365.25 * years) + 10)).isoformat()

    fetched = err = None
    try:
        import psxdata
        fetched = _normalise(psxdata.stocks(symbol, start=start, cache=cache))
    except Exception as e:
        err = f"{type(e).__name__}: {e}"

    if fetched is not None:
        _write_cache(symbol, fetched)
        return _drop_partial(fetched, partial)

    cached = _read_cache(symbol)
    if cached is not None:
        return _drop_partial(cached, partial)

    raise RuntimeError(
        f"No data for {symbol}: PSX unreachable ({err or 'empty response'}) and "
        f"nothing cached in {CACHE_DIR}. Check /diag — PSX blocks datacentre IPs.")


def cache_age_hours(symbol):
    """How old the cached copy is, or None if there is no cache."""
    p = _cache_path(symbol)
    if not os.path.exists(p):
        return None
    return (datetime.now().timestamp() - os.path.getmtime(p)) / 3600


def to_weekly(daily):
    """Resample daily bars to weeks ending Friday, dropping any partial week."""
    if daily is None or len(daily) == 0:
        return daily
    w = daily.resample("W-FRI").agg({"open": "first", "high": "max", "low": "min",
                                     "close": "last", "volume": "sum"}).dropna()
    return w


def to_monthly(daily):
    if daily is None or len(daily) == 0:
        return daily
    return daily.resample("ME").agg({"open": "first", "high": "max", "low": "min",
                                     "close": "last", "volume": "sum"}).dropna()


# --------------------------------------------------------------------------
# the written report
# --------------------------------------------------------------------------

def _pct(a, b):
    try:
        return (float(a) / float(b) - 1) * 100
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _candle_note(df):
    """Name the last bar only when it is unambiguous. Silence beats a guess."""
    if len(df) < 2:
        return None
    o, h, l, c = (float(df.open.iloc[-1]), float(df.high.iloc[-1]),
                  float(df.low.iloc[-1]), float(df.close.iloc[-1]))
    pc = float(df.close.iloc[-2])
    po = float(df.open.iloc[-2])
    rng = h - l
    if rng <= 0:
        return None
    body = abs(c - o)
    upper, lower = h - max(c, o), min(c, o) - l

    if body / rng < 0.1:
        return ("The last bar is a doji — buyers and sellers finished level. "
                "After a run it marks hesitation; on its own it means nothing.")
    if lower > body * 2 and c > o:
        return ("The last bar has a long lower wick: price was pushed down and "
                "bought back before the close. That is demand showing up, but "
                "one bar is an anecdote.")
    if upper > body * 2 and c < o:
        return ("The last bar has a long upper wick: a rally that was sold into. "
                "Supply is present at this level.")
    if c > po and o < pc and c > o and pc < po:
        return ("The last bar engulfs the previous one to the upside — a change "
                "of hands, worth more when it comes on above-average volume.")
    if c < po and o > pc and c < o and pc > po:
        return "The last bar engulfs the previous one to the downside."
    return None


def report(symbol, daily, volume=True, candles=True, monthly=False,
           bench=None, structure=True):
    """A plain-language read of the chart. Returns text, not a verdict.

    Deliberately descriptive: it says what price and volume have DONE. The
    verdict lives in psx_brain, the structure in psx_wyckoff.
    """
    if daily is None or len(daily) < 30:
        return f"{symbol}: not enough history to describe (need 30+ sessions)."

    c = daily.close.astype(float)
    px = float(c.iloc[-1])
    L = [f"{symbol} — {px:,.2f} as of {daily.index[-1]:%d %b %Y}.", ""]

    # --- trend ------------------------------------------------------------
    e20 = c.ewm(span=20, adjust=False).mean()
    e50 = c.ewm(span=50, adjust=False).mean()
    e200 = c.ewm(span=200, adjust=False).mean() if len(c) >= 200 else None
    above = [f"{s}-EMA" for s, e in (("20", e20), ("50", e50), ("200", e200))
             if e is not None and px > float(e.iloc[-1])]
    L.append("TREND: price is above the " + ", ".join(above) + "."
             if above else
             "TREND: price is below every moving average on the chart — this is a "
             "downtrend, whatever else looks appealing.")
    if e200 is not None:
        d200 = _pct(px, e200.iloc[-1])
        L.append(f"  It sits {d200:+.1f}% against its 200-EMA, the line that "
                 f"separates a correction from a bear market for most names.")

    for label, w in (("1 week", 5), ("1 month", 21), ("3 months", 63),
                     ("1 year", 252)):
        if len(c) > w:
            L.append(f"  {label}: {_pct(px, c.iloc[-1 - w]):+.1f}%")

    # --- range ------------------------------------------------------------
    hi52 = float(c.tail(252).max())
    lo52 = float(c.tail(252).min())
    pos = (px - lo52) / (hi52 - lo52) * 100 if hi52 > lo52 else None
    L += ["", f"RANGE: the 52-week band is {lo52:,.2f} to {hi52:,.2f}."]
    if pos is not None:
        L.append(f"  Price is {pos:.0f}% of the way up it "
                 f"({_pct(px, hi52):+.1f}% from the high).")

    # --- volume -----------------------------------------------------------
    if volume and "volume" in daily:
        v = daily.volume.astype(float)
        v20 = float(v.tail(20).mean())
        if v20 > 0:
            L += ["", f"VOLUME: last session traded {float(v.iloc[-1]):,.0f} "
                      f"against a 20-day average of {v20:,.0f} "
                      f"({float(v.iloc[-1]) / v20:.1f}x)."]
        val20 = float((c * v).tail(20).mean())
        L.append(f"  About Rs {val20 / 1e6:,.1f}M changes hands per session. "
                 f"That, not your conviction, sets the size you can actually get "
                 f"in and out of.")
        obv = (np.sign(c.diff().fillna(0)) * v).cumsum()
        if len(obv) > 21:
            L.append("  On-balance volume is "
                     + ("rising — accumulation is keeping pace with price."
                        if float(obv.iloc[-1]) > float(obv.iloc[-21])
                        else "falling while you consider buying. Distribution."))

    # --- structure --------------------------------------------------------
    if structure:
        win = daily.tail(120)
        sup = float(win.low.min())
        res = float(win.high.max())
        L += ["", f"LEVELS (120 sessions): support {sup:,.2f}, resistance "
                  f"{res:,.2f}."]
        L.append(f"  Downside to support {_pct(sup, px):+.1f}%, upside to "
                 f"resistance {_pct(res, px):+.1f}%.")

    # --- candles ----------------------------------------------------------
    if candles:
        note = _candle_note(daily)
        if note:
            L += ["", "LAST BAR: " + note]

    # --- monthly ----------------------------------------------------------
    if monthly:
        m = to_monthly(daily)
        if m is not None and len(m) >= 6:
            up = int((m.close.diff().tail(6) > 0).sum())
            L += ["", f"MONTHLY: {up} of the last 6 months closed higher."]

    # --- vs benchmark -----------------------------------------------------
    if bench is not None and len(bench) > 63:
        b = pd.Series(bench).astype(float)
        s_ = _pct(px, c.iloc[-64])
        b_ = _pct(float(b.iloc[-1]), float(b.iloc[-64]))
        if s_ is not None and b_ is not None:
            L += ["", f"VS INDEX (3m): stock {s_:+.1f}% against the index "
                      f"{b_:+.1f}% — {'out' if s_ > b_ else 'under'}performing by "
                      f"{abs(s_ - b_):.1f} points."]

    L += ["", "This section describes what has happened. It is not a "
              "recommendation — confirm every level yourself before acting."]
    return "\n".join(L)


def market_breadth(rows):
    """Breadth across the terminal's own scan rows (not the whole exchange)."""
    if not rows:
        return ("No scan has been run yet, so there is no breadth to report from "
                "your own universe.")
    n = len(rows)
    up = sum(1 for r in rows if (r.get("dTrend") or "").upper() == "UP")
    cloud = sum(1 for r in rows if (r.get("cloud") or "").lower() == "above")
    pos = sum(1 for r in rows if (r.get("1m%") or 0) > 0)
    strong = sum(1 for r in rows if (r.get("SCORE") or 0) >= 10)

    L = [f"BREADTH OF YOUR SCAN ({n} stocks ranked):",
         f"  {up}/{n} ({up / n * 100:.0f}%) in a daily uptrend",
         f"  {cloud}/{n} ({cloud / n * 100:.0f}%) trading above the daily cloud",
         f"  {pos}/{n} ({pos / n * 100:.0f}%) up over the last month",
         f"  {strong}/{n} scoring 10 or better"]

    frac = up / n
    if frac >= 0.6:
        L.append("Broad participation — in this tape a good chart is more likely "
                 "to be carried by the market rather than fighting it.")
    elif frac >= 0.35:
        L.append("Mixed participation. Selection matters more than direction "
                 "here; demand the trigger rather than anticipating it.")
    else:
        L.append("Narrow participation — most names are in downtrends. Breakouts "
                 "fail disproportionately in this condition regardless of how "
                 "clean the individual chart looks. Size down.")
    L.append("Breadth is measured across the stocks YOU scanned, which is a "
             "biased sample of the exchange. Read it as context, not as the "
             "market's own breadth.")
    return "\n".join(L)
