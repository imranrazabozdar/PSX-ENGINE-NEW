#!/usr/bin/env python3
"""
psx_wyckoff.py — a mechanical Wyckoff analyst for PSX price/volume data.

Reads ONLY price and volume. No fundamentals, no news, no indicators from
psx_report — deliberately, so the Wyckoff read is an independent second
opinion rather than a restatement of the Supertrend/Ichimoku verdict.

WHAT IT DOES
  1. Finds the most recent horizontal trading range (the "cause").
  2. Classifies it: accumulation / re-accumulation / distribution /
     re-distribution — from the trend INTO the range plus where the volume
     is going inside it.
  3. Labels the schematic events it can actually justify: PS, SC/BC, AR,
     ST, Spring, UT/UTAD, SOS/SOW, LPS/LPSY.
  4. Assigns a phase (A-E) and grades every Spring/Upthrust High/Med/Low
     against the classic criteria — volume, penetration depth, recovery
     speed, close location, and whether a successful test followed.
  5. Applies the three laws explicitly, including effort-vs-result.
  6. Projects cause-and-effect targets and states PSX-specific risks.

CONSERVATIVE BY DESIGN
  Nothing is labelled unless it meets the criteria. "No valid Spring" is a
  normal, correct output. Where a range cannot be found at all the module
  says so and reports trend context only — it does not invent structure.

USAGE
    import psx_report, psx_wyckoff
    df = psx_report.load_from_psx("PSO", 3)
    r  = psx_wyckoff.analyse("PSO", df, timeframe="daily")
    print(r["narrative"])
"""

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------
# tuning — all thresholds in one place so they can be argued with
# --------------------------------------------------------------------------

CFG = {
    "vol_window": 30,        # bars for the "normal volume" baseline
    "climax_vol": 1.8,       # x median volume to qualify as climactic
    "climax_spread": 1.35,   # x median spread to qualify as climactic
    "wide_spread": 1.5,      # x median spread = "expanding spread" (SOS/SOW)
    "sos_vol": 1.3,          # x median volume for a Sign of Strength
    "light_vol": 1.20,       # at or below this = light volume
    "heavy_vol": 2.00,       # above this = heavy volume
    "edge_tol": 0.15,        # within this fraction of range height = "at" a boundary
    "spring_recover_bars": 3,   # must close back inside within this many bars
    "phase_c_position": 0.45,   # Spring/UT must sit this far into the range
    "min_range_bars": 18,
    "max_range_bars": 200,
    "max_end_offset": 40,    # how far back the range may have ended (Phase E)
    "range_score_floor": 3.2,
    "min_boundary_touches": 2,
    "min_penetration": 0.02,   # a poke smaller than this fraction of the range
                               # height is noise, not a Spring or an Upthrust
    "min_penetration_pct": 0.3,# ...and it must also be >0.3% of price
    "cluster_bars": 5,         # penetrations this close together are ONE event
    "max_labelled": 3,         # keep the most significant few, not every wobble
}

PROB = ("Low", "Medium", "High")


# --------------------------------------------------------------------------
# bar-level features
# --------------------------------------------------------------------------

def _features(df):
    """Per-bar effort (volume) and result (spread, close location) measures.

    Everything is expressed as a ratio to a rolling MEDIAN, not a mean:
    PSX volume is spiky and a single 20x day would otherwise reset the
    baseline and hide every later climax.
    """
    f = pd.DataFrame(index=df.index)
    w = CFG["vol_window"]
    f["close"] = df.close.astype(float)
    f["open"] = df.open.astype(float) if "open" in df else df.close.astype(float)
    f["high"] = df.high.astype(float)
    f["low"] = df.low.astype(float)
    f["volume"] = df.volume.astype(float)

    med_v = f.volume.rolling(w, min_periods=8).median()
    f["vr"] = (f.volume / med_v.replace(0, np.nan)).fillna(1.0)

    f["spread"] = (f.high - f.low).abs()
    med_s = f.spread.rolling(w, min_periods=8).median()
    f["sr"] = (f.spread / med_s.replace(0, np.nan)).fillna(1.0)

    rng = f.spread.replace(0, np.nan)
    f["clp"] = ((f.close - f.low) / rng).fillna(0.5).clip(0, 1)
    f["body"] = ((f.close - f.open) / rng).fillna(0.0).clip(-1, 1)
    f["up"] = f.close >= f.close.shift()
    return f


def _slope_pct(series):
    """Least-squares slope over the whole series, as % of its mean."""
    y = np.asarray(series, dtype=float)
    n = len(y)
    if n < 3:
        return 0.0
    x = np.arange(n)
    b = np.polyfit(x, y, 1)[0]
    m = float(np.mean(y)) or 1.0
    return b * n / m * 100


# --------------------------------------------------------------------------
# 1. trading range detection
# --------------------------------------------------------------------------

def _score_window(f, lo_i, hi_i):
    """Quality of the window [lo_i, hi_i) as a horizontal trading range."""
    w = f.iloc[lo_i:hi_i]
    if len(w) < CFG["min_range_bars"]:
        return None
    support = float(np.percentile(w.low, 8))
    resist = float(np.percentile(w.high, 92))
    height = resist - support
    mid = (resist + support) / 2
    if height <= 0 or mid <= 0:
        return None
    rel_height = height / mid
    # A "range" that is 90% tall is a trend; one that is 2% tall is a coma.
    if not (0.055 <= rel_height <= 0.60):
        return None

    tol = CFG["edge_tol"] * height
    touch_lo = int((w.low <= support + tol).sum())
    touch_hi = int((w.high >= resist - tol).sum())
    if touch_lo < CFG["min_boundary_touches"] or touch_hi < CFG["min_boundary_touches"]:
        return None

    # Strict containment: a window that swallows a breakout must NOT look
    # well-contained, or the boundaries get dragged along by the move that
    # ended the range.
    inside = float(((w.close >= support) & (w.close <= resist)).mean())
    drift = abs(_slope_pct(w.close)) / (rel_height * 100)   # net travel vs height
    # A window that travels further than its own height is a trend leg, not a
    # range. Without this the scanner happily "finds" ranges inside a clean
    # markup and then invents Springs at their edges.
    if drift > 0.95 or inside < 0.62:
        return None

    score = (inside * 2.4
             + min(touch_lo, 5) * 0.32
             + min(touch_hi, 5) * 0.32
             + max(0.0, 1.0 - min(drift, 1.4)) * 1.7
             + np.log1p(len(w)) * 0.55
             - max(0.0, rel_height - 0.40) * 3.0)

    return {"start_i": lo_i, "end_i": hi_i, "bars": len(w),
            "support": support, "resistance": resist, "height": height,
            "mid": mid, "rel_height": rel_height, "inside": inside,
            "touch_lo": touch_lo, "touch_hi": touch_hi,
            "drift": drift, "score": float(score)}


