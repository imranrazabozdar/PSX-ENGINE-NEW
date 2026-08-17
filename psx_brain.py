#!/usr/bin/env python3
"""
psx_brain.py — the indicator engine and the written verdict.

NEWLY WRITTEN MODULE — READ THIS BEFORE TRUSTING A NUMBER IT PRODUCES.

The original psx_brain.py was not supplied with the v2.0 bundle. Only its
`compare()` function survived, and that one function is reproduced below as
it was written. Everything else here — `analyse()`, every indicator weight,
every verdict threshold — is NEW logic written to satisfy the call surface
the rest of the terminal expects. It is NOT a restoration of the tuned
engine that produced the original results.

What that means in practice:

  * The individual indicators are standard, publicly documented formulas
    (Wilder's RSI and ADX, MACD, Ichimoku, Supertrend, Keltner, Donchian,
    CMF, MFI, Force Index, VPT, OBV, ordinary-least-squares trend fit). Those
    are arithmetic and are correct as implemented.
  * The WEIGHTS that combine them into `score`, and the CUTOFFS that turn
    `score` into BUY / BUY ON TRIGGER / WAIT / AVOID, are judgement calls with
    no backtest behind them. They are declared in SCORE_WEIGHTS and CUTOFFS at
    the top of the file so they can be seen and changed, rather than buried.
  * Backtest these against graded history before sizing real positions on
    them. An untested gate layer is exactly what inverts an edge: a good
    score band can be made worthless by a bad threshold sitting on top of it.

Verdicts are decision support. Confirm every level manually before ordering.
"""

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------
# UNVALIDATED KNOBS — no backtest behind any of these numbers
# --------------------------------------------------------------------------

SCORE_WEIGHTS = {
    "trend": 4.0,        # EMA 20/50/200 stack
    "ichimoku": 2.5,     # position against the cloud
    "supertrend": 2.0,   # ATR trend flip
    "macd": 2.5,         # histogram + signal cross
    "rsi": 1.5,          # momentum regime, not overbought/oversold
    "adx": 1.5,          # is the trend real or is it noise
    "flow_daily": 3.0,   # dVol / 6
    "flow_weekly": 2.0,  # wVol / 6
    "rel_strength": 2.0, # excess return vs the index
    "regression": 1.5,   # slope quality of the 60-day fit
}                        # theoretical span: -22.5 .. +22.5

CUTOFFS = {
    "buy": 9.0,          # score at or above this AND above the trigger
    "trigger": 6.0,      # score at or above this but not yet through the level
    "avoid": -6.0,       # score at or below this
}

ATR_STOP_MULT = 2.5      # widest stop allowed, in ATRs
SWING_LOOKBACK = 20      # bars used for the structural stop and the trigger
MIN_BARS = 60            # below this there is not enough history to judge


# --------------------------------------------------------------------------
# indicators
# --------------------------------------------------------------------------

def _ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def _rma(s, n):
    """Wilder's smoothing — what RSI and ADX are actually defined with."""
    return s.ewm(alpha=1.0 / n, adjust=False).mean()


def _true_range(df):
    pc = df.close.shift()
    return pd.concat([df.high - df.low,
                      (df.high - pc).abs(),
                      (df.low - pc).abs()], axis=1).max(axis=1)


def _atr(df, n=14):
    return _rma(_true_range(df), n)


def _rsi(c, n=14):
    d = c.diff()
    gain = _rma(d.clip(lower=0), n)
    loss = _rma((-d).clip(lower=0), n)
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def _macd(c, fast=12, slow=26, sig=9):
    line = _ema(c, fast) - _ema(c, slow)
    signal = _ema(line, sig)
    return line, signal, line - signal


def _adx(df, n=14):
    """Wilder's ADX with the real +DI/-DI construction, not a range proxy."""
    up = df.high.diff()
    dn = -df.low.diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr = _rma(_true_range(df), n)
    pdi = 100 * _rma(pd.Series(plus, index=df.index), n) / atr.replace(0, np.nan)
    mdi = 100 * _rma(pd.Series(minus, index=df.index), n) / atr.replace(0, np.nan)
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return _rma(dx.fillna(0), n), pdi, mdi


