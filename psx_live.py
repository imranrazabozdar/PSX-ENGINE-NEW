#!/usr/bin/env python3
"""
psx_live.py — market-wide snapshot, breadth, sectors and tape signals.

NEWLY WRITTEN MODULE. The original psx_live.py was not supplied with the v2.0
bundle. This is a reimplementation against the call surface the terminal
expects (market_snapshot / tape_signals / tape_summary / session_progress /
breadth_report / sector_map / yields).

IMPORTANT DIFFERENCE FROM THE ORIGINAL: the original called psxterminal.com,
an endpoint whose response shape is not documented anywhere in the surviving
code. Rather than guess at fields that would fail silently, this version is
built on the `psxdata` package — the PSX screener, symbols and sector tables,
which are known and parseable. Set PSXTERMINAL_URL if you want to point the
snapshot at that API instead; it is off by default.

Consequence you must know about: the PSX screener is an END-OF-DAY table with
an intraday refresh, NOT a real-time tick tape. "Live" here means "the latest
snapshot PSX publishes", which can be minutes to a full session behind. Every
function returns None when the feed is unreachable — a missing feed is
reported as missing, never filled in with the last known value dressed up as
current.
"""

import os
import time
from datetime import datetime, timedelta, timezone

PKT = timezone(timedelta(hours=5))
SESSION_START = 9 * 60 + 15
SESSION_END = 15 * 60 + 30

DEFAULT_TTL = int(os.environ.get("LIVE_TTL", "300"))
BIG_MOVE_PCT = 3.0

_CACHE = {}


def _cached(key, ttl, build):
    """Tiny TTL cache — PSX rate-limits and the UI polls every 60s."""
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = build()
    if val is not None:
        _CACHE[key] = (time.time(), val)
    return val


def _screener():
    """The full PSX screener as a DataFrame, or None if unreachable."""
    def build():
        try:
            import psxdata
            df = psxdata.PSXClient()._screener.fetch()
            return df if df is not None and not df.empty else None
        except Exception:
            return None
    return _cached("screener", DEFAULT_TTL, build)


def _num(row, key):
    try:
        v = float(row.get(key))
        return v if v == v else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# session clock
# --------------------------------------------------------------------------

def session_progress():
    """Fraction of the PSX session elapsed, 0.0-1.0. Pure clock arithmetic."""
    now = datetime.now(PKT)
    if now.weekday() > 4:
        return 0.0
    mins = now.hour * 60 + now.minute
    if mins <= SESSION_START:
        return 0.0
    if mins >= SESSION_END:
        return 1.0
    return (mins - SESSION_START) / (SESSION_END - SESSION_START)


# --------------------------------------------------------------------------
# snapshot and sectors
# --------------------------------------------------------------------------

def market_snapshot(ttl=DEFAULT_TTL):
    """{SYMBOL: {price, chg, volume, value_m}} for the whole exchange.

    Returns None when PSX cannot be reached, so callers fall back rather than
    rendering an empty market as a flat one.
    """
    def build():
        df = _screener()
        if df is None:
            return None
        out = {}
        for row in df.to_dict("records"):
            sym = str(row.get("symbol", "")).strip().upper()
            if not sym:
                continue
            price = _num(row, "price")
            vol = _num(row, "volume_avg_30d")
            out[sym] = {
                "price": round(price, 2) if price else None,
                "chg": (round(_num(row, "change_pct"), 2)
                        if _num(row, "change_pct") is not None else None),
                "volume": vol,
                "value_m": (round(price * vol / 1e6, 1)
                            if price and vol else None),
            }
        return out or None
    return _cached(f"snap_{ttl}", ttl, build)


def sector_map():
    """{SYMBOL: sector name} straight from the PSX symbols table."""
    def build():
        try:
            import psxdata
            df = psxdata.symbols()
            if df is None or df.empty:
                return None
            return {str(r["symbol"]).upper(): str(r.get("sector_name") or "Other")
                    for r in df.to_dict("records") if r.get("symbol")}
        except Exception:
            return None
    return _cached("sectors", 3600, build)


def yields(symbol):
    """Valuation fields for one symbol, in the API key names psx_context uses.

    Returns None (not an empty dict) when the symbol is absent, so callers can
    tell "no data" apart from "all zeros".
    """
    df = _screener()
    if df is None or "symbol" not in df.columns:
        return None
    m = df[df["symbol"].astype(str).str.upper() == symbol.upper()]
    if m.empty:
        return None
    row = m.iloc[0].to_dict()
    out = {"pe": _num(row, "pe_ratio"),
           "dividendYield": _num(row, "dividend_yield"),
           "marketCap": _num(row, "market_cap"),
           "freeFloat": _num(row, "free_float"),
           "price": _num(row, "price"),
           "volume30Avg": _num(row, "volume_avg_30d")}
    return out if any(v is not None for v in out.values()) else None


