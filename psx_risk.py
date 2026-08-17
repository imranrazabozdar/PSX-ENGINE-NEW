#!/usr/bin/env python3
"""
psx_risk.py — the capital-protection layer your engine did not have.

psx_brain already produces a stop and a size_pct. That is per-trade sizing in
percentage terms. Two things were missing:

  1. A VETO layer that can downgrade a good-looking setup for reasons that have
     nothing to do with the chart: it is illiquid, it is too volatile to hold,
     there is no room to the next ceiling, or you already own too much of it.
  2. BOOK-LEVEL risk. Per-trade sizing caps the damage from one position. It is
     blind to the fact that eight "safe" 1.5% trades in three cement names all
     gap down on the same morning. This module sizes every candidate, then
     admits them best-first until total heat, sector exposure or a position
     count binds — and reports what it deferred and why, never silently drops.

Nothing here promises no loss. It labels each setup Low/Medium/High risk and
sizes so that a stop that actually fills costs a known, small fraction.
"""

import numpy as np
import pandas as pd

RISK = {
    "capital": 1_000_000,           # your working capital in PKR — override per call
    "max_risk_per_trade_pct": 1.5,  # loss if the stop fills, as % of capital
    "max_position_pct": 15.0,       # never more than this % of capital in one name
    "max_concentration_pct": 25.0,  # above this weight in the CURRENT book, no adds
    "min_rr": 1.5,                  # room-to-target : risk below this = thin upside
    "min_rr_riskon_floor": 1.1,     # relaxed floor in a confirmed risk-on tape
    "rr_riskon_full_pct": 8.0,      # index % above EMA at which the floor is reached
    "min_avg_value_pkr": 2_000_000, # 20-day average traded VALUE floor
    "max_atr_pct": 6.0,             # daily range above this = high volatility
    "max_extension_pct": 11.0,      # % above the 20-EMA that counts as chasing
    "extension_riskon_mult": 1.8,   # chase guard widens by up to this in a rally
}

BOOK = {
    "max_heat_pct": 6.0,            # total capital at risk if EVERY stop fills
    "max_sector_pct": 30.0,         # capital deployed into any one sector
    "max_positions": 8,
}


def _ramp(regime, pct_above, base, floor, full_pct):
    """Scale a threshold with rally strength. Neutral/risk-off keeps `base`."""
    if regime != "risk-on" or floor >= base:
        return base
    strength = 1.0 if pct_above is None else float(np.clip(pct_above / (full_pct or 8), 0, 1))
    return base - (base - floor) * strength


def _concentration(symbol, price, holdings):
    """This name's share of the current book. Other positions at their cost."""
    if not holdings or not price:
        return None
    total = this = 0.0
    for h in holdings:
        q = float(h.get("qty") or 0)
        if q <= 0:
            continue
        if str(h.get("symbol", "")).upper() == symbol.upper():
            v = q * price
            this += v
        else:
            v = q * float(h.get("avg_cost") or 0)
        total += v
    if total <= 0 or this <= 0:
        return None
    return round(this / total * 100, 1)