def _find_range(f):
    """Best-scoring recent trading range, or None if price is simply trending."""
    n = len(f)
    best = None
    max_end = min(CFG["max_end_offset"], max(0, n - CFG["min_range_bars"] - 1))
    for end_off in range(0, max_end + 1, 2):
        hi_i = n - end_off
        for L in range(CFG["min_range_bars"], CFG["max_range_bars"] + 1, 3):
            lo_i = hi_i - L
            if lo_i < 5:
                break
            s = _score_window(f, lo_i, hi_i)
            if s and (best is None or s["score"] > best["score"]):
                best = s
    if not best or best["score"] < CFG["range_score_floor"]:
        return None

    # If the final bars of the winning window broke decisively out of it, the
    # range ENDED there. Trim it back to the last bar that closed inside and
    # recompute the boundaries from that window only — otherwise the breakout
    # itself inflates the resistance it just cleared.
    edge = 0.15 * best["height"]
    tail = f.iloc[best["start_i"]:best["end_i"]]
    outside = (tail.close > best["resistance"] + edge) | \
              (tail.close < best["support"] - edge)
    if bool(outside.any()):
        pos = np.flatnonzero(outside.values)
        # only trim a breakout that runs to the END of the window
        if pos[-1] >= len(tail) - 1:
            run = int(pos[-1])
            allpos = set(int(x) for x in pos.tolist())
            while run - 1 in allpos:
                run -= 1
            new_end = int(best["start_i"]) + run
            trimmed = _score_window(f, best["start_i"], new_end)
            if trimmed:
                trimmed["score"] = best["score"]
                best = trimmed
    best["ended_bars_ago"] = n - best["end_i"]
    return best


def _prior_trend(f, start_i, look=45):
    """Direction of travel INTO the range — decides accumulation vs distribution."""
    a = max(0, start_i - look)
    if start_i - a < 8:
        return {"dir": "unknown", "pct": None,
                "note": "not enough history before the range to judge the prior trend"}
    p0 = float(f.close.iloc[a])
    p1 = float(f.close.iloc[start_i])
    pct = (p1 / p0 - 1) * 100 if p0 else 0.0
    d = "down" if pct <= -8 else "up" if pct >= 8 else "sideways"
    return {"dir": d, "pct": round(pct, 1),
            "note": f"price moved {pct:+.1f}% over the {start_i - a} bars into the range"}


# --------------------------------------------------------------------------
# 2. event detection
# --------------------------------------------------------------------------

def _ts(f, i):
    try:
        return str(pd.Timestamp(f.index[i]).date())
    except Exception:
        return str(f.index[i])


def _ev(f, i, kind, note, **extra):
    b = f.iloc[i]
    e = {"kind": kind, "i": int(i), "date": _ts(f, i),
         "high": round(float(b.high), 2), "low": round(float(b.low), 2),
         "close": round(float(b.close), 2),
         "vol_x": round(float(b.vr), 2), "spread_x": round(float(b.sr), 2),
         "close_pos": round(float(b.clp) * 100), "note": note}
    e.update(extra)
    return e


def _find_climax(f, R, side):
    """SC (side='low') or BC (side='high') — Phase A stopping action.

    Searched in the first 45% of the range plus a 12-bar lead-in, because the
    climax that STOPS a trend usually prints as the range is being created.
    """
    a = max(0, R["start_i"] - 12)
    b = R["start_i"] + max(6, int(R["bars"] * 0.45))
    b = min(b, R["end_i"])
    tol = 0.22 * R["height"]
    best, best_k = None, 0.0
    for i in range(a, b):
        r = f.iloc[i]
        if r.vr < CFG["climax_vol"] or r.sr < CFG["climax_spread"]:
            continue
        if side == "low":
            if r.low > R["support"] + tol:
                continue
            falling = i >= 5 and f.close.iloc[i] < f.close.iloc[i - 5]
            if not falling:
                continue
        else:
            if r.high < R["resistance"] - tol:
                continue
            rising = i >= 5 and f.close.iloc[i] > f.close.iloc[i - 5]
            if not rising:
                continue
        k = float(r.vr) * float(r.sr)
        if k > best_k:
            best_k, best = k, i
    if best is None:
        return None
    if side == "low":
        note = ("Selling Climax — heavy volume, wide spread into new lows, "
                "then a close off the low: supply being absorbed")
        return _ev(f, best, "SC", note)
    note = ("Buying Climax — heavy volume and wide spread into new highs "
            "with the close well off the high: demand being met by supply")
    return _ev(f, best, "BC", note)


def _find_preliminary(f, R, climax, side):
    """PS / PSY — the first heavy-volume warning bar BEFORE the climax."""
    if not climax:
        return None
    a = max(0, R["start_i"] - 30)
    best, best_v = None, 0.0
    for i in range(a, climax["i"]):
        r = f.iloc[i]
        if r.vr >= 1.6 and r.sr >= 1.2 and r.vr > best_v:
            best_v, best = float(r.vr), i
    if best is None:
        return None
    kind = "PS" if side == "low" else "PSY"
    what = ("Preliminary Support — the first sign that substantial buying is "
            "meeting the decline; not yet a turn")
    if side == "high":
        what = ("Preliminary Supply — the first sign that substantial selling "
                "is meeting the advance; not yet a top")
    return _ev(f, best, kind, what)


def _find_ar(f, R, climax, side):
    """Automatic Rally / Reaction — the snap-back that sets the far boundary."""
    if not climax:
        return None
    a = climax["i"] + 1
    b = min(climax["i"] + 22, R["end_i"])
    if b <= a:
        return None
    seg = f.iloc[a:b]
    if side == "low":
        i = int(seg.high.values.argmax()) + a
        if float(f.high.iloc[i]) < climax["low"] + 0.35 * R["height"]:
            return None
        return _ev(f, i, "AR",
                   "Automatic Rally — supply exhausted, so light demand lifts "
                   "price easily. Its high sets the top of the range (the creek)")
    i = int(seg.low.values.argmin()) + a
    if float(f.low.iloc[i]) > climax["high"] - 0.35 * R["height"]:
        return None
    return _ev(f, i, "AR",
               "Automatic Reaction — demand exhausted, so light supply drops "
               "price easily. Its low sets the bottom of the range (the ice)")


def _find_tests(f, R, climax, ar, side, limit=4):
    """Secondary Tests — returns to the climax area on progressively less volume."""
    if not climax:
        return []
    start = (ar["i"] if ar else climax["i"]) + 1
    tol = CFG["edge_tol"] * R["height"]
    out, prev_v = [], float(climax["vol_x"])
    for i in range(start, R["end_i"]):
        r = f.iloc[i]
        if side == "low":
            at_edge = r.low <= R["support"] + tol
        else:
            at_edge = r.high >= R["resistance"] - tol
        if not at_edge or r.vr >= prev_v * 0.85:
            continue
        good = r.vr < float(climax["vol_x"]) * 0.6 and r.sr < 1.2
        note = ("Secondary Test — price returns to the climax area on lighter "
                "volume and a narrower spread, which is what a successful test "
                "looks like" if good else
                "Secondary Test — volume lighter than the climax but not "
                "decisively so; the test is inconclusive")
        out.append(_ev(f, i, "ST", note, quality="good" if good else "weak"))
        prev_v = float(r.vr)
        if len(out) >= limit:
            break
    return out


def _significant(depth_frac, distance, level):
    """Is this poke big enough to mean anything, or is it a tick of noise?"""
    if depth_frac < CFG["min_penetration"]:
        return False
    if level and (distance / level * 100) < CFG["min_penetration_pct"]:
        return False
    return True


