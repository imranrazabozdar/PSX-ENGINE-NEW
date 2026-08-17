#!/usr/bin/env python3
"""
psx_verdict.py — v2.0 composite read.

psx_brain gives an indicator verdict. This adds the four things that were
missing and, crucially, makes them ARGUE with each other rather than averaging
them into mush:

    chart (psx_brain)  +  structure (psx_wyckoff)
                       +  market context (psx_context: regime, RS, shariah, fundamentals)
                       +  capital rules (psx_risk)

Agreement between the indicator read and the Wyckoff read is the signal worth
having. Disagreement is worth MORE than either alone, because it tells you
exactly which assumption to check, so disagreement is reported prominently
instead of being smoothed away.

The composite can only ever DOWNGRADE psx_brain's verdict, never upgrade it.
That asymmetry is deliberate: extra evidence should make you more selective,
not more confident.
"""

import psx_brain
import psx_context
import psx_report
import psx_risk
import psx_wyckoff

WEIGHTS = {"chart": 0.55, "structure": 0.30, "fundamentals": 0.15}


def _structure_score(w):
    """Map a Wyckoff read to 0-100 so it can sit beside the other sections."""
    if not w or not w.get("ok") or not w.get("range"):
        return None
    s = 50.0
    s += {"A": -5, "B": 0, "C": 8, "D": 18, "E": 6, "?": 0}.get(w.get("phase"), 0)
    st = (w.get("structure") or "").lower()
    if "re-accumulation" in st:
        s += 12
    elif "accumulation" in st:
        s += 14
    elif "re-distribution" in st:
        s -= 12
    elif "distribution" in st:
        s -= 14
    for sp in w.get("springs") or []:
        s += {"High": 10, "Medium": 5, "Low": 0}.get(sp.get("probability"), 0)
    for ut in w.get("upthrusts") or []:
        s -= {"High": 10, "Medium": 5, "Low": 0}.get(ut.get("probability"), 0)
    s += min(len(w.get("sos") or []), 3) * 3
    s -= min(len(w.get("sow") or []), 3) * 3
    if w.get("lps"):
        s += 6
    if w.get("lpsy"):
        s -= 6
    s += {"High": 4, "Medium": 0, "Low": -4}.get(w.get("confidence"), 0)
    return round(max(0.0, min(100.0, s)), 1)


def _agreement(brain_cls, w):
    """Do the chart and the structure tell the same story? Name it either way."""
    if not w or not w.get("range"):
        return "no structure", ("No trading range, so there is no Wyckoff opinion "
                                "to agree or disagree with. The chart read stands "
                                "alone.")
    chart_bull = brain_cls in ("buy", "trigger")
    wyck_bull = "ullish" in (w.get("bias") or "")
    wyck_bear = "earish" in (w.get("bias") or "")

    if chart_bull and wyck_bull:
        return "agree-bull", ("The indicators and the Wyckoff structure agree on "
                              "the upside. This is the only configuration where "
                              "both methods are pulling the same way — it is what "
                              "you are looking for.")
    if not chart_bull and wyck_bear:
        return "agree-bear", ("Indicators and structure agree on the downside. "
                              "There is nothing to do here except stay out.")
    if chart_bull and wyck_bear:
        return "conflict-chart-bull", (
            "CONFLICT: the indicators are bullish while the Wyckoff structure is "
            "distributive. Trend indicators lag; distribution happens while they "
            "still look fine. When these two disagree in this direction the "
            "structure has usually been right, so treat the indicator buy as "
            "unproven until the structure resolves.")
    if not chart_bull and wyck_bull:
        return "conflict-wyck-bull", (
            "CONFLICT: the Wyckoff structure is constructive but the indicators "
            "have not turned. That is the normal state of a Phase C or early "
            "Phase D setup — accumulation completes before the moving averages "
            "notice. This is an early-entry situation, which means smaller size "
            "and a stop that respects the range floor, not a full position.")
    return "mixed", ("The two reads point in different directions without either "
                     "being decisive. Nothing has to be done today.")