def assess(symbol, daily, levels, regime=None, pct_above=None,
           holdings=None, capital=None, cfg=None):
    """Per-trade risk verdict.

    levels: the dict psx_brain.analyse puts in result['levels'].
    Returns risk_level, warnings, vetoes and a concrete share count.
    """
    C = dict(RISK)
    C.update(cfg or {})
    cap = float(capital or C["capital"])
    warn, veto = [], []

    price = float(daily.close.iloc[-1])
    stop = float(levels.get("stop") or 0)
    t2 = float(levels.get("t2") or 0)

    # --- liquidity: the constraint that actually decides your size on PSX
    val20 = float((daily.close * daily.volume).rolling(20).mean().iloc[-1])
    if val20 < C["min_avg_value_pkr"]:
        warn.append(f"ILLIQUID: about Rs {val20/1e6:.2f}M traded per day against a "
                    f"Rs {C['min_avg_value_pkr']/1e6:.1f}M floor. Your own order "
                    f"would move this price. Size to what you could sell in one "
                    f"session, not to what the model suggests.")
        veto.append("illiquid")

    # --- volatility
    tr = pd.concat([daily.high - daily.low,
                    (daily.high - daily.close.shift()).abs(),
                    (daily.low - daily.close.shift()).abs()], axis=1).max(axis=1)
    atr_pct = float((tr.rolling(14).mean().iloc[-1] / price) * 100)
    if atr_pct > C["max_atr_pct"]:
        warn.append(f"HIGH VOLATILITY: the average daily range is {atr_pct:.1f}%. "
                    f"A 2-ATR stop is {2*atr_pct:.1f}% away, so a normal position "
                    f"here carries an abnormal loss if it goes wrong.")

    # --- room to the target (headroom R:R), regime-aware
    rr = (t2 - price) / (price - stop) if price > stop and t2 > price else 0.0
    rr_min = _ramp(regime, pct_above, C["min_rr"], C["min_rr_riskon_floor"],
                   C["rr_riskon_full_pct"])
    if rr < rr_min:
        eased = (f" (eased from {C['min_rr']} because the tape is risk-on)"
                 if rr_min < C["min_rr"] else "")
        warn.append(f"THIN UPSIDE: room-to-target against risk is {rr:.2f} versus "
                    f"a {rr_min:.2f} minimum{eased}. Price is jammed under a "
                    f"ceiling — you are paying full risk for partial reward.")
        veto.append("poor_rr")

    # --- chasing: how far above the 20-EMA, widened in a confirmed rally
    ema20 = float(daily.close.ewm(span=20, adjust=False).mean().iloc[-1])
    ext = (price / ema20 - 1) * 100
    ext_cap = C["max_extension_pct"] * _ramp(
        regime, pct_above, 1.0, C["extension_riskon_mult"], C["rr_riskon_full_pct"]
    ) if regime == "risk-on" else C["max_extension_pct"]
    if ext > ext_cap:
        warn.append(f"EXTENDED: {ext:.1f}% above the 20-EMA against a "
                    f"{ext_cap:.1f}% guard. Entering here means your stop has to "
                    f"sit under a move that has already happened.")
        veto.append("extended")

    # --- concentration against what you already hold
    conc = _concentration(symbol, price, holdings)
    if conc is not None and conc > C["max_concentration_pct"]:
        warn.append(f"CONCENTRATED: {symbol} is already {conc}% of your book "
                    f"against a {C['max_concentration_pct']}% cap. Holding or "
                    f"trimming is fine; adding is what this blocks.")
        veto.append("concentrated")

    # --- the rules that always apply
    warn.append(f"Rule: no leverage, never all-in, at most "
                f"{C['max_position_pct']}% of capital in one name.")
    warn.append("Rule: this is decision support. Confirm manually before any order.")

    hard = sum(1 for w in warn if w.split(":")[0].isupper() and ":" in w)
    risk_level = "High" if (veto or hard >= 2) else "Medium" if hard == 1 else "Low"

    # --- concrete sizing
    sizing = None
    if price > stop > 0:
        rps = price - stop
        max_loss = cap * C["max_risk_per_trade_pct"] / 100
        shares = int(max_loss / rps)
        shares = max(0, min(shares, int(cap * C["max_position_pct"] / 100 / price)))
        sizing = {
            "capital": round(cap),
            "shares": shares,
            "position_pkr": round(shares * price),
            "position_pct": round(shares * price / cap * 100, 2),
            "risk_per_share": round(rps, 2),
            "max_loss_pkr": round(shares * rps),
            "max_loss_pct": round(shares * rps / cap * 100, 2),
        }
    else:
        warn.append("Cannot size a position: the stop is not below the price, so "
                    "there is no defined risk to divide capital by.")

    return {"symbol": symbol, "risk_level": risk_level, "warnings": warn,
            "vetoes": veto, "sizing": sizing,
            "metrics": {"avg_value_pkr": round(val20), "atr_pct": round(atr_pct, 2),
                        "headroom_rr": round(rr, 2), "rr_min": round(rr_min, 2),
                        "extension_pct": round(ext, 1), "ext_cap": round(ext_cap, 1),
                        "concentration_pct": conc}}


def apply_vetoes(verdict, vetoes):
    """Downgrade rather than delete: the analysis stays visible, the buy does not."""
    if not vetoes:
        return verdict, None
    if verdict in ("BUY", "BUY ON TRIGGER"):
        return "WAIT", ("Risk layer downgraded " + verdict + " to WAIT: "
                        + ", ".join(vetoes) + ". The chart may be right; the "
                        "trade is not takeable as sized.")
    return verdict, None