def _cluster(events, key):
    """Collapse penetrations within CFG['cluster_bars'] into the deepest one.

    Five separate 'Springs' over six sessions are one shakeout described five
    times. Keeping them all is the over-labelling this module exists to avoid.
    """
    if not events:
        return []
    events = sorted(events, key=lambda e: e["i"])
    groups, cur = [], [events[0]]
    for e in events[1:]:
        if e["i"] - cur[-1]["i"] <= CFG["cluster_bars"]:
            cur.append(e)
        else:
            groups.append(cur)
            cur = [e]
    groups.append(cur)
    out = []
    for g in groups:
        best = max(g, key=lambda e: (e.get(key) or 0, e.get("criteria_met", 0)))
        if len(g) > 1:
            best = dict(best)
            best["merged"] = len(g)
            best["note"] += (f" ({len(g)} bars of this shakeout were merged into "
                             f"one event — the deepest is shown.)")
        out.append(best)
    return out


def _grade_penetration(depth_frac):
    if depth_frac < 0.08:
        return "minor"
    if depth_frac <= 0.20:
        return "moderate"
    return "major"


def _find_springs(f, R):
    """Springs / shakeouts: penetrate support, then close back inside fast.

    Rejects anything that does not close back inside within
    CFG['spring_recover_bars'] — that is a breakdown, not a Spring.
    """
    support, height = R["support"], R["height"]
    tol = CFG["edge_tol"] * height
    out = []
    i = R["start_i"]
    while i < R["end_i"]:
        r = f.iloc[i]
        if r.low >= support:
            i += 1
            continue
        # support must already be established by earlier touches
        earlier = f.iloc[R["start_i"]:i]
        if int((earlier.low <= support + tol).sum()) < CFG["min_boundary_touches"]:
            i += 1
            continue

        # find the extreme of this penetration and when it closes back inside
        j, low_i, low_v = i, i, float(r.low)
        recovered = None
        while j < min(i + CFG["spring_recover_bars"] + 1, R["end_i"]):
            if float(f.low.iloc[j]) < low_v:
                low_v, low_i = float(f.low.iloc[j]), j
            if float(f.close.iloc[j]) > support:
                recovered = j
                break
            j += 1

        depth_frac = (support - low_v) / height if height else 0
        if not _significant(depth_frac, support - low_v, support):
            i += 1
            continue
        pos = (low_i - R["start_i"]) / max(1, R["bars"])
        sb = f.iloc[low_i]
        vol_x, clp = float(sb.vr), float(sb.clp)

        if recovered is None:
            out.append({"kind": "BREAKDOWN", "i": int(low_i), "date": _ts(f, low_i),
                        "low": round(low_v, 2), "vol_x": round(vol_x, 2),
                        "depth_pct_of_range": round(depth_frac * 100, 1),
                        "penetration": _grade_penetration(depth_frac),
                        "probability": "n/a", "criteria": [],
                        "note": ("Penetrated support and did NOT close back inside "
                                 f"within {CFG['spring_recover_bars']} bars. Labelling "
                                 "this a Spring would be hindsight bias — on the "
                                 "evidence it is a breakdown / Sign of Weakness.")})
            i = max(j, i + 1)
            continue

        bars_to_recover = recovered - i + 1

        # a successful test: later higher low near support on lighter volume
        test = None
        for k in range(recovered + 1, min(recovered + 14, R["end_i"])):
            rk = f.iloc[k]
            if float(rk.low) > low_v and float(rk.low) <= support + tol \
                    and float(rk.vr) < vol_x * 0.85:
                test = _ev(f, k, "ST(Spring)",
                           "Test of the Spring — higher low on lighter volume: "
                           "supply is not returning at these prices")
                break

        crit = []
        crit.append(("late in the range (Phase C position)", pos >= CFG["phase_c_position"]))
        crit.append(("volume light-to-moderate on the penetration",
                     vol_x <= CFG["heavy_vol"]))
        crit.append((f"closed back inside within {bars_to_recover} bar(s)",
                     bars_to_recover <= CFG["spring_recover_bars"]))
        crit.append(("close in the upper half of the spring bar's range", clp >= 0.5))
        crit.append(("successful test followed (higher low, lighter volume)",
                     test is not None))
        met = sum(1 for _, ok in crit if ok)

        vol_word = ("light" if vol_x <= CFG["light_vol"]
                    else "moderate" if vol_x <= CFG["heavy_vol"] else "heavy")
        out.append({
            "kind": "SPRING", "i": int(low_i), "date": _ts(f, low_i),
            "low": round(low_v, 2), "support": round(support, 2),
            "close": round(float(sb.close), 2),
            "vol_x": round(vol_x, 2), "vol_word": vol_word,
            "spread_x": round(float(sb.sr), 2),
            "close_pos": round(clp * 100),
            "depth_pct_of_range": round(depth_frac * 100, 1),
            "penetration": _grade_penetration(depth_frac),
            "bars_to_recover": int(bars_to_recover),
            "position_in_range": round(pos * 100),
            "test": test, "criteria": crit, "criteria_met": met,
            "probability": PROB[2] if met >= 5 else PROB[1] if met >= 3 else PROB[0],
            "note": (f"Price undercut support at {support:.2f} to {low_v:.2f} "
                     f"({_grade_penetration(depth_frac)} penetration, "
                     f"{depth_frac*100:.1f}% of range height) on {vol_word} volume "
                     f"({vol_x:.1f}x normal), closing back inside after "
                     f"{bars_to_recover} bar(s) with the close at "
                     f"{clp*100:.0f}% of that bar's range."),
        })
        i = max(recovered, i + 1)
    real = _cluster([e for e in out if e["kind"] == "SPRING"], "depth_pct_of_range")
    other = [e for e in out if e["kind"] != "SPRING"]
    return (real[-CFG["max_labelled"]:] + other[-2:])