def analyse(symbol, daily=None, bench=None, years=3, partial="drop",
            sector_peers=None, regime=None, holdings=None, capital=None,
            with_wyckoff=True, weekly_wyckoff=False, memory=None):
    """One symbol, everything. Returns the psx_brain dict plus v2 sections."""
    if daily is None:
        daily = psx_report.load_from_psx(symbol, years)

    res = psx_brain.analyse(symbol, daily, bench, partial)

    # ---- structure ------------------------------------------------------
    wy = wywk = None
    if with_wyckoff:
        try:
            wy = psx_wyckoff.analyse(symbol, daily, "daily", bench)
        except Exception as e:
            wy = {"ok": False, "error": str(e)[:180]}
        if weekly_wyckoff:
            try:
                wywk = psx_wyckoff.analyse(symbol, psx_report.to_weekly(daily),
                                           "weekly", bench)
            except Exception:
                wywk = None

    # ---- context --------------------------------------------------------
    try:
        ctx = psx_context.for_symbol(symbol, daily, bench, sector_peers, regime)
    except Exception as e:
        ctx = {"regime": {"regime": "unknown", "gate": "off",
                          "note": f"context unavailable: {str(e)[:120]}"},
               "rs": None, "shariah": None, "fundamentals": None}

    # ---- capital rules --------------------------------------------------
    reg = ctx.get("regime") or {}
    try:
        rk = psx_risk.assess(symbol, daily, res["levels"],
                             regime=reg.get("regime"),
                             pct_above=reg.get("pct_above"),
                             holdings=holdings, capital=capital)
    except Exception as e:
        rk = {"risk_level": "unknown", "warnings": [f"risk layer failed: {str(e)[:120]}"],
              "vetoes": [], "sizing": None, "metrics": {}}

    # ---- compose --------------------------------------------------------
    chart_score = max(0.0, min(100.0, 50 + (res.get("score") or 0) * 2.2))
    struct_score = _structure_score(wy)
    fund = ctx.get("fundamentals") or {}
    fund_score = fund.get("score")

    parts = [(chart_score, WEIGHTS["chart"])]
    if struct_score is not None:
        parts.append((struct_score, WEIGHTS["structure"]))
    if fund_score is not None:
        parts.append((fund_score, WEIGHTS["fundamentals"]))
    wsum = sum(w for _, w in parts)
    composite = round(sum(v * w for v, w in parts) / wsum, 1)

    agree, agree_note = _agreement(res.get("class"), wy)

    # ---- verdict: downgrades only ---------------------------------------
    verdict = res["verdict"]
    trail = []
    for new, note in (psx_context.apply_regime_gate(verdict, reg.get("regime")),):
        if note:
            verdict, _ = new, trail.append(note)
    v2, note = psx_risk.apply_vetoes(verdict, rk.get("vetoes"))
    if note:
        verdict = v2
        trail.append(note)
    if agree == "conflict-chart-bull" and verdict in ("BUY", "BUY ON TRIGGER"):
        verdict = "WAIT"
        trail.append("Structure conflict downgraded the verdict to WAIT: the "
                     "indicators want to buy into what the Wyckoff read calls "
                     "distribution.")

    # ---- confidence -----------------------------------------------------
    conf = float(res.get("confidence") or 50)
    conf_notes = []
    if struct_score is not None:
        if agree in ("agree-bull", "agree-bear"):
            conf = min(95, conf + 6)
            conf_notes.append("both methods agree: +6")
        elif agree.startswith("conflict"):
            conf = max(10, conf - 12)
            conf_notes.append("methods conflict: -12")
    if (wy or {}).get("confidence") == "Low":
        conf = max(10, conf - 5)
        conf_notes.append("the Wyckoff read itself is low-confidence: -5")
    if fund.get("low_confidence"):
        conf = max(10, conf - 3)
        conf_notes.append("thin fundamental inputs: -3")
    if memory:
        try:
            adj, mnote = memory.confidence_adjustment(symbol)
            conf = max(10, min(95, conf + adj))
            conf_notes.append(mnote)
        except Exception:
            pass
    if verdict != res["verdict"]:
        conf = min(conf, 55)

    sh = ctx.get("shariah") or {}
    rs = ctx.get("rs") or {}

    # ---- the paragraph a human actually reads ---------------------------
    lines = [f"{symbol} at {res['price']} — {verdict} "
             f"(composite {composite}/100, confidence {int(conf)}/100)."]
    if composite >= 65 and verdict in ("WAIT", "AVOID"):
        lines.append("Note the gap between the two numbers: the composite scores "
                     "how good the SETUP is, the verdict says whether it is "
                     "TAKEABLE today. A high composite on a WAIT means keep this "
                     "on the list, not buy it.")
    lines.append(agree_note)
    if wy and wy.get("ok") and wy.get("range"):
        R = wy["range"]
        lines.append(f"Structure: {wy['structure']}, Phase {wy['phase']}, "
                     f"range {R['support']}-{R['resistance']} with price "
                     f"{wy['position_in_range']}% of the way up it.")
    if rs.get("note"):
        lines.append(rs["note"])
    if reg.get("note"):
        lines.append("Market: " + reg["note"])
    if fund_score is not None:
        lines.append(f"Fundamentals score {fund_score}/100"
                     + (" on thin inputs." if fund.get("low_confidence") else
                        f" against {fund.get('peers_used', 0)} sector peers."))
    if sh.get("status"):
        lines.append("Shariah: " + sh["status"] + ".")
    if rk.get("sizing"):
        s = rk["sizing"]
        lines.append(f"Sizing on Rs {s['capital']:,} of capital: {s['shares']:,} "
                     f"shares (Rs {s['position_pkr']:,}, {s['position_pct']}% of "
                     f"capital), losing Rs {s['max_loss_pkr']:,} "
                     f"({s['max_loss_pct']}%) if the stop fills.")
    lines.append(f"Risk level {rk.get('risk_level')}."
                 + (" Downgrades applied: " + " ".join(trail) if trail else ""))

    res.update({
        "v2": True,
        "verdict_v1": res["verdict"],
        "verdict": verdict,
        "confidence_v1": res.get("confidence"),
        "confidence": int(conf),
        "composite": composite,
        "scores": {"chart": round(chart_score, 1), "structure": struct_score,
                   "fundamentals": fund_score, "weights": WEIGHTS},
        "agreement": agree, "agreement_note": agree_note,
        "downgrades": trail, "confidence_notes": conf_notes,
        "wyckoff": wy, "wyckoff_weekly": wywk,
        "context": ctx, "risk": rk,
        "summary": " ".join(lines),
    })
    return res


