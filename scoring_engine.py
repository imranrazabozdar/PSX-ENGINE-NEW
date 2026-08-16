"""scoring_engine.py — Blends the section scores into a final 0-100 score and
a confidence percentage.

Weights are configurable in config.WEIGHTS (currently technical 0.70 /
fundamentals 0.30; news + sentiment are 0-weight). Learning adjusts
CONFIDENCE, not weights: if a stock's past signals keep failing, confidence
drops; if they keep working, it rises modestly. Small samples are flagged as
overfitting risk and barely move confidence. The weak-section confidence
penalty is weight-aware, so a minor section can't dominate confidence.
"""

import logging

import config
import database as db

log = logging.getLogger("scoring")


def _indicator_accuracy_boost(symbol, tech_flags):
    """±confidence from per-sub-indicator track records on this specific symbol.

    When a sub-indicator (RSI, MACD, trend etc.) has been bullish at signal time
    ≥5 times and its win rate deviates from 50%, it earns a credit or debit that
    modulates confidence. Averaged across applicable indicators; capped at ±8 pts.
    This is SEPARATE from the overall win-rate adjustment in
    historical_confidence_adjust — that adjusts for how the WHOLE signal did;
    this adjusts for which INDICATORS are actually predicting outcomes here.
    """
    if not tech_flags:
        return 0.0, ""
    all_stats = {r["indicator"]: r for r in db.indicator_stats(symbol)}
    boosts, parts = [], []
    for ind, bullish in tech_flags.items():
        if bullish is not True:   # only grade indicators that were bullish
            continue
        s = all_stats.get(f"tech_{ind}")
        if not s:
            continue
        total = (s["hits"] or 0) + (s["misses"] or 0)
        if total < 5:             # too few samples — don't overfit
            continue
        wr = s["hits"] / total
        boosts.append((wr - 0.5) * 10)   # wr=100% → +5, wr=0% → -5
        parts.append(f"{ind} {wr:.0%}")
    if not boosts:
        return 0.0, ""
    avg = sum(boosts) / len(boosts)
    capped = round(max(-8.0, min(8.0, avg)), 1)
    return capped, "Indicator track records: " + ", ".join(parts)


# Only signals graded under STRICT rules count toward confidence. Watch/Hold
# are graded on the loose "didn't lose >3%" rule (their 80-90% 'win' rates are
# survival, not edge) — counting them pushed nearly every symbol to the +15
# confidence cap, so confidence stopped discriminating (failed Buys averaged
# 71% confidence in the 2026-07-15 audit).
_STRICT_SIGNALS = ("Buy", "Strong Buy", "Avoid", "Exit")


def historical_confidence_adjust(symbol):
    """Return (adjustment in percentage points, note)."""
    rows = [r for r in db.signal_accuracy(symbol) if r["signal"] in _STRICT_SIGNALS]
    wins = sum(r["n"] for r in rows if r["outcome"] == "worked")
    losses = sum(r["n"] for r in rows if r["outcome"] == "failed")
    total = wins + losses
    if total == 0:
        return 0.0, "No strictly-graded (Buy/Avoid) history yet — base confidence."
    win_rate = wins / total
    if total < 10:
        # tiny sample: cap influence, warn about overfitting
        adj = (win_rate - 0.5) * 8
        return round(adj, 1), (f"Only {total} completed signals — small sample, "
                               "OVERFITTING RISK; history given little weight.")
    adj = (win_rate - 0.5) * 30          # max ±15 points
    return round(max(-15, min(15, adj)), 1), \
        f"History: {wins}W/{losses}L (win rate {win_rate:.0%}) over {total} " \
        "strictly-graded Buy/Avoid signals."


def compute(symbol, macro, sentiment, technical, fundamentals=None, tech_flags=None):
    w = config.WEIGHTS
    fund = fundamentals or {"score": 50.0, "low_confidence": True}
    final = round(w["macro_news"] * macro["score"]
                  + w["sentiment"] * sentiment["score"]
                  + w["technical"] * technical["score"]
                  + w.get("fundamentals", 0) * fund["score"], 1)

    # ---- data quality. Only sections that ACTUALLY drive the score count here.
    # News/sentiment are 0-weight, so their (usually low) confidence must not
    # paint every stock "weak" — that was making 28/30 stocks look low-quality
    # when the weighted sections (technical, fundamentals) were fine.
    weak = []
    if macro.get("low_confidence") and w.get("macro_news", 0) > 0: weak.append("macro/news")
    if sentiment.get("low_confidence") and w.get("sentiment", 0) > 0: weak.append("sentiment")
    if technical.get("low_confidence") and w.get("technical", 0) > 0: weak.append("technical")
    if fund.get("low_confidence") and w.get("fundamentals", 0) > 0: weak.append("fundamentals")
    data_quality = "good" if not weak else ("weak: " + ", ".join(weak))

    # ---- confidence
    confidence = 70.0
    # Weight-aware penalty: a weak section dents confidence in proportion to how
    # much it actually drives the score. With the technical-first weights, a
    # quiet/empty news section barely matters, while weak technicals (the core)
    # matter most. (Old behaviour was a flat 12 pts per weak section.)
    _key = {"macro/news": "macro_news", "sentiment": "sentiment",
            "technical": "technical", "fundamentals": "fundamentals"}
    confidence -= sum(36 * w.get(_key.get(s, s), 0) for s in weak)
    # agreement bonus: all WEIGHTED sections pointing the same way. 0-weight
    # sections (news/sentiment) are excluded so their noise can't deny the bonus.
    _sec = {"technical": technical["score"], "fundamentals": fund["score"],
            "macro_news": macro["score"], "sentiment": sentiment["score"]}
    scores = [v for k, v in _sec.items() if w.get(k, 0) > 0]
    if scores and max(scores) - min(scores) < 15:
        confidence += 8
    adj, hist_note = historical_confidence_adjust(symbol)
    ind_boost, ind_note = _indicator_accuracy_boost(symbol, tech_flags or {})
    confidence = round(max(10, min(95, confidence + adj + ind_boost)), 1)
    history_note = hist_note + (f" | {ind_note}" if ind_note else "")

    return {"final_score": final, "confidence": confidence,
            "data_quality": data_quality, "weak_sections": weak,
            "history_note": history_note,
            "breakdown": {"macro_news": macro["score"],
                          "sentiment": sentiment["score"],
                          "technical": technical["score"],
                          "fundamentals": fund["score"],
                          "weights": w}}