def _find_upthrusts(f, R, distributive):
    """Upthrusts / UTADs: poke above resistance, fail, close back inside."""
    resist, height = R["resistance"], R["height"]
    tol = CFG["edge_tol"] * height
    out = []
    i = R["start_i"]
    while i < R["end_i"]:
        r = f.iloc[i]
        if r.high <= resist:
            i += 1
            continue
        earlier = f.iloc[R["start_i"]:i]
        if int((earlier.high >= resist - tol).sum()) < CFG["min_boundary_touches"]:
            i += 1
            continue

        j, hi_i, hi_v = i, i, float(r.high)
        rejected = None
        while j < min(i + CFG["spring_recover_bars"] + 1, R["end_i"]):
            if float(f.high.iloc[j]) > hi_v:
                hi_v, hi_i = float(f.high.iloc[j]), j
            if float(f.close.iloc[j]) < resist:
                rejected = j
                break
            j += 1

        depth_frac = (hi_v - resist) / height if height else 0
        if not _significant(depth_frac, hi_v - resist, resist):
            i += 1
            continue
        pos = (hi_i - R["start_i"]) / max(1, R["bars"])
        ub = f.iloc[hi_i]
        vol_x, clp = float(ub.vr), float(ub.clp)

        # A bar that closes near its HIGH has not failed, whatever it did to
        # the resistance line. Calling that an upthrust is the classic
        # over-label, so it is rejected outright rather than graded Low.
        if rejected is not None and clp > 0.60:
            i = max(rejected, i + 1)
            continue

        # ...and if price makes a higher high straight afterwards, the poke was
        # continuation, not rejection.
        if rejected is not None:
            fwd = f.iloc[rejected + 1:min(rejected + 4, len(f))]
            if len(fwd) and float(fwd.high.max()) > hi_v:
                i = max(rejected, i + 1)
                continue

        if rejected is None:
            out.append({"kind": "BREAKOUT", "i": int(hi_i), "date": _ts(f, hi_i),
                        "high": round(hi_v, 2), "vol_x": round(vol_x, 2),
                        "depth_pct_of_range": round(depth_frac * 100, 1),
                        "probability": "n/a", "criteria": [],
                        "note": ("Cleared resistance and held — it did not close "
                                 f"back inside within {CFG['spring_recover_bars']} "
                                 "bars, so this is a breakout / Jump Across the "
                                 "Creek, not an Upthrust.")})
            i = max(j, i + 1)
            continue

        bars_to_reject = rejected - i + 1

        # narrowing spreads INTO resistance is the quality tell
        pre = f.iloc[max(R["start_i"], hi_i - 4):hi_i]
        narrowing = bool(len(pre) >= 2 and float(pre.sr.mean()) < 1.05)

        sow = None
        for k in range(rejected + 1, min(rejected + 14, R["end_i"])):
            rk = f.iloc[k]
            if float(rk.close) < float(rk.open) and rk.sr >= CFG["wide_spread"] \
                    and rk.vr >= CFG["sos_vol"]:
                sow = _ev(f, k, "SOW(after UT)",
                          "Sign of Weakness after the upthrust — wide down "
                          "spread on increased volume: supply has taken control")
                break

        late = pos >= 0.60
        is_utad = bool(late and distributive)
        crit = []
        crit.append(("late in the range (Phase C position)",
                     pos >= CFG["phase_c_position"]))
        crit.append(("volume elevated on the penetration", vol_x >= 1.30))
        crit.append((f"closed back inside within {bars_to_reject} bar(s)",
                     bars_to_reject <= CFG["spring_recover_bars"]))
        crit.append(("close in the lower half of the bar's range", clp <= 0.5))
        crit.append(("spreads narrowed approaching resistance", narrowing))
        crit.append(("Sign of Weakness followed", sow is not None))
        met = sum(1 for _, ok in crit if ok)

        out.append({
            "kind": "UTAD" if is_utad else "UT",
            "i": int(hi_i), "date": _ts(f, hi_i),
            "high": round(hi_v, 2), "resistance": round(resist, 2),
            "close": round(float(ub.close), 2),
            "vol_x": round(vol_x, 2), "spread_x": round(float(ub.sr), 2),
            "close_pos": round(clp * 100),
            "depth_pct_of_range": round(depth_frac * 100, 1),
            "penetration": _grade_penetration(depth_frac),
            "bars_to_reject": int(bars_to_reject),
            "position_in_range": round(pos * 100),
            "narrowing_spreads": narrowing, "sow": sow,
            "criteria": crit, "criteria_met": met,
            "probability": PROB[2] if met >= 5 else PROB[1] if met >= 3 else PROB[0],
            "note": (f"Price pushed above resistance at {resist:.2f} to {hi_v:.2f} "
                     f"({depth_frac*100:.1f}% of range height) on {vol_x:.1f}x "
                     f"volume, then closed back inside after {bars_to_reject} "
                     f"bar(s) with the close at {clp*100:.0f}% of that bar's range."
                     + (" Late-stage position and a distributive structure make "
                        "this an UTAD rather than a simple upthrust."
                        if is_utad else "")),
        })
        i = max(rejected, i + 1)
    real = _cluster([e for e in out if e["kind"] in ("UT", "UTAD")],
                    "depth_pct_of_range")
    other = [e for e in out if e["kind"] not in ("UT", "UTAD")]
    return (real[-CFG["max_labelled"]:] + other[-2:])


def _find_sos_sow(f, R, after_i, side, exclude=()):
    """SOS / SOW — expanding spread plus expanding volume in one direction.

    Searched across the whole range and everything after it, not only after the
    Phase C event: a Sign of Strength inside Phase B is still information. Each
    one is flagged `confirms_phase_c` when it postdates the Phase C event, which
    is what actually turns a Spring into a tradeable structure.
    """
    a = R["start_i"]
    skip = {int(x) for x in exclude if x is not None}
    out = []
    for i in range(a, len(f)):
        if i in skip:            # the climax itself is not a Sign of Strength
            continue
        r = f.iloc[i]
        if r.sr < CFG["wide_spread"] or r.vr < CFG["sos_vol"]:
            continue
        if i >= 1:               # a Sign of Strength/Weakness makes progress
            prev = float(f.close.iloc[i - 1])
            if side == "up" and float(r.close) <= prev:
                continue
            if side == "down" and float(r.close) >= prev:
                continue
        if side == "up":
            if float(r.close) <= float(r.open) or r.clp < 0.60:
                continue
            jumped = float(r.close) > R["resistance"]
            note = ("Sign of Strength — wide up spread on increased volume, "
                    "closing near the high" +
                    (". Close is above the range top: a Jump Across the Creek"
                     if jumped else " inside the range"))
            out.append(_ev(f, i, "SOS", note, jumped_creek=jumped,
                           confirms_phase_c=bool(after_i is not None and i > after_i)))
        else:
            if float(r.close) >= float(r.open) or r.clp > 0.40:
                continue
            broke = float(r.close) < R["support"]
            note = ("Sign of Weakness — wide down spread on increased volume, "
                    "closing near the low" +
                    (". Close is below the range floor: through the ice"
                     if broke else " inside the range"))
            out.append(_ev(f, i, "SOW", note, broke_ice=broke,
                           confirms_phase_c=bool(after_i is not None and i > after_i)))
        if len(out) >= 4:
            break
    return out


def _find_lps(f, R, sos_list, side):
    """LPS / LPSY — the low-volume pullback (or feeble rally) after SOS/SOW."""
    if not sos_list:
        return None
    start = sos_list[-1]["i"] + 1
    end = len(f)
    if end - start < 2:
        return None
    seg = f.iloc[start:end]
    if side == "up":
        i = int(seg.low.values.argmin()) + start
        after = f.iloc[start:i + 1]
        if len(after) < 2 or float(after.vr.mean()) >= 1.0:
            return None
        if float(f.low.iloc[i]) <= R["support"]:
            return None
        return _ev(f, i, "LPS",
                   "Last Point of Support — the pullback after the Sign of "
                   "Strength is holding a higher low on drying volume: no "
                   "supply came back. This is where the low-risk entry sits")
    i = int(seg.high.values.argmax()) + start
    after = f.iloc[start:i + 1]
    if len(after) < 2 or float(after.vr.mean()) >= 1.0:
        return None
    if float(f.high.iloc[i]) >= R["resistance"]:
        return None
    return _ev(f, i, "LPSY",
               "Last Point of Supply — the rally after the Sign of Weakness is "
               "feeble and on light volume, failing below resistance: demand "
               "is absent")


# --------------------------------------------------------------------------
# 3. the three laws
# --------------------------------------------------------------------------

def _law_supply_demand(f, R):
    w = f.iloc[R["start_i"]:R["end_i"]]
    up_v = float(w.loc[w.up, "volume"].sum())
    dn_v = float(w.loc[~w.up, "volume"].sum())
    tot = up_v + dn_v
    share = (up_v / tot * 100) if tot else 50.0
    if share >= 57:
        verdict = "demand has the upper hand inside the range"
    elif share <= 43:
        verdict = "supply has the upper hand inside the range"
    else:
        verdict = "supply and demand are near balance inside the range"
    return {"up_volume_share": round(share, 1), "verdict": verdict,
            "text": (f"{share:.0f}% of the volume inside the range printed on "
                     f"up bars — {verdict}.")}


