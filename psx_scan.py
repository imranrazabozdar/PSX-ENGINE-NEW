#!/usr/bin/env python3
"""
psx_scan.py — universe listing and the one-row-per-symbol screen.

NEWLY WRITTEN MODULE. The original was not supplied with the v2.0 bundle.
Reimplemented against the two functions the terminal calls: get_universe(kind)
and evaluate(symbol, years).

`evaluate` runs inside a thread pool over hundreds of symbols, so it never
raises — a symbol that cannot be fetched or has too little history returns
None and is dropped from the scan. It does NOT pass a benchmark to
psx_brain, so scan rows carry no relative-strength component; the full read
in the DEEP READ tab does.
"""

import psx_brain
import psx_report

UNIVERSES = {
    "KSE100": "KSE100",
    "KSE30": "KSE30",
    "KMI30": "KMI30",
    "ALLSHR": "ALLSHR",
}


def get_universe(kind="KSE100"):
    """Ticker list for an index name, or every listed symbol for 'all'.

    Raises when PSX is unreachable — the caller shows the error rather than
    silently scanning a stale or partial universe.
    """
    import psxdata

    key = str(kind or "KSE100").strip()
    if key.lower() in ("all", "*", "market", "whole"):
        df = psxdata.symbols()
        if df is None or df.empty:
            raise RuntimeError("PSX returned no symbol list.")
        keep = df
        for flag in ("is_etf", "is_debt", "is_gem"):
            if flag in keep.columns:
                keep = keep[~keep[flag].astype(bool)]
        return [str(s).upper() for s in keep["symbol"].tolist() if str(s).strip()]

    index = UNIVERSES.get(key.upper(), key.upper())
    syms = psxdata.tickers(index)
    if not syms:
        raise RuntimeError(f"PSX returned no constituents for {index}.")
    return [str(s).upper() for s in syms]


def evaluate(symbol, years=2):
    """One scan row, or None if the symbol cannot be judged.

    Deliberately swallows every exception: a scan over the whole market will
    always contain suspended, newly-listed and illiquid scrips, and one bad
    symbol must not take the run down.
    """
    try:
        df = psx_report.load_from_psx(symbol, years)
        res = psx_brain.analyse(symbol, df, None, "drop")
    except Exception:
        return None

    try:
        c = df.close.astype(float)
        m1 = round((float(c.iloc[-1]) / float(c.iloc[-22]) - 1) * 100, 1) \
            if len(c) > 22 else 0.0
        avg_vol_m = round(float(df.volume.tail(20).mean()) / 1e6, 3)
    except Exception:
        return None

    st = res["state"]
    return {
        "sym": res["symbol"],
        "price": res["price"],
        "1m%": m1,
        "wVol": st["wVol"],
        "dVol": st["dVol"],
        "dTrend": st["dTrend"],
        "cloud": st["cloud"],
        "SCORE": res["score"],
        "avgVolM": avg_vol_m,
        "verdict": res["verdict"],
        "confidence": res["confidence"],
    }
