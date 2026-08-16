"""market_regime.py — Tier 2 market context, driven by the benchmark index
(config.BENCHMARK_INDEX) fetched from the same public PSX DPS endpoint as stocks.

Two jobs:
  1. REGIME GATE — is the market risk-on? The index must be above its own EMA
     (config.REGIME_EMA_SPAN). Buying individual stocks while the index is below
     its 50-EMA is a top way to lose money, so signal_generator softens Buys to
     Watch when the regime is risk-off.
  2. RELATIVE STRENGTH — is a stock OUTPERFORMING the index over 1/3/6 months?
     Profit comes from owning leaders, not laggards, so RS feeds the technical
     score and the ranking.

Honest by design: if the index can't be fetched, regime is "unknown" (the gate
fails OPEN — we never block trades on missing data) and RS is skipped (no
fabricated number).
"""

import logging

import config
import data_fetcher

log = logging.getLogger("market_regime")


def fetch_index():
    """Return (DataFrame[date, open, close, volume], meta) for the benchmark."""
    return data_fetcher.fetch_eod(config.BENCHMARK_INDEX)


def assess_regime(index_eod=None):
    """Risk-on / risk-off from the index vs its EMA. Returns a dict; regime is
    'unknown' (gate disabled) when the index is missing or too short."""
    if index_eod is None:
        index_eod, _ = fetch_index()
    if index_eod is None or len(index_eod) < config.REGIME_EMA_SPAN:
        return {"regime": "unknown", "index": config.BENCHMARK_INDEX,
                "level": None, "ema": None, "pct_above": None,
                "note": f"{config.BENCHMARK_INDEX} unavailable — regime gate off this run."}
    close = index_eod["close"].astype(float)
    ema = close.ewm(span=config.REGIME_EMA_SPAN, adjust=False).mean()
    level, ema_last = float(close.iloc[-1]), float(ema.iloc[-1])
    pct_above = (level / ema_last - 1) * 100
    regime = "risk-on" if level >= ema_last else "risk-off"
    note = (f"{config.BENCHMARK_INDEX} {level:,.0f} is {abs(pct_above):.1f}% "
            f"{'above' if regime == 'risk-on' else 'below'} its "
            f"{config.REGIME_EMA_SPAN}-EMA ({ema_last:,.0f}) -> {regime}.")
    return {"regime": regime, "index": config.BENCHMARK_INDEX, "level": level,
            "ema": ema_last, "pct_above": round(pct_above, 2), "note": note}


def _ret(series, window):
    if len(series) <= window:
        return None
    return float(series.iloc[-1]) / float(series.iloc[-1 - window]) - 1


def relative_strength(stock_eod, index_eod=None):
    """Stock return minus index return over the configured windows, blended to a
    0-100 RS score (50 = tracks the index; >50 = outperforming). None if data is
    insufficient — never fabricated."""
    if index_eod is None:
        index_eod, _ = fetch_index()
    if stock_eod is None or index_eod is None:
        return None
    sc, ic = stock_eod["close"].astype(float), index_eod["close"].astype(float)
    rels, num, used_w = {}, 0.0, 0.0
    for name, w in config.RS_LOOKBACKS.items():
        sr, ir = _ret(sc, w), _ret(ic, w)
        if sr is None or ir is None:
            continue
        rel = sr - ir
        rels[name] = round(rel * 100, 1)
        wt = config.RS_WEIGHTS.get(name, 0)
        num += wt * rel
        used_w += wt
    if used_w == 0:
        return None
    blended = num / used_w                          # weighted avg outperformance
    rs_score = max(0.0, min(100.0, 50 + blended * 200))   # ±25% blend -> 0/100
    return {"rs_score": round(rs_score, 1), "outperforming": blended > 0,
            "rel": rels, "blended_pct": round(blended * 100, 1)}