def rank(results):
    """Rank composite results and explain the ordering, v2 style."""
    ok = [r for r in results if r and "error" not in r]
    ok.sort(key=lambda r: r.get("composite", 0), reverse=True)
    if not ok:
        return {"ranked": [], "commentary": "Nothing to rank."}

    L = [f"Ranked {len(ok)} names on the composite of chart, Wyckoff structure "
         f"and fundamentals.", ""]
    best = ok[0]
    L.append(f"TOP: {best['symbol']} — {best['verdict']}, composite "
             f"{best['composite']}, confidence {best['confidence']}.")
    L.append(f"  {best.get('agreement_note', '')}")

    both = [r for r in ok if r.get("agreement") == "agree-bull"]
    if both:
        L += ["", "CHART AND STRUCTURE BOTH BULLISH — the highest-quality group "
                  "on this list: " + ", ".join(r["symbol"] for r in both[:8])]

    early = [r for r in ok if r.get("agreement") == "conflict-wyck-bull"]
    if early:
        L += ["", "EARLY (Wyckoff constructive, indicators not yet turned) — "
                  "smaller size, stop under the range floor: "
              + ", ".join(r["symbol"] for r in early[:8])]

    traps = [r for r in ok if r.get("agreement") == "conflict-chart-bull"]
    if traps:
        L += ["", "INDICATOR BULL / STRUCTURE BEAR — the configuration that costs "
                  "money. Indicators are late to distribution: "
              + ", ".join(r["symbol"] for r in traps[:8])]

    springs = [(r["symbol"], sp) for r in ok for sp in (r.get("wyckoff") or {}).get("springs") or []
               if sp.get("probability") == "High"]
    if springs:
        L += ["", "HIGH-PROBABILITY SPRINGS: "
              + ", ".join(f"{s} ({sp['date']})" for s, sp in springs[:8])]

    phase_d = [r for r in ok if (r.get("wyckoff") or {}).get("phase") == "D"]
    if phase_d:
        L += ["", "PHASE D (cause built, markup starting): "
              + ", ".join(r["symbol"] for r in phase_d[:8])]

    vetoed = [r for r in ok if (r.get("risk") or {}).get("vetoes")]
    if vetoed:
        L += ["", "BLOCKED BY THE RISK LAYER: "
              + ", ".join(f"{r['symbol']} ({', '.join(r['risk']['vetoes'])})"
                          for r in vetoed[:8])]

    unver = [r for r in ok
             if ((r.get("context") or {}).get("shariah") or {}).get("compliant") is None]
    if unver:
        L += ["", f"{len(unver)} name(s) have no shariah verification on file. That "
                  "is an absence of evidence, not a negative finding."]

    reg = (ok[0].get("context") or {}).get("regime") or {}
    if reg.get("note"):
        L += ["", "MARKET REGIME: " + reg["note"]]

    return {"ranked": ok, "commentary": "\n".join(L)}