def _ichimoku(df, t=9, k=26, b=52):
    conv = (df.high.rolling(t).max() + df.low.rolling(t).min()) / 2
    base = (df.high.rolling(k).max() + df.low.rolling(k).min()) / 2
    a = ((conv + base) / 2).shift(k)
    bb = ((df.high.rolling(b).max() + df.low.rolling(b).min()) / 2).shift(k)
    return conv, base, a, bb


def _supertrend(df, n=10, mult=3.0):
    """Returns +1 while the ATR band trails below price, -1 once it flips."""
    atr = _atr(df, n)
    hl2 = (df.high + df.low) / 2
    upper, lower = hl2 + mult * atr, hl2 - mult * atr
    direction = pd.Series(1, index=df.index)
    for i in range(1, len(df)):
        c = float(df.close.iloc[i])
        if c > float(upper.iloc[i - 1]):
            direction.iloc[i] = 1
        elif c < float(lower.iloc[i - 1]):
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i - 1]
    return direction


def _cmf(df, n=20):
    rng = (df.high - df.low).replace(0, np.nan)
    mfm = ((df.close - df.low) - (df.high - df.close)) / rng
    mfv = (mfm * df.volume).fillna(0)
    return mfv.rolling(n).sum() / df.volume.rolling(n).sum().replace(0, np.nan)


def _mfi(df, n=14):
    tp = (df.high + df.low + df.close) / 3
    raw = tp * df.volume
    up = raw.where(tp > tp.shift(), 0.0).rolling(n).sum()
    dn = raw.where(tp < tp.shift(), 0.0).rolling(n).sum()
    return (100 - 100 / (1 + up / dn.replace(0, np.nan))).fillna(50)


def _force(df, n=13):
    return _ema(df.close.diff() * df.volume, n)


def _vpt(df):
    return (df.volume * df.close.pct_change().fillna(0)).cumsum()


def _obv(df):
    return (np.sign(df.close.diff().fillna(0)) * df.volume).cumsum()