def _law_cause_effect(R, f):
    """Range width x duration -> a horizontal-count style objective.

    This is an approximation of a point-and-figure count, not a count taken
    off a figure chart, and it is labelled as such wherever it is shown.
    """
    height, bars = R["height"], R["bars"]
    mult = float(np.clip(bars / 26.0, 1.0, 3.0))
    up_base = R["resistance"] + height * mult
    dn_base = max(0.01, R["support"] - height * mult)
    return {
        "cause_bars": bars, "height": round(height, 2),
        "height_pct": round(height / R["mid"] * 100, 1),
        "multiplier": round(mult, 2),
        "up_conservative": round(R["resistance"] + height, 2),
        "up_base": round(up_base, 2),
        "up_extended": round(R["resistance"] + height * min(3.0, mult * 1.5), 2),
        "down_conservative": round(max(0.01, R["support"] - height), 2),
        "down_base": round(dn_base, 2),
        "text": (f"{bars} bars of horizontal work across a {height:.2f} wide range "
                 f"({height/R['mid']*100:.1f}% of price) is the cause. Worked up as a "
                 f"horizontal count that projects roughly {R['resistance'] + height:.2f} "
                 f"to {up_base:.2f} on an upside resolution, or {max(0.01, R['support'] - height):.2f} "
                 f"to {dn_base:.2f} on a downside one. Treat these as magnitude "
                 f"estimates from the size of the cause, not price predictions."),
    }


def _law_effort_result(f, R, n=20):
    """Divergences: big effort with no result (absorption/supply) and the reverse."""
    w = f.iloc[max(R["start_i"], len(f) - n):]
    notes, absorb, churn = [], [], []
    for i in range(len(w)):
        r = w.iloc[i]
        big_effort = float(r.vr) >= 1.8
        small_result = abs(float(r.body)) < 0.35 and float(r.sr) < 1.2
        if big_effort and small_result:
            near_lo = float(r.low) <= R["support"] + 0.25 * R["height"]
            near_hi = float(r.high) >= R["resistance"] - 0.25 * R["height"]
            where = ("at support" if near_lo else "at resistance" if near_hi
                     else "mid-range")
            (absorb if near_lo else churn).append(
                f"{str(pd.Timestamp(w.index[i]).date())} "
                f"({r.vr:.1f}x volume, no progress, {where})")
        if float(r.sr) >= 1.6 and float(r.vr) <= 0.8:
            notes.append(f"{str(pd.Timestamp(w.index[i]).date())}: a wide bar on "
                         f"{r.vr:.1f}x volume — a large move on little effort, "
                         f"which points to a thin book rather than conviction")
    if absorb:
        notes.append("High effort with no downward result at support — that is "
                     "absorption: someone is taking the stock that is being sold. "
                     + "; ".join(absorb[:3]))
    if churn:
        notes.append("High effort with no upward result at resistance — that is "
                     "supply meeting demand. " + "; ".join(churn[:3]))
    if not notes:
        notes.append("No material effort-vs-result divergence in the recent bars: "
                     "volume and price movement are broadly proportionate.")
    return {"notes": notes, "absorption_bars": len(absorb), "churn_bars": len(churn)}


# --------------------------------------------------------------------------
# 4. phase & classification
# --------------------------------------------------------------------------

def _classify(prior, sd, springs, upthrusts, R):
    """Accumulation / re-accumulation / distribution / re-distribution."""
    good_springs = [s for s in springs
                    if s["kind"] == "SPRING" and s["probability"] != "Low"]
    real_uts = [u for u in upthrusts
                if u["kind"] in ("UT", "UTAD") and u["probability"] != "Low"]
    acc = 0.0
    if prior["dir"] == "down":
        acc += 2.0
    elif prior["dir"] == "up":
        acc -= 2.0
    acc += (sd["up_volume_share"] - 50) / 7.0
    acc += 1.4 * len(good_springs) - 1.4 * len(real_uts)

    if acc >= 1.0:
        base = "Accumulation" if prior["dir"] != "up" else "Re-accumulation"
    elif acc <= -1.0:
        base = "Distribution" if prior["dir"] != "down" else "Re-distribution"
    else:
        base = "Undetermined range"
    return base, round(acc, 2)


def _phase(R, f, climax, ar, tests, springs, upthrusts, sos, sow, structure):
    """Conservative phase call, with the reason stated."""
    n = len(f)
    last = float(f.close.iloc[-1])
    outside_up = last > R["resistance"]
    outside_dn = last < R["support"]
    good_spring = [s for s in springs if s["kind"] == "SPRING"
                   and s["probability"] != "Low"]
    real_ut = [u for u in upthrusts if u["kind"] in ("UT", "UTAD")
               and u["probability"] != "Low"]
    jumped = [s for s in sos if s.get("jumped_creek")]
    broke = [s for s in sow if s.get("broke_ice")]
    conf_sos = [s for s in sos if s.get("confirms_phase_c")] or sos
    conf_sow = [s for s in sow if s.get("confirms_phase_c")] or sow

    if outside_up and jumped:
        return "E", ("Phase E — price has left the range to the upside on a Sign "
                     "of Strength. The markup is under way; further action should "
                     "be judged against the new trend, not the old range.")
    if outside_dn and broke:
        return "E", ("Phase E — price has left the range to the downside through "
                     "the ice. Markdown is under way.")
    if good_spring and conf_sos:
        return "D", ("Phase D — a Phase C shakeout is behind us and demand has "
                     "since produced a Sign of Strength. This is the part of the "
                     "structure where the stock should work its way to the range "
                     "top.")
    if real_ut and conf_sow:
        return "D", ("Phase D — an upthrust is behind us and supply has since "
                     "produced a Sign of Weakness. Rallies from here are likely "
                     "to be distribution, not accumulation.")
    if good_spring or real_ut:
        return "C", ("Phase C — the test of the range is in place (%s) but it has "
                     "not yet been confirmed by a decisive Sign of %s. This is the "
                     "highest-reward and highest-uncertainty point in the schematic."
                     % ("Spring" if good_spring else "Upthrust",
                        "Strength" if good_spring else "Weakness"))
    if climax and ar and len(tests) <= 1 and R["bars"] <= 40:
        return "A", ("Phase A — stopping action only. The climax and automatic "
                     "reaction have set the boundaries; the range has not yet "
                     "built enough cause to matter.")
    if climax or R["bars"] >= 30:
        return "B", ("Phase B — the range is building cause. Price is working "
                     "between the boundaries, tests are happening on both sides, "
                     "and no decisive Phase C event has appeared yet. This phase "
                     "can last much longer than it feels like it should.")
    return "?", ("Phase undetermined — the range is identifiable but the "
                 "schematic events needed to place it in the sequence are not "
                 "clearly present. More data would help.")


# --------------------------------------------------------------------------
# 5. PSX-specific risk
# --------------------------------------------------------------------------

