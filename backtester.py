"""backtester.py — Two jobs:

1. OUTCOME TRACKER (the learning loop): for every stored run, fill in real
   prices after the next run, 1, 3, and 7 days, then judge whether the
   signal 'worked'. Buy/Strong Buy works if price moved toward target
   before stop; Avoid/Exit works if price fell. Results feed
   indicator_accuracy and signal_accuracy, which adjust future CONFIDENCE.

2. BACKTEST MODE: replay the EOD history with the technical module to show
   how score-based signals would have behaved. This is in-sample and
   explicitly labelled with overfitting warnings — it is evidence, not proof.
"""

import json
import logging
from datetime import datetime, timedelta

import pandas as pd

import config
import database as db
import data_fetcher
import technical_analyzer

log = logging.getLogger("backtester")


# ---------------------------------------------------------------------------
# Benchmark cache — real KMI30 EOD as the "market" for grading Avoid/Exit. Far
# more honest than the cohort-median proxy (which is the engine's own filtered
# universe), but the cohort median stays as a fallback when the index isn't
# fetchable (offline / 403). Cached once per process; never fabricated.
# ---------------------------------------------------------------------------
_BENCH_CACHE = {"eod": None, "tried": False}


def _benchmark_eod():
    if not _BENCH_CACHE["tried"]:
        try:
            import market_regime
            eod, _ = market_regime.fetch_index()
            _BENCH_CACHE["eod"] = eod
        except Exception as e:
            log.warning("Benchmark index fetch failed for grading: %s", e)
        _BENCH_CACHE["tried"] = True
    return _BENCH_CACHE["eod"]


def _benchmark_forward_move(start_date, days=3):
    """Real benchmark (KMI30) % move from start_date over `days` calendar days.
    None when the index data isn't reachable or doesn't span the window."""
    eod = _benchmark_eod()
    if eod is None or len(eod) == 0:
        return None
    eod_sorted = eod.sort_values("date")
    start = pd.Timestamp(start_date).normalize()
    sub0 = eod_sorted[eod_sorted["date"] >= start]
    if sub0.empty:
        return None
    p0 = float(sub0["close"].iloc[0])
    sub1 = eod_sorted[eod_sorted["date"] >= start + pd.Timedelta(days=days)]
    if sub1.empty:
        return None
    p1 = float(sub1["close"].iloc[0])
    return (p1 / p0 - 1) * 100


# ---------------------------------------------------------------------------
# Outcome tracking
# ---------------------------------------------------------------------------
def _price_on_or_after(eod, when):
    # EOD timestamps sit at midnight; run_time carries an intraday time. Without
    # normalising, "3 days after a 15:17 run" skips that day's midnight EOD and
    # lands a session late. Compare on calendar date so the horizon is exact.
    sub = eod[eod["date"] >= pd.Timestamp(when).normalize()]
    return float(sub["close"].iloc[0]) if len(sub) else None


def update_outcomes():
    """Fill pending forward prices from real EOD data and grade signals."""
    pend = db.pending_outcomes()
    if not pend:
        return 0
    eod_cache = {}
    updated = 0
    for run in pend:
        sym = run["symbol"]
        if sym not in eod_cache:
            eod_cache[sym], _ = data_fetcher.fetch_eod(sym)
        eod = eod_cache[sym]
        if eod is None:
            continue
        t0 = datetime.fromisoformat(run["run_time"])
        for field, days in (("price_1d", 1), ("price_3d", 3), ("price_7d", 7)):
            if run[field] is None and datetime.now() >= t0 + timedelta(days=days):
                p = _price_on_or_after(eod, t0 + timedelta(days=days))
                if p:
                    db.update_outcome(run["id"], field, p)
                    run[field] = p
                    updated += 1
        # grade once 3-day price exists
        if run["outcome"] is None and run["price_3d"] is not None and run["price"]:
            _grade_and_attribute(run)
        # 7-day grade: a LEAD signal (early watch) needs room to play out, so it
        # is judged on the longer horizon. Stored separately — it never touches
        # `outcome`, the 3-day grade the Buy/Avoid stats are built on.
        if run["price_7d"] is not None and run["price"] and \
                _col(run, "outcome_7d") is None:
            db.update_outcome(run["id"], "outcome_7d",
                              "worked" if _beat_market_7d(run) else "failed")
    log.info("Outcome tracker updated %d fields", updated)
    return updated