def _regression(c, n=60):
    """Slope as % per day, plus R² — a trend you can fit is worth more."""
    y = c.tail(n).to_numpy(dtype=float)
    if len(y) < 20:
        return 0.0, 0.0
    x = np.arange(len(y), dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    fit = slope * x + intercept
    ss_res = float(((y - fit) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return float(slope / y.mean() * 100), float(max(0.0, r2))


def _flow_score(df):
    """Six independent volume reads, each +1 or -1. Range -6..+6.

    Six dimensions rather than one because any single volume measure is easy
    to fool; agreement across differently-constructed ones is the point.
    """
    if len(df) < 30:
        return 0
    s = 0
    obv = _obv(df)
    s += 1 if float(obv.iloc[-1]) > float(obv.iloc[-21]) else -1
    cmf = _cmf(df)
    s += 1 if float(cmf.iloc[-1] or 0) > 0 else -1
    s += 1 if float(_mfi(df).iloc[-1]) > 50 else -1
    s += 1 if float(_force(df).iloc[-1]) > 0 else -1
    vpt = _vpt(df)
    s += 1 if float(vpt.iloc[-1]) > float(vpt.iloc[-21]) else -1
    up = float(df.volume.where(df.close > df.open, 0).tail(20).sum())
    dn = float(df.volume.where(df.close < df.open, 0).tail(20).sum())
    s += 1 if up > dn else -1
    return int(s)


def _drop_partial(df, partial):
    from datetime import datetime
    if partial != "drop" or df is None or len(df) < 2:
        return df
    if df.index[-1].date() == datetime.now().date() and datetime.now().hour < 16:
        return df.iloc[:-1]
    return df


# --------------------------------------------------------------------------
# the read
# --------------------------------------------------------------------------

def analyse(symbol, daily, bench=None, partial="drop"):
    """Full indicator read for one symbol.

    daily: OHLCV DataFrame, oldest first (psx_report.load_from_psx).
    bench: index close series for relative strength. None simply omits it.
    """
    symbol = symbol.upper()
    df = _drop_partial(daily, partial)
    if df is None or len(df) < MIN_BARS:
        raise ValueError(f"{symbol}: need {MIN_BARS}+ sessions, got "
                         f"{0 if df is None else len(df)}")

    df = df.astype(float)
    c, price = df.close, float(df.close.iloc[-1])
    weekly = df.resample("W-FRI").agg({"open": "first", "high": "max",
                                       "low": "min", "close": "last",
                                       "volume": "sum"}).dropna()

    e20, e50 = _ema(c, 20), _ema(c, 50)
    e200 = _ema(c, 200) if len(c) >= 200 else None
    rsi = float(_rsi(c).iloc[-1])
    _, _, hist = _macd(c)
    macd_h = float(hist.iloc[-1])
    adx_s, pdi, mdi = _adx(df)
    adx = float(adx_s.iloc[-1])
    conv, base, sa, sb = _ichimoku(df)
    st_dir = int(_supertrend(df).iloc[-1])
    atr = float(_atr(df).iloc[-1])
    slope, r2 = _regression(c)
    volx = float(df.volume.iloc[-1] / df.volume.tail(20).mean()) \
        if float(df.volume.tail(20).mean()) > 0 else 0.0

    dVol = _flow_score(df)
    wVol = _flow_score(weekly) if len(weekly) >= 30 else 0

    # cloud position
    ca, cb = float(sa.iloc[-1] or 0), float(sb.iloc[-1] or 0)
    top, bot = max(ca, cb), min(ca, cb)
    cloud = "above" if price > top else "below" if price < bot else "inside"

    dTrend = "UP" if price > float(e20.iloc[-1]) > float(e50.iloc[-1]) else "DOWN"
    wTrend = "UP" if len(weekly) >= 20 and float(weekly.close.iloc[-1]) > \
        float(_ema(weekly.close, 10).iloc[-1]) else "DOWN"

    # ---- relative strength ----------------------------------------------
    rs_txt, rs_excess = None, 0.0
    if bench is not None:
        b = pd.Series(bench).astype(float).dropna()
        if len(b) > 63 and len(c) > 63:
            s_ = (price / float(c.iloc[-64]) - 1) * 100
            b_ = (float(b.iloc[-1]) / float(b.iloc[-64]) - 1) * 100
            rs_excess = s_ - b_
            rs_txt = (f"Over three months the stock is {s_:+.1f}% against the "
                      f"index at {b_:+.1f}% — "
                      f"{'leading by' if rs_excess > 0 else 'lagging by'} "
                      f"{abs(rs_excess):.1f} points.")

    # ---- score -----------------------------------------------------------
    W = SCORE_WEIGHTS
    parts = {}

    stack = 0.0
    stack += 0.5 if price > float(e20.iloc[-1]) else -0.5
    stack += 0.3 if float(e20.iloc[-1]) > float(e50.iloc[-1]) else -0.3
    if e200 is not None:
        stack += 0.2 if price > float(e200.iloc[-1]) else -0.2
    parts["trend"] = stack * W["trend"]
    parts["ichimoku"] = W["ichimoku"] * (1 if cloud == "above" else
                                         -1 if cloud == "below" else 0)
    parts["supertrend"] = W["supertrend"] * st_dir
    parts["macd"] = W["macd"] * float(np.clip(macd_h / (atr or 1) * 3, -1, 1))
    parts["rsi"] = W["rsi"] * float(np.clip((rsi - 50) / 20, -1, 1))
    trend_sign = 1 if float(pdi.iloc[-1]) > float(mdi.iloc[-1]) else -1
    parts["adx"] = W["adx"] * trend_sign * float(np.clip((adx - 20) / 20, 0, 1))
    parts["flow_daily"] = W["flow_daily"] * dVol / 6
    parts["flow_weekly"] = W["flow_weekly"] * wVol / 6
    parts["rel_strength"] = W["rel_strength"] * float(np.clip(rs_excess / 15, -1, 1))
    parts["regression"] = W["regression"] * float(np.clip(slope * 5, -1, 1)) * r2

    score = round(sum(parts.values()), 1)

    # ---- levels ----------------------------------------------------------
    swing_low = float(df.low.tail(SWING_LOOKBACK).min())
    swing_high = float(df.high.tail(SWING_LOOKBACK).max())
    stop = max(swing_low - 0.25 * atr, price - ATR_STOP_MULT * atr)
    stop = min(stop, price * 0.985)
    if stop <= 0:
        stop = price * 0.9
    risk = price - stop
    trigger = swing_high if swing_high > price else round(price + 0.25 * atr, 2)
    levels = {
        "trigger": round(trigger, 2),
        "stop": round(stop, 2),
        "t1": round(price + 2 * risk, 2),
        "t2": round(price + 3 * risk, 2),
        "t3": round(price + 5 * risk, 2),
        "risk_pct": round(risk / price * 100, 2),
        "rr": round(3.0, 2),
        "support": round(swing_low, 2),
        "resistance": round(swing_high, 2),
    }

    # ---- verdict ---------------------------------------------------------
    if score <= CUTOFFS["avoid"]:
        verdict, klass = "AVOID", "avoid"
    elif score >= CUTOFFS["buy"] and price >= trigger:
        verdict, klass = "BUY", "buy"
    elif score >= CUTOFFS["trigger"]:
        verdict, klass = "BUY ON TRIGGER", "trigger"
    else:
        verdict, klass = "WAIT", "wait"

    # ---- reasons ---------------------------------------------------------
    bull, bear, flags = [], [], []

    if dTrend == "UP":
        bull.append(f"Price {price:,.2f} is above a rising 20-EMA "
                    f"({float(e20.iloc[-1]):,.2f}) which is itself above the "
                    f"50-EMA — the moving averages are stacked the right way up.")
    else:
        bear.append(f"The moving averages are not stacked bullishly: price "
                    f"{price:,.2f} against a 20-EMA of {float(e20.iloc[-1]):,.2f}.")
    if e200 is not None:
        (bull if price > float(e200.iloc[-1]) else bear).append(
            f"It is {(price / float(e200.iloc[-1]) - 1) * 100:+.1f}% against its "
            f"200-EMA.")
    if cloud == "above":
        bull.append("Price trades above the Ichimoku cloud, which is the "
                    "method's definition of a bull regime.")
    elif cloud == "below":
        bear.append("Price is beneath the Ichimoku cloud — the cloud is now "
                    "overhead resistance, not support.")
    else:
        flags.append("Price is INSIDE the Ichimoku cloud. That is the method's "
                     "explicit no-trade zone: direction is undefined until it "
                     "leaves one side or the other.")
    (bull if st_dir > 0 else bear).append(
        "Supertrend is " + ("long — the ATR band is trailing below price."
                            if st_dir > 0 else
                            "short — the ATR band flipped overhead."))
    if macd_h > 0:
        bull.append("The MACD histogram is positive: the shorter average is "
                    "pulling away from the longer one.")
    else:
        bear.append("The MACD histogram is negative — momentum is still "
                    "against the trade.")
    if adx >= 25:
        (bull if trend_sign > 0 else bear).append(
            f"ADX at {adx:.0f} says this is a real trend rather than chop, and "
            f"{'+DI is on top' if trend_sign > 0 else '-DI is on top'}.")
    else:
        flags.append(f"ADX is only {adx:.0f}. Below 25 there is no trend to "
                     f"follow — breakout signals fail most often in exactly this "
                     f"condition.")
    if rsi > 70:
        flags.append(f"RSI {rsi:.0f} is extended. That is not a sell signal in a "
                     f"strong trend, but it is a bad place to start a position.")
    elif rsi < 30:
        flags.append(f"RSI {rsi:.0f} is washed out. Cheap can stay cheap — wait "
                     f"for the turn rather than catching it.")

    if dVol >= 3:
        bull.append(f"Daily money flow is {dVol:+d}/6 — the volume measures "
                    f"broadly agree that buyers are in control.")
    elif dVol <= -3:
        bear.append(f"Daily money flow is {dVol:+d}/6. Volume is leaving.")
    if wVol >= 2 and dVol <= -2:
        flags.append("TRAP PATTERN: the weekly flow is still accumulating while "
                     "the daily flow has turned distributive. The weekly picture "
                     "is the one that lags here — wait for the daily to turn back "
                     "up rather than buying the weekly.")
    if volx > 2:
        flags.append(f"Last session traded {volx:.1f}x its 20-day average "
                     f"volume. Confirm what the news was before assuming this is "
                     f"accumulation.")
    if rs_txt:
        (bull if rs_excess > 0 else bear).append(rs_txt)
    if r2 > 0.7:
        (bull if slope > 0 else bear).append(
            f"The 60-day trend fits a straight line closely (R² {r2:.2f}) at "
            f"{slope:+.2f}% per day — an orderly move, not a spike.")

    if levels["risk_pct"] > 8:
        flags.append(f"The structural stop sits {levels['risk_pct']:.1f}% away. "
                     f"That is a wide stop; size down accordingly or wait for a "
                     f"tighter entry near {levels['support']:,.2f}.")

    # ---- confidence ------------------------------------------------------
    agree = sum(1 for v in (stack, 1 if cloud == "above" else -1 if cloud == "below" else 0,
                            st_dir, macd_h, dVol, wVol) if v > 0)
    disagree = sum(1 for v in (stack, 1 if cloud == "above" else -1 if cloud == "below" else 0,
                               st_dir, macd_h, dVol, wVol) if v < 0)
    conf = 50 + (agree - disagree) * 6
    conf += 8 if adx >= 25 else -8
    conf += 5 if len(df) >= 250 else -5
    if cloud == "inside":
        conf -= 8
    confidence = int(np.clip(conf, 5, 95))

    size_pct = round(float(np.clip(1.5 / max(levels["risk_pct"], 0.1) * 100,
                                   0, 15)), 1)

    summary = (
        f"{symbol} at {price:,.2f} — {verdict} (score {score:+.1f}, confidence "
        f"{confidence}/100). Daily trend {dTrend}, weekly {wTrend}, "
        f"{cloud} the cloud, flow {dVol:+d}/6 daily and {wVol:+d}/6 weekly. "
        f"Trigger {levels['trigger']:,.2f}, stop {levels['stop']:,.2f} "
        f"({levels['risk_pct']:.1f}% risk), first target {levels['t1']:,.2f}. "
        + ("Already through the trigger. " if price >= trigger else
           f"Needs a close above {levels['trigger']:,.2f} to act. ")
        + "These thresholds are unvalidated — confirm manually before ordering.")

    return {
        "symbol": symbol, "price": round(price, 2), "score": score,
        "verdict": verdict, "class": klass, "confidence": confidence,
        "size_pct": size_pct,
        "state": {"wVol": wVol, "dVol": dVol, "dTrend": dTrend, "wTrend": wTrend,
                  "cloud": cloud, "rsi": round(rsi, 1), "adx": round(adx, 1),
                  "volx": round(volx, 2)},
        "levels": levels,
        "bull": bull, "bear": bear, "flags": flags,
        "rs": rs_txt, "summary": summary,
        "components": {k: round(v, 2) for k, v in parts.items()},
        "bars": len(df), "asof": df.index[-1].strftime("%Y-%m-%d"),
    }


def compare(results):
    """Rank several analysed stocks and explain the ordering."""
    if not results:
        return {"ranked": [], "commentary": "No stocks to compare."}
    r = sorted(results, key=lambda x: x["score"], reverse=True)
    best = r[0]

    lines = [f"Ranked {len(r)} stocks by weighted setup quality.", ""]
    lines.append(f"STRONGEST: {best['symbol']} ({best['verdict']}, "
                 f"score {best['score']}, confidence {best['confidence']}/100).")
    if best["bull"]:
        lines.append(f"  Why: {best['bull'][0]}")
    if best["verdict"] in ("BUY ON TRIGGER", "WAIT"):
        lines.append(f"  Not yet actionable — needs a close above "
                     f"{best['levels']['trigger']}.")

    trig = [x for x in r if x["verdict"] == "BUY ON TRIGGER"]
    if trig:
        lines.append("")
        lines.append("AWAITING TRIGGER: " + ", ".join(
            f"{x['symbol']} (>{x['levels']['trigger']})" for x in trig[:6]))

    traps = [x for x in r if any("TRAP PATTERN" in f for f in x["flags"])]
    if traps:
        lines.append("")
        lines.append("TRAP PATTERN (weekly accumulation, daily distribution) — "
                     "wait for the daily to turn: " +
                     ", ".join(x["symbol"] for x in traps))

    avoid = [x for x in r if x["verdict"] == "AVOID"]
    if avoid:
        lines.append("")
        lines.append("AVOID: " + ", ".join(x["symbol"] for x in avoid) +
                     ". Trend and flow both negative — no edge in owning these.")

    n_up = sum(1 for x in r if x["state"]["dTrend"] == "UP")
    n_above = sum(1 for x in r if x["state"]["cloud"] == "above")
    lines.append("")
    lines.append(f"BREADTH OF THIS LIST: {n_up}/{len(r)} with daily trend up, "
                 f"{n_above}/{len(r)} above the daily cloud.")
    if n_up / len(r) < 0.35:
        lines.append("Weak internal breadth — size down and demand the trigger "
                     "rather than anticipating it.")
    return {"ranked": r, "commentary": "\n".join(lines)}