def _psx_risks(f, df, bench=None):
    out = []
    close, vol = f.close, f.volume
    val20 = float((close * vol).rolling(20).mean().iloc[-1])
    if val20 < 2e6:
        out.append(f"LIQUIDITY: about Rs {val20/1e6:.2f}M changes hands per day. "
                   f"At that size the spread and the exit are the real risk, not "
                   f"the analysis. Size positions so you could get out on one "
                   f"bad day, and expect the Wyckoff levels to be violated by "
                   f"noise more often than they would be in a liquid name.")
    else:
        out.append(f"Liquidity is workable: about Rs {val20/1e6:.1f}M traded per "
                   f"day over the last 20 sessions.")

    prev = close.shift()
    gaps = ((f.open - prev).abs() / prev).dropna()
    gap_rate = float((gaps > 0.02).mean() * 100) if len(gaps) else 0.0
    tr = (f.high - f.low) / close
    atr_pct = float(tr.rolling(14).mean().iloc[-1] * 100)
    out.append(f"GAP RISK: {gap_rate:.0f}% of sessions opened more than 2% away "
               f"from the previous close, and the average daily range is "
               f"{atr_pct:.1f}%. PSX price limits mean a fast move can lock the "
               f"stock at the band before your stop can fill, so a stop level is "
               f"an intention, not a guarantee.")

    if bench is not None:
        try:
            b = pd.Series(bench).reindex(f.index).ffill()
            n = min(63, len(f) - 1)
            sr = f.close.pct_change().tail(n)
            br = b.pct_change().tail(n)
            corr = float(sr.corr(br))
            sx = (float(f.close.iloc[-1]) / float(f.close.iloc[-n]) - 1) * 100
            bx = (float(b.iloc[-1]) / float(b.iloc[-n]) - 1) * 100
            out.append(f"MARKET CORRELATION: {corr:+.2f} daily correlation with "
                       f"the index over 3 months; the stock did {sx:+.1f}% against "
                       f"the index's {bx:+.1f}%. "
                       + ("At that correlation the index decides most of the "
                          "outcome — a bullish structure here still fails if "
                          "KSE-100 rolls over."
                          if corr > 0.5 else
                          "The low correlation means this structure can resolve "
                          "largely on its own, which cuts both ways."))
        except Exception:
            pass
    return out


# --------------------------------------------------------------------------
# main entry
# --------------------------------------------------------------------------