# --------------------------------------------------------------------------
# book level
# --------------------------------------------------------------------------

def book(candidates, capital=None, cfg=None):
    """Admit candidates best-first until a cap binds.

    candidates: [{symbol, score, verdict, price, stop, sector}, ...]
    Returns admitted / deferred / unsizable and a summary of the resulting book.
    """
    C = dict(BOOK)
    C.update(cfg or {})
    cap = float(capital or RISK["capital"])
    max_heat = cap * C["max_heat_pct"] / 100
    max_sector = cap * C["max_sector_pct"] / 100
    EPS = 1e-6

    ranked = sorted(candidates, key=lambda c: (c.get("score") or 0), reverse=True)
    admitted, deferred, unsizable = [], [], []
    heat = deployed = 0.0
    sector_val = {}

    for c in ranked:
        sym = str(c.get("symbol", "")).upper()
        sec = c.get("sector") or "Unknown"
        price, stop = c.get("price"), c.get("stop")
        if not price or not stop or price <= stop:
            unsizable.append({**c, "sector": sec,
                              "reason": "no stop below the price, so the position "
                                        "cannot be risk-sized"})
            continue
        rps = price - stop
        shares = int(cap * RISK["max_risk_per_trade_pct"] / 100 / rps)
        shares = min(shares, int(cap * RISK["max_position_pct"] / 100 / price))
        if shares <= 0:
            unsizable.append({**c, "sector": sec,
                              "reason": "risk per share is too large for even one "
                                        "share inside the per-trade cap"})
            continue

        value, risk = shares * price, shares * rps
        why = []
        if len(admitted) >= C["max_positions"]:
            why.append(f"{C['max_positions']}-position cap already reached")
        if heat + risk > max_heat + EPS:
            why.append(f"would push total book heat past {C['max_heat_pct']:.0f}% "
                       f"of capital")
        if sector_val.get(sec, 0.0) + value > max_sector + EPS:
            why.append(f"would push {sec} exposure past the "
                       f"{C['max_sector_pct']:.0f}% sector cap")

        row = {"symbol": sym, "score": c.get("score"), "verdict": c.get("verdict"),
               "sector": sec, "price": price, "stop": stop, "shares": shares,
               "value_pkr": round(value), "risk_pkr": round(risk),
               "heat_pct": round(risk / cap * 100, 2),
               "weight_pct": round(value / cap * 100, 2)}
        if why:
            row["reason"] = "; ".join(why)
            deferred.append(row)
        else:
            admitted.append(row)
            heat += risk
            deployed += value
            sector_val[sec] = sector_val.get(sec, 0.0) + value

    sectors = {s: {"value_pkr": round(v), "pct": round(v / cap * 100, 2)}
               for s, v in sorted(sector_val.items(), key=lambda kv: -kv[1])}
    top = next(iter(sectors.items()), None)
    summary = {
        "capital": round(cap),
        "positions": len(admitted), "max_positions": C["max_positions"],
        "heat_pkr": round(heat), "heat_pct": round(heat / cap * 100, 2),
        "max_heat_pct": C["max_heat_pct"],
        "heat_room_pct": round(C["max_heat_pct"] - heat / cap * 100, 2),
        "deployed_pkr": round(deployed),
        "deployed_pct": round(deployed / cap * 100, 2),
        "cash_pct": round(100 - deployed / cap * 100, 2),
        "sectors": sectors, "max_sector_pct": C["max_sector_pct"],
        "deferred": len(deferred), "unsizable": len(unsizable),
    }
    summary["text"] = (
        f"{len(admitted)} position(s) admitted. If every stop filled on the same "
        f"morning you would lose Rs {heat:,.0f} — {heat/cap*100:.1f}% of capital "
        f"against a {C['max_heat_pct']:.0f}% ceiling. "
        f"{deployed/cap*100:.0f}% deployed, {100-deployed/cap*100:.0f}% in cash"
        + (f", heaviest in {top[0]} at {top[1]['pct']:.0f}%" if top else "")
        + f". {len(deferred)} deferred by a cap, {len(unsizable)} unsizable.")
    return {"admitted": admitted, "deferred": deferred,
            "unsizable": unsizable, "book": summary}