# --------------------------------------------------------------------------
# breadth
# --------------------------------------------------------------------------

def breadth_report():
    """Advance/decline across every PSX sector. Empty string when unreachable."""
    try:
        import psxdata
        df = psxdata.sectors()
    except Exception:
        return ""
    if df is None or df.empty:
        return ""

    rows = df.to_dict("records")
    adv = sum(int(r.get("advance") or 0) for r in rows)
    dec = sum(int(r.get("decline") or 0) for r in rows)
    unch = sum(int(r.get("unchanged") or 0) for r in rows)
    total = adv + dec + unch
    if total == 0:
        return ""

    L = [f"PSX BREADTH — {adv} advancing, {dec} declining, {unch} unchanged "
         f"({total} scrips).",
         f"  Advance/decline ratio {adv / max(dec, 1):.2f}."]

    if adv > dec * 1.5:
        L.append("  Broad buying across the exchange, not a handful of index "
                 "names carrying the tape.")
    elif dec > adv * 1.5:
        L.append("  Broad selling. In this condition individual breakouts fail "
                 "far more often than the chart suggests.")
    else:
        L.append("  Mixed tape — the index level is hiding two-way action "
                 "underneath it.")

    ranked = sorted(rows, key=lambda r: (int(r.get("advance") or 0)
                                         - int(r.get("decline") or 0)),
                    reverse=True)
    def line(r):
        return (f"{r.get('sector_name', '?')} "
                f"(+{int(r.get('advance') or 0)}/-{int(r.get('decline') or 0)})")
    if len(ranked) >= 2:
        L.append("  Strongest sectors: " + ", ".join(line(r) for r in ranked[:3]))
        L.append("  Weakest sectors: " + ", ".join(line(r) for r in ranked[-3:]))

    turnover = sum(float(r.get("turnover") or 0) for r in rows)
    if turnover:
        L.append(f"  Total turnover {turnover / 1e6:,.1f}M shares.")
    return "\n".join(L)


# --------------------------------------------------------------------------
# tape
# --------------------------------------------------------------------------

def tape_signals(symbols, ttl=60):
    """Notable moves in the watchlist. None when the feed is unreachable.

    This reads the PSX screener snapshot, so it flags what has ALREADY moved
    today. It is not an intraday tick feed and cannot see block prints or
    order-book pressure — the original module's `block` class is therefore
    never emitted rather than being faked from daily aggregates.
    """
    snap = market_snapshot(ttl=ttl)
    if snap is None:
        return None

    alerts = []
    for sym in symbols:
        d = snap.get(str(sym).upper())
        if not d or d.get("price") is None or d.get("chg") is None:
            continue
        chg, why = d["chg"], []
        if abs(chg) < BIG_MOVE_PCT:
            continue
        if chg > 0:
            kind, klass = "MOVING UP", "accum"
            why.append(f"up {chg:.2f}% on the session")
        else:
            kind, klass = "MOVING DOWN", "distrib"
            why.append(f"down {abs(chg):.2f}% on the session")
        if abs(chg) >= 6:
            why.append("a move this size usually has news behind it — find the "
                       "announcement before reacting to the chart")
        if d.get("value_m"):
            why.append(f"Rs {d['value_m']:,.1f}M average daily turnover "
                       f"(30-day, not today's)")
        alerts.append({"symbol": str(sym).upper(), "kind": kind, "class": klass,
                       "price": d["price"], "chg": chg,
                       "value_m": d.get("value_m") or "—",
                       "trades": "—", "why": why})

    alerts.sort(key=lambda a: abs(a["chg"]), reverse=True)
    return alerts


def tape_summary(alerts):
    """Plain-language wrap-up of the tape alerts."""
    pct = round(session_progress() * 100)
    if alerts is None:
        return "Live feed unreachable — nothing to summarise."
    if not alerts:
        return (f"No watchlist name has moved more than {BIG_MOVE_PCT:.0f}% "
                f"today ({pct}% of the session elapsed). A quiet tape is not a "
                f"signal in either direction.")
    up = [a for a in alerts if a["chg"] > 0]
    dn = [a for a in alerts if a["chg"] < 0]
    L = [f"{len(alerts)} watchlist name(s) moving more than "
         f"{BIG_MOVE_PCT:.0f}%, {pct}% of the session elapsed.", ""]
    if up:
        L.append("UP: " + ", ".join(f"{a['symbol']} {a['chg']:+.1f}%"
                                    for a in up[:8]))
    if dn:
        L.append("DOWN: " + ", ".join(f"{a['symbol']} {a['chg']:+.1f}%"
                                      for a in dn[:8]))
    L += ["", "These are moves that have already happened. Chasing the top of "
              "one is how a good watchlist turns into a bad book — check the "
              "level and the reason first."]
    return "\n".join(L)