def analyse(symbol, df, timeframe="daily", bench=None, lookback=320,
            min_bars=None):
    """Full Wyckoff read. Returns a dict; ['narrative'] is the written analysis.

    min_bars defaults to 60 daily bars but only 40 weekly ones — three years of
    daily history is ~156 weekly bars, and demanding 60 of those would refuse
    perfectly readable weekly structures on any recently listed name.
    """
    if min_bars is None:
        min_bars = 40 if timeframe == "weekly" else 60
    if df is None or len(df) < min_bars:
        have = 0 if df is None else len(df)
        return {"symbol": symbol, "timeframe": timeframe, "ok": False,
                "error": f"Need at least {min_bars} {timeframe} bars; have {have}.",
                "narrative": f"{symbol}: {have} {timeframe} bars is not enough for a "
                             f"Wyckoff read — at least {min_bars} are needed to "
                             f"establish a range and count touches of its "
                             f"boundaries. Nothing is inferred from a shorter "
                             f"window."}

    d = df.tail(lookback).copy()
    f = _features(d)
    last = float(f.close.iloc[-1])
    R = _find_range(f)

    # ---------- no range: report the trend honestly and stop ----------
    if R is None:
        sl = _slope_pct(f.close.tail(60))
        direction = "markup (uptrend)" if sl > 6 else \
                    "markdown (downtrend)" if sl < -6 else "drifting"
        hi = float(f.high.tail(120).max())
        lo = float(f.low.tail(120).min())
        nar = "\n".join([
            f"WYCKOFF READ — {symbol} ({timeframe})",
            f"Last price {last:.2f}",
            "",
            "1. CONTEXT AND PHASE",
            f"   No well-defined horizontal trading range is present in the recent "
            f"{min(lookback, len(d))} bars. Price is in a {direction}, which in "
            f"Wyckoff terms is Phase E of a structure whose range sits further "
            f"back than this window, or a trend that has not yet stopped.",
            "   Forcing range boundaries onto trending data would produce "
            "invented Springs and Upthrusts, so no events are labelled.",
            "",
            "2. WHAT WOULD CHANGE THIS",
            f"   A range needs a stopping action — a climax on heavy volume and "
            f"wide spread, then an automatic reaction — followed by sideways work "
            f"between two boundaries. Watch the {lo:.2f}-{hi:.2f} extremes of the "
            f"last 120 bars: the first climactic bar near either end is where the "
            f"next range will start to build.",
            "",
            "3. ASSUMPTIONS AND DATA GAPS",
            "   This read uses end-of-day price and volume only. Intraday spread "
            "and closing behaviour within the day, which Wyckoff analysis leans "
            "on, are not visible in EOD bars.",
        ])
        return {"symbol": symbol, "timeframe": timeframe, "ok": True,
                "price": round(last, 2), "range": None,
                "structure": "No trading range", "phase": "E?",
                "bias": "trend-following, not structural",
                "events": [], "springs": [], "upthrusts": [],
                "risks": _psx_risks(f, d, bench), "narrative": nar,
                "confidence": "Low"}

    # ---------- classification needs a first pass at both sides ----------
    prior = _prior_trend(f, R["start_i"])
    sd = _law_supply_demand(f, R)
    springs_pre = _find_springs(f, R)
    ut_pre = _find_upthrusts(f, R, distributive=(prior["dir"] == "up"))
    structure, acc_score = _classify(prior, sd, springs_pre, ut_pre, R)

    distributive = structure.startswith(("Distribution", "Re-distribution"))
    side = "high" if distributive else "low"

    climax = _find_climax(f, R, side)
    prelim = _find_preliminary(f, R, climax, side)
    ar = _find_ar(f, R, climax, side)
    tests = _find_tests(f, R, climax, ar, side)

    springs = springs_pre
    upthrusts = _find_upthrusts(f, R, distributive=distributive)

    # The Phase C anchor is the LAST CREDIBLE event on the side the structure
    # is actually on. Taking max() over every marginal poke in either direction
    # let a stray Low-probability upthrust sit after a real SOS and hide it.
    good_sp = [s for s in springs if s["kind"] == "SPRING" and s["probability"] != "Low"]
    good_ut = [u for u in upthrusts if u["kind"] in ("UT", "UTAD")
               and u["probability"] != "Low"]
    prefer = good_ut if distributive else good_sp
    fallback = (good_sp + good_ut) or \
               [s for s in springs if s["kind"] == "SPRING"] + \
               [u for u in upthrusts if u["kind"] in ("UT", "UTAD")]
    pick = prefer or fallback
    phase_c_i = max((e["i"] for e in pick), default=None)
    cands = [e["i"] for e in pick]

    climax_bars = [e["i"] for e in (climax, prelim, ar) if e]
    sos = _find_sos_sow(f, R, phase_c_i, "up", exclude=climax_bars)
    sow = _find_sos_sow(f, R, phase_c_i, "down", exclude=climax_bars)
    lps = _find_lps(f, R, [e for e in sos if e.get("confirms_phase_c")] or sos, "up")
    lpsy = _find_lps(f, R, [e for e in sow if e.get("confirms_phase_c")] or sow, "down")

    # ---- boundary refinement -------------------------------------------
    # Wyckoff defines the far boundary by the Automatic Rally / Reaction, not by
    # a percentile. Where an AR exists, use it: the percentile boundary drifts
    # toward a late breakout and then reports price as "100% of the way up the
    # range" when it has actually already left.
    boundary_note = ("Boundaries are the 8th/92nd percentile of lows and highs "
                     "in the range window.")
    if ar:
        if not distributive and ar["high"] < R["resistance"]:
            R["resistance"] = float(ar["high"])
            boundary_note = ("Resistance refined DOWN to the Automatic Rally high "
                             f"({ar['high']:.2f}, {ar['date']}) — in the method the "
                             "AR sets the creek, and the percentile boundary was "
                             "being pulled up by the later breakout.")
        elif distributive and ar["low"] > R["support"]:
            R["support"] = float(ar["low"])
            boundary_note = ("Support refined UP to the Automatic Reaction low "
                             f"({ar['low']:.2f}, {ar['date']}) — the AR sets the ice.")
        R["height"] = R["resistance"] - R["support"]
        R["mid"] = (R["resistance"] + R["support"]) / 2
        R["rel_height"] = R["height"] / R["mid"] if R["mid"] else 0

    phase, phase_why = _phase(R, f, climax, ar, tests, springs, upthrusts,
                              sos, sow, structure)
    ce = _law_cause_effect(R, f)
    er = _law_effort_result(f, R)

    # ---------- current strength vs weakness ----------
    strength, weakness = [], []
    for s in springs:
        if s["kind"] == "SPRING" and s["probability"] != "Low":
            strength.append(f"{s['probability']}-probability Spring on {s['date']} "
                            f"({s['penetration']} penetration, {s['vol_word']} volume)"
                            + (" with a successful test" if s.get("test") else
                               " — no successful test yet"))
        elif s["kind"] == "BREAKDOWN":
            weakness.append(f"Support broken on {s['date']} with no recovery "
                            f"inside {CFG['spring_recover_bars']} bars")
    for u in upthrusts:
        if u["kind"] in ("UT", "UTAD") and u["probability"] != "Low":
            weakness.append(f"{u['probability']}-probability {u['kind']} on "
                            f"{u['date']} ({u['vol_x']:.1f}x volume, close at "
                            f"{u['close_pos']}% of range)")
        elif u["kind"] == "BREAKOUT":
            strength.append(f"Resistance cleared and held on {u['date']}")
    if sos:
        strength.append(f"{len(sos)} Sign(s) of Strength, latest {sos[-1]['date']}"
                        + (" clearing the range top" if sos[-1].get("jumped_creek")
                           else " inside the range"))
    if sow:
        weakness.append(f"{len(sow)} Sign(s) of Weakness, latest {sow[-1]['date']}"
                        + (" through the range floor" if sow[-1].get("broke_ice")
                           else " inside the range"))
    if lps:
        strength.append(f"Last Point of Support holding at {lps['low']:.2f} "
                        f"({lps['date']}) on drying volume")
    if lpsy:
        weakness.append(f"Last Point of Supply at {lpsy['high']:.2f} "
                        f"({lpsy['date']}) — rally failed on light volume")
    if er["absorption_bars"]:
        strength.append(f"{er['absorption_bars']} recent bar(s) of heavy volume "
                        f"absorbed at support without further downside")
    if er["churn_bars"]:
        weakness.append(f"{er['churn_bars']} recent bar(s) of heavy volume at "
                        f"resistance with no upward progress")
    if sd["up_volume_share"] >= 57:
        strength.append(f"{sd['up_volume_share']:.0f}% of range volume on up bars")
    elif sd["up_volume_share"] <= 43:
        weakness.append(f"only {sd['up_volume_share']:.0f}% of range volume on "
                        f"up bars — sellers own the tape inside the range")

    # ---------- conclusion ----------
    net = len(strength) - len(weakness)
    if phase == "E" and last > R["resistance"]:
        bias, direction = "Bullish", "higher, with the range top as support"
    elif phase == "E" and last < R["support"]:
        bias, direction = "Bearish", "lower, with the range floor as resistance"
    elif phase == "D" and structure.endswith(("Accumulation", "accumulation")):
        bias, direction = "Bullish", "toward the top of the range and through it"
    elif phase == "D" and distributive:
        bias, direction = "Bearish", "toward the range floor and through it"
    elif phase == "C" and springs:
        bias, direction = "Cautiously bullish", ("higher IF the Spring is "
                                                 "confirmed by a Sign of Strength")
    elif phase == "C" and upthrusts:
        bias, direction = "Cautiously bearish", ("lower IF the upthrust is "
                                                 "confirmed by a Sign of Weakness")
    elif net >= 2:
        bias, direction = "Mildly bullish", "sideways with an upward lean"
    elif net <= -2:
        bias, direction = "Mildly bearish", "sideways with a downward lean"
    else:
        bias, direction = "Neutral", "sideways, inside the range"

    pos_in_range = (last - R["support"]) / R["height"] * 100 if R["height"] else 50
    conf_pts = sum([climax is not None, ar is not None, len(tests) >= 1,
                    bool(cands), bool(sos or sow), R["bars"] >= 30,
                    R["inside"] >= 0.8])
    confidence = "High" if conf_pts >= 6 else "Medium" if conf_pts >= 4 else "Low"

    # ---------- narrative, in the order the brief asks for ----------
    L = []
    A = L.append
    A(f"WYCKOFF READ — {symbol} ({timeframe} bars)")
    A(f"Last {last:.2f}   ·   {structure}, Phase {phase}   ·   "
      f"bias {bias}   ·   read confidence {confidence}")
    A("")
    A("1. CONTEXT AND PHASE")
    A(f"   {prior['note'].capitalize()}, so the range is being read as "
      f"{structure.lower()} (classification score {acc_score:+.2f}).")
    A(f"   {phase_why}")
    A("")
    A("2. TRADING RANGE BOUNDARIES")
    A(f"   Support / the ice : {R['support']:.2f}   ({R['touch_lo']} touches)")
    A(f"   Resistance / creek: {R['resistance']:.2f}   ({R['touch_hi']} touches)")
    A(f"   Height {R['height']:.2f} ({R['rel_height']*100:.1f}% of price) over "
      f"{R['bars']} bars; {R['inside']*100:.0f}% of closes sat inside the "
      f"boundaries.")
    A(f"   {boundary_note}")
    A(f"   Price is {pos_in_range:.0f}% of the way up the range right now"
      + (f", and the range last contained price {R['ended_bars_ago']} bars ago."
         if R.get("ended_bars_ago") else "."))
    A("")
    A("3. EVENTS IDENTIFIED")
    seq = [e for e in [prelim, climax, ar] if e] + tests
    seq += [s for s in springs if s["kind"] == "SPRING"]
    seq += [u for u in upthrusts if u["kind"] in ("UT", "UTAD")]
    seq += sos + sow + [e for e in (lps, lpsy) if e]
    seq = sorted(seq, key=lambda e: e["i"])
    if seq:
        for e in seq:
            A(f"   {e['date']}  {e['kind']:<12} {e.get('note', '')}")
    else:
        A("   None of the schematic events meet their criteria in this window. "
          "The range exists but its internal structure is not yet legible — "
          "that is a Phase B condition and it is not tradeable.")
    unlabelled = [s for s in springs if s["kind"] == "BREAKDOWN"] + \
                 [u for u in upthrusts if u["kind"] == "BREAKOUT"]
    if unlabelled:
        A("")
        A("   Deliberately NOT labelled:")
        for e in unlabelled:
            A(f"   {e['date']}  {e['kind']:<12} {e['note']}")
    A("")
    A("4. SPRING / UPTHRUST QUALITY")
    graded = [s for s in springs if s["kind"] == "SPRING"] + \
             [u for u in upthrusts if u["kind"] in ("UT", "UTAD")]
    if not graded:
        A("   No Spring and no Upthrust meet the criteria. Nothing is being "
          "labelled to fill the gap.")
    for e in graded:
        A(f"   {e['kind']} — {e['date']} — probability {e['probability']} "
          f"({e['criteria_met']}/{len(e['criteria'])} criteria)")
        A(f"     {e['note']}")
        for text, ok in e["criteria"]:
            A(f"     [{'x' if ok else ' '}] {text}")
        t = e.get("test") or e.get("sow")
        A(f"     Follow-through: {t['note'] if t else 'none yet — unconfirmed'}")
    A("")
    A("5. STRENGTH VS WEAKNESS NOW")
    if strength:
        for s in strength:
            A(f"   + {s}")
    else:
        A("   + nothing on the strength side of the ledger")
    if weakness:
        for s in weakness:
            A(f"   - {s}")
    else:
        A("   - nothing on the weakness side of the ledger")
    A("")
    A("   Law of Supply and Demand: " + sd["text"])
    A("   Law of Cause and Effect: " + ce["text"])
    A("   Law of Effort vs Result:")
    for nte in er["notes"]:
        A(f"     · {nte}")
    A("")
    A("6. MOST PROBABLE NEXT DIRECTION AND LEVELS")
    A(f"   Bias: {bias} — the path of least resistance is {direction}.")
    A(f"   Invalidation: a decisive close "
      f"{'below ' + format(R['support'], '.2f') if not distributive else 'above ' + format(R['resistance'], '.2f')} "
      f"breaks the structure being described here and the read has to be redone.")
    A(f"   Levels to watch: {R['support']:.2f} (floor), "
      f"{R['mid']:.2f} (mid-range), {R['resistance']:.2f} (ceiling), "
      f"then {ce['up_conservative']:.2f} / {ce['up_base']:.2f} on an upside "
      f"resolution or {ce['down_conservative']:.2f} / {ce['down_base']:.2f} on a "
      f"downside one.")
    if phase in ("A", "B"):
        A("   Phase A and B ranges are not entries. The cause is still being "
          "built and the odds of chopping around are higher than the odds of a "
          "resolution in either direction.")
    A("")
    A("   PSX-SPECIFIC RISK")
    for r in _psx_risks(f, d, bench):
        A(f"     · {r}")
    A("")
    A("7. ASSUMPTIONS AND WHAT WOULD IMPROVE CONFIDENCE")
    A("   · End-of-day bars only. Wyckoff reads intraday spread and where the "
      "close sits within the day; on EOD data the close location is available "
      "but the sequence within the day is not.")
    A("   · Range boundaries are statistical (8th/92nd percentile of lows and "
      "highs), not hand-drawn. A boundary a discretionary analyst would place a "
      "few paisa differently can change a marginal Spring into a marginal test.")
    A(f"   · Volume is compared to a {CFG['vol_window']}-bar rolling median. On a "
      "thin PSX counter a single block trade can distort that baseline.")
    if confidence != "High":
        missing = []
        if not climax:
            missing.append("a visible climax to anchor Phase A")
            
        if not ar:
            missing.append("an automatic rally/reaction to confirm the boundary")
        if not tests:
            missing.append("at least one secondary test")
        if not cands:
            missing.append("a Phase C event (Spring or Upthrust)")
        if not (sos or sow):
            missing.append("a Sign of Strength or Weakness to confirm direction")
        if missing:
            A("   · Confidence is capped because the structure is missing "
              + ", ".join(missing) + ".")
    A("   · Volume by participant type (foreign vs local, broker-level) would "
      "materially sharpen the absorption reading. It is not in the EOD feed.")

    return {
        "symbol": symbol, "timeframe": timeframe, "ok": True,
        "price": round(last, 2),
        "structure": structure, "structure_score": acc_score,
        "phase": phase, "phase_note": phase_why,
        "bias": bias, "direction": direction, "confidence": confidence,
        "position_in_range": round(pos_in_range),
        "range": {"support": round(R["support"], 2),
                  "resistance": round(R["resistance"], 2),
                  "mid": round(R["mid"], 2),
                  "height": round(R["height"], 2),
                  "height_pct": round(R["rel_height"] * 100, 1),
                  "bars": R["bars"], "touch_lo": R["touch_lo"],
                  "touch_hi": R["touch_hi"],
                  "inside_pct": round(R["inside"] * 100),
                  "start": _ts(f, R["start_i"]),
                  "end": _ts(f, min(R["end_i"], len(f) - 1)),
                  "ended_bars_ago": R.get("ended_bars_ago", 0),
                  "quality": round(R["score"], 2),
                  "boundary_note": boundary_note},
        "prior_trend": prior,
        "events": seq, "not_labelled": unlabelled,
        "springs": [s for s in springs if s["kind"] == "SPRING"],
        "upthrusts": [u for u in upthrusts if u["kind"] in ("UT", "UTAD")],
        "sos": sos, "sow": sow, "lps": lps, "lpsy": lpsy,
        "strength": strength, "weakness": weakness,
        "laws": {"supply_demand": sd, "cause_effect": ce, "effort_result": er},
        "risks": _psx_risks(f, d, bench),
        "narrative": "\n".join(L),
    }