def _col(run, name):
    """sqlite3.Row has no .get(); a column added by a later migration may be
    absent in an older row object."""
    try:
        return run[name]
    except (IndexError, KeyError):
        return None


def _beat_market_7d(run):
    """Did this name beat the market over 7 days? Same honest benchmark chain as
    the 3-day grade (real KMI30 -> cohort median -> 0). Used for the early-watch
    tier, whose whole point is lead time: 3 days is too short to judge it."""
    chg = (run["price_7d"] / run["price"] - 1) * 100
    market = _benchmark_forward_move(run["run_time"][:10], days=7)
    if market is None:
        market = db.cohort_forward_move(run["run_time"][:10],
                                        exclude_symbol=run["symbol"], days=7)
    return chg > (market if market is not None else 0.0)


def _signal_worked(run):
    """Did the signal call play out? Real, rule-based — no fabrication.

      * Buy/Strong Buy — BEAT the real benchmark (KMI30) 3-day forward move
                         without breaching the stop. Was an absolute ">1% in 3
                         days", which mostly measured the market: it scored 22%
                         against a 38% base rate for "any symbol rose >1%", so a
                         Buy could "fail" in a down market while still being the
                         right relative call (and vice versa). Same benchmark →
                         cohort-median → "did not fall" fallback chain as
                         Avoid/Exit, so Buy and Avoid are finally comparable.
      * Avoid/Exit     — RELATIVE to the REAL benchmark (KMI30) forward move:
                         the stock underperformed the actual market index. Falls
                         back to the cohort median (engine's own universe) when
                         the index isn't reachable, then to "did not rise" when
                         even the cohort is too thin to benchmark. Three honest
                         fallbacks — never fabricated.
      * Watch/Hold     — graded loosely: didn't lose more than 3%.
    """
    chg = (run["price_3d"] / run["price"] - 1) * 100
    sig = run["signal"]
    if sig in ("Buy", "Strong Buy", "Avoid", "Exit"):
        market = _benchmark_forward_move(run["run_time"][:10], days=3)
        if market is None:
            market = db.cohort_forward_move(run["run_time"][:10],
                                            exclude_symbol=run["symbol"])
        bar = market if market is not None else 0.0
        if sig in ("Avoid", "Exit"):
            return chg < bar
        # A Buy is right when it OUTPERFORMS the market and the stop held. The
        # stop condition stays absolute: a stopped-out trade is a real loss no
        # matter what the index did.
        return chg > bar and (run["stop_loss"] is None
                              or run["price_3d"] > run["stop_loss"])
    return chg > -3.0


def _grade_and_attribute(run):
    """Grade one run and credit/blame the sections + sub-indicators that drove it."""
    worked = _signal_worked(run)
    sym = run["symbol"]
    db.update_outcome(run["id"], "outcome", "worked" if worked else "failed")
    # credit/blame the dominant section (section-level)
    b = {"technical": run["technical_score"],
         "sentiment": run["sentiment_score"],
         "macro_news": run["macro_news_score"]}
    dominant = max(b, key=lambda k: b[k] or 0)
    db.bump_indicator(dominant, sym, worked)
    # sub-indicator attribution: each bullish flag earns a hit or miss
    # so scoring_engine can later boost/penalise per-indicator confidence.
    if run.get("tech_flags"):
        try:
            for ind, bullish in json.loads(run["tech_flags"]).items():
                if bullish is True:
                    db.bump_indicator(f"tech_{ind}", sym, worked)
        except Exception:
            pass
    return worked


def regrade_all():
    """One-time / maintenance: re-grade EVERY completed run under the current
    rules and rebuild indicator_accuracy from scratch. Needed whenever the
    grading logic changes (e.g. Avoid moving from absolute to relative), since
    update_outcomes only ever grades rows whose outcome is still NULL."""
    db.reset_indicator_accuracy()
    runs = db.gradeable_runs()
    flipped = 0
    for run in runs:
        before = run["outcome"]
        worked = _grade_and_attribute(run)
        if before is not None and before != ("worked" if worked else "failed"):
            flipped += 1
    log.info("Re-graded %d runs (%d outcomes changed)", len(runs), flipped)
    return {"regraded": len(runs), "flipped": flipped}