# --------------------------------------------------------------------------
# screening the market for Wyckoff structures
# --------------------------------------------------------------------------

def summarise(r):
    """One-row summary for a screening table."""
    if not r.get("ok"):
        return {"symbol": r["symbol"], "error": r.get("error")}
    R = r.get("range") or {}
    sp = r.get("springs") or []
    ut = r.get("upthrusts") or []
    best_sp = max(sp, key=lambda s: s["criteria_met"], default=None)
    best_ut = max(ut, key=lambda u: u["criteria_met"], default=None)
    return {
        "symbol": r["symbol"], "price": r["price"],
        "structure": r["structure"], "phase": r["phase"],
        "bias": r["bias"], "confidence": r["confidence"],
        "support": R.get("support"), "resistance": R.get("resistance"),
        "pos_pct": r.get("position_in_range"),
        "range_bars": R.get("bars"),
        "spring": (f"{best_sp['probability']} {best_sp['date']}"
                   if best_sp else ""),
        "upthrust": (f"{best_ut['kind']} {best_ut['probability']} {best_ut['date']}"
                     if best_ut else ""),
        "sos": len(r.get("sos") or []), "sow": len(r.get("sow") or []),
        "lps": bool(r.get("lps")), "lpsy": bool(r.get("lpsy")),
        "target_up": (r["laws"]["cause_effect"]["up_base"]
                      if r.get("laws") else None),
    }


def rank_key(s):
    """Sort so the actionable Wyckoff setups float to the top."""
    if s.get("error"):
        return -99
    k = 0.0
    k += {"A": 1, "B": 2, "C": 6, "D": 8, "E": 4}.get(s.get("phase"), 0)
    if str(s.get("structure", "")).lower().find("accumulation") >= 0:
        k += 3
    if s.get("spring"):
        k += 2 + (2 if s["spring"].startswith("High") else
                  1 if s["spring"].startswith("Medium") else 0)
    if s.get("lps"):
        k += 2
    k += min(s.get("sos") or 0, 3)
    k -= min(s.get("sow") or 0, 3)
    if s.get("upthrust"):
        k -= 3
    k += {"High": 2, "Medium": 1, "Low": 0}.get(s.get("confidence"), 0)
    return k