# ---------------------------------------------------------------------------
# Historical backtest (technical-only replay; macro/sentiment history is not
# reconstructable honestly, so we do not pretend to backtest it)
#
# Tier 2 #8 — the replay now reports the metrics that actually predict whether
# an edge is real and tradeable: expectancy, profit factor, max drawdown, plus
# an out-of-sample tail + rolling walk-forward folds to flag overfitting.
# ---------------------------------------------------------------------------
def _generate_trades(symbol, eod, hold_days, entry_score, start_i=60):
    """Replay the window bar-by-bar. At each bar the technical module sees ONLY
    data up to that bar (no lookahead); a qualifying setup opens a trade held
    `hold_days` bars or until the close breaches the stop. Returns trade dicts
    carrying their bar index `i` so they can be split for walk-forward."""
    trades = []
    i, n = start_i, len(eod)
    while i < n - hold_days:
        window = eod.iloc[: i + 1]
        quote = {"price": float(window["close"].iloc[-1]),
                 "volume": float(window["volume"].iloc[-1])}
        t = technical_analyzer.analyze(symbol, window, quote)
        if t["score"] is not None and t["score"] >= entry_score and not t.get("breakdown"):
            entry = quote["price"]
            exit_p = float(eod["close"].iloc[i + hold_days])
            stopped = any(float(eod["close"].iloc[j]) <= t["stop_loss"]
                          for j in range(i + 1, i + hold_days + 1))
            pnl = ((t["stop_loss"] if stopped else exit_p) / entry - 1) * 100
            trades.append({"i": i, "date": str(eod['date'].iloc[i].date()),
                           "entry": round(entry, 2), "pnl_pct": round(pnl, 2),
                           "stopped": stopped})
            i += hold_days
        i += 1
    return trades


def trade_metrics(trades):
    """Profit-oriented metrics from a list of trades (each with `pnl_pct`).

      * expectancy_pct  — average % you can expect PER TRADE (win%·avgWin −
                          loss%·avgLoss). The single most important number: a
                          positive expectancy is the whole game.
      * profit_factor   — gross profit / gross loss. >1.5 healthy, <1 = losing.
      * max_drawdown_pct— worst peak-to-trough dip of a compounded equity curve
                          (1 unit risked per trade). Governs survivability.
      * payoff_ratio    — avg win / avg loss size.
    """
    pnls = [t["pnl_pct"] for t in trades]
    n = len(pnls)
    if n == 0:
        return {"trades": 0}
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = len(wins) / n
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0      # <= 0
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    gross_profit = sum(wins)
    gross_loss = -sum(losses)                                    # positive magnitude
    if gross_loss > 0:
        profit_factor = round(gross_profit / gross_loss, 2)
    else:
        profit_factor = None if gross_profit == 0 else float("inf")
    payoff = round(avg_win / -avg_loss, 2) if avg_loss < 0 else None
    # compounded equity curve -> max drawdown + total return
    equity, peak, max_dd = 1.0, 1.0, 0.0
    curve = []
    for p in pnls:
        equity *= (1 + p / 100)
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1)
        curve.append(round(equity, 4))
    return {"trades": n,
            "win_rate_pct": round(win_rate * 100, 1),
            "avg_win_pct": round(avg_win, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "expectancy_pct": round(expectancy, 2),
            "profit_factor": profit_factor,
            "payoff_ratio": payoff,
            "max_drawdown_pct": round(max_dd * 100, 1),
            "total_return_pct": round((equity - 1) * 100, 1),
            "equity_curve": curve}


def _walk_forward(trades, n_bars, folds):
    """Split the window into `folds` sequential time slices and report metrics
    per slice. A real edge holds up across folds; one that only works in a single
    slice is a red flag for overfitting/luck."""
    if folds < 2 or not trades:
        return []
    step = n_bars / folds
    out = []
    for k in range(folds):
        lo, hi = int(k * step), int((k + 1) * step)
        ft = [t for t in trades if lo <= t["i"] < hi]
        m = trade_metrics(ft)
        out.append({"fold": k + 1, "trades": m["trades"],
                    "expectancy_pct": m.get("expectancy_pct"),
                    "profit_factor": m.get("profit_factor"),
                    "win_rate_pct": m.get("win_rate_pct")})
    return out


def _verdict(oos, walk):
    """Plain-English read on whether the edge survives out-of-sample."""
    if not oos or oos.get("trades", 0) < 3:
        return "Inconclusive — too few out-of-sample trades to judge."
    pf = oos.get("profit_factor")
    pf_ok = pf is not None and (pf == float("inf") or pf >= 1.2)
    profitable_folds = sum(1 for f in walk
                           if (f.get("expectancy_pct") or 0) > 0)
    if oos.get("expectancy_pct", 0) > 0 and pf_ok:
        if walk and profitable_folds >= max(2, len(walk) - 1):
            return "Edge HOLDS out-of-sample and is consistent across folds."
        return "Edge holds out-of-sample, but consistency across folds is mixed."
    return ("Edge does NOT hold out-of-sample — likely in-sample overfit; "
            "treat as evidence against, not for.")


def backtest(symbol, lookback=None, hold_days=None, entry_score=None,
             oos_fraction=None, folds=None):
    cfg = config.BACKTEST
    lookback = lookback or cfg["lookback"]
    hold_days = hold_days or cfg["hold_days"]
    entry_score = cfg["entry_score"] if entry_score is None else entry_score
    oos_fraction = cfg["oos_fraction"] if oos_fraction is None else oos_fraction
    folds = folds or cfg["walk_forward_folds"]

    eod, meta = data_fetcher.fetch_eod(symbol)
    if eod is None or len(eod) < 80:
        return {"symbol": symbol, "error": "insufficient history", "meta": meta}
    eod = eod.tail(lookback).reset_index(drop=True)
    n = len(eod)
    trades = _generate_trades(symbol, eod, hold_days, entry_score)
    if not trades:
        return {"symbol": symbol, "trades": 0,
                "note": "No qualifying setups in window."}

    # split by ENTRY bar: the in-sample head vs the held-out out-of-sample tail
    split_i = int(n * (1 - oos_fraction))
    is_trades = [t for t in trades if t["i"] < split_i]
    oos_trades = [t for t in trades if t["i"] >= split_i]

    full = trade_metrics(trades)
    oos = trade_metrics(oos_trades)
    walk = _walk_forward(trades, n, folds)
    detail = [{k: t[k] for k in ("date", "entry", "pnl_pct", "stopped")}
              for t in trades]
    return {"symbol": symbol,
            "window": {"bars": n, "from": str(eod["date"].iloc[0].date()),
                       "to": str(eod["date"].iloc[-1].date())},
            "params": {"hold_days": hold_days, "entry_score": entry_score,
                       "oos_fraction": oos_fraction},
            **full,
            "in_sample": trade_metrics(is_trades),
            "out_of_sample": oos,
            "walk_forward": walk,
            "verdict": _verdict(oos, walk),
            "detail": detail,
            "warning": ("Technical-only replay on a SMALL sample; stops use "
                        "close (no intraday H/L). Evidence, not proof — past "
                        "performance ≠ future results.")}


def backtest_portfolio(symbols=None, **kw):
    """Run the backtest across the whole universe and aggregate into one book of
    trades, so you see the strategy's portfolio-wide expectancy, profit factor
    and max drawdown — not just one symbol at a time."""
    symbols = symbols or config.STOCKS
    per_symbol, all_trades, errors = {}, [], []
    for s in symbols:
        r = backtest(s, **kw)
        if r.get("error"):
            errors.append(s)
            continue
        if not r.get("trades"):
            continue
        per_symbol[s] = {k: r.get(k) for k in
                         ("trades", "win_rate_pct", "expectancy_pct",
                          "profit_factor", "max_drawdown_pct",
                          "total_return_pct")}
        per_symbol[s]["verdict"] = r.get("verdict")
        all_trades += [{"symbol": s, **t} for t in r["detail"]]
    # combined equity curve in chronological order across all names
    all_trades.sort(key=lambda t: t["date"])
    agg = trade_metrics(all_trades)
    return {"symbols_traded": len(per_symbol), "errors": errors,
            "aggregate": agg, "per_symbol": per_symbol,
            "warning": ("Aggregate of independent per-symbol replays; assumes "
                        "you could have taken every signal. Evidence, not proof.")}
