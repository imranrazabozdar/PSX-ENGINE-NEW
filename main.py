"""main.py — Entry point and orchestrator.

Usage (Windows-friendly):
    python main.py run            # one full analysis run + report
    python main.py schedule       # auto-run every 10 min + 9AM/9PM reports
    python main.py morning        # print morning report
    python main.py evening        # print evening report
    python main.py backtest PSO   # technical backtest for one symbol (metrics)
    python main.py metrics        # whole-universe edge: expectancy/PF/maxDD/OOS
    python main.py portfolio      # book-level risk (heat + sector caps) from Buys
    python main.py accuracy       # signal & indicator accuracy stats
    python main.py history PSO    # recent stored runs for a symbol
"""

import sys
import io
import json
import logging
from datetime import datetime

# Force UTF-8 output on Windows consoles that default to cp1252
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import config
import ssl_compat
ssl_compat.enable()   # OS trust store for HTTPS (must precede any network call)
import database as db
import data_fetcher
import shariah_checker
import macro_news_analyzer
import sentiment_analyzer
import technical_analyzer
import fundamentals_analyzer
import market_regime
import scoring_engine
import risk_manager
import signal_generator
import portfolio_risk
import portfolio_advisor
import reports
import backtester
import news_feed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.FileHandler(config.LOG_PATH, encoding="utf-8"),
              logging.StreamHandler()])
log = logging.getLogger("main")


def _days_to_earnings(symbol):
    """Days until a KNOWN earnings/result date (config override, else the news
    feed's optional earnings_date). None when unknown — no blackout is invented."""
    ed = (getattr(config, "EARNINGS_DATES", {}) or {}).get(symbol)
    if not ed:
        av = news_feed.get(symbol)
        ed = av.get("earnings_date") if av else None
    if not ed:
        return None
    try:
        return (datetime.fromisoformat(str(ed)).date() - datetime.now().date()).days
    except Exception:
        return None


def analyze_stock(symbol, news_items, index_eod=None, regime=None,
                  holdings=None):
    """Full pipeline for one symbol. Returns the result dict and stores it."""
    shariah = shariah_checker.check(symbol)
    quote = data_fetcher.latest_quote(symbol)
    eod, eod_meta = data_fetcher.fetch_eod(symbol)

    rs = market_regime.relative_strength(eod, index_eod) if index_eod is not None else None
    rs_score = rs["rs_score"] if rs else None
    ohlc = db.get_daily_ohlc(symbol)          # real H/L bars → true ATR/ADX when ready
    technical = technical_analyzer.analyze(symbol, eod, quote, rs_score=rs_score, ohlc=ohlc)
    sentiment = sentiment_analyzer.analyze(symbol, news_items)
    macro = macro_news_analyzer.analyze(symbol, news_items)
    fundamentals = fundamentals_analyzer.analyze(symbol)
    tech_flags = technical.get("tech_flags")
    scoring = scoring_engine.compute(symbol, macro, sentiment, technical,
                                     fundamentals, tech_flags=tech_flags)
    risk = risk_manager.assess(symbol, technical, sentiment, macro,
                               regime=(regime or {}).get("regime"),
                               regime_pct_above=(regime or {}).get("pct_above"),
                               holdings=holdings)
    prev_sig = (db.last_run(symbol) or {}).get("signal")
    signal = signal_generator.generate(symbol, scoring["final_score"],
                                       scoring["confidence"], risk,
                                       shariah, technical,
                                       regime=(regime or {}).get("regime"),
                                       regime_pct_above=(regime or {}).get("pct_above"),
                                       prev_signal=prev_sig,
                                       days_to_earnings=_days_to_earnings(symbol))
    is_early, early_reason = signal_generator.early_watch(
        scoring["final_score"], technical, shariah)

    db.save_run({
        "run_time": datetime.now().isoformat(), "symbol": symbol,
        "price": technical.get("price"), "volume": technical.get("volume"),
        "technical_score": technical.get("score"),
        "sentiment_score": sentiment.get("score"),
        "macro_news_score": macro.get("score"),
        "final_score": scoring["final_score"], "signal": signal["signal"],
        "confidence": signal["confidence"],
        "stop_loss": technical.get("stop_loss"),
        "target1": technical.get("target1"), "target2": technical.get("target2"),
        "support": technical.get("support"),
        "resistance": technical.get("resistance"),
        "risk_level": risk["risk_level"], "shariah_status": shariah["status"],
        "data_quality": scoring["data_quality"],
        "relative_strength": rs_score,
        "market_regime": (regime or {}).get("regime"),
        "main_reason": "; ".join(signal["reasons"])[:400],
        "main_risk": (risk["warnings"][0] if risk["warnings"] else "")[:400],
        "tech_flags": json.dumps(tech_flags) if tech_flags else None,
        "confluence": signal.get("confluence", 0),
        "buy_zone_low": signal.get("buy_zone_low"),
        "buy_zone_high": signal.get("buy_zone_high"),
        "accumulation_candidate": int(bool(technical.get("accumulation_candidate"))),
        "accumulation_reasons": json.dumps(technical.get("accumulation_reasons") or []),
        "cmf": technical.get("cmf"),
        "obv_divergence_bullish": (int(technical["obv_divergence_bullish"])
                                   if technical.get("obv_divergence_bullish") is not None
                                   else None),
        "early_watch": int(is_early),
        "early_reason": early_reason or None,
    })

    if quote.get("warning"):
        log.warning("%s: %s", symbol, quote["warning"])
    if eod_meta.get("warning"):
        log.warning("%s: %s", symbol, eod_meta["warning"])

    return {"symbol": symbol, "shariah": shariah, "quote": quote,
            "technical": technical, "sentiment": sentiment, "macro": macro,
            "fundamentals": fundamentals, "relative_strength": rs,
            "scoring": scoring, "risk": risk, "signal": signal}


def full_run():
    log.info("=== Engine run started ===")
    db.init_db()
    news_items = data_fetcher.fetch_news()
    # Per-company public news (Google News RSS) -> real per-stock sentiment.
    for s in config.STOCKS:
        news_items += data_fetcher.fetch_company_news(s)
    backtester.update_outcomes()          # learning loop first

    # Tier 2: fetch the benchmark index ONCE; judge the market regime. Both feed
    # relative strength (per stock) and the regime gate (market-wide).
    index_eod, index_meta = market_regime.fetch_index()
    regime = market_regime.assess_regime(index_eod)
    log.info("Market regime: %s", regime["note"])

    # Real book (portfolio.json) so the concentration cap can see what is already
    # held — per-trade sizing alone is blind to it. Missing/unreadable file = no
    # holdings = the cap simply never fires (never a fabricated position).
    holdings = portfolio_advisor.load_portfolio().get("holdings", [])

    results = [analyze_stock(s, news_items, index_eod, regime, holdings)
               for s in config.STOCKS]

    # Tier 2 #9: book-level risk across every Buy this run (heat + sector caps).
    candidates = [{"symbol": r["symbol"],
                   "score": r["scoring"]["final_score"],
                   "signal": r["signal"]["signal"],
                   "price": r["technical"].get("price"),
                   "stop": r["technical"].get("stop_loss"),
                   "sector": config.SECTORS.get(r["symbol"], "Unknown")}
                  for r in results
                  if r["signal"]["signal"] in ("Buy", "Strong Buy")]
    portfolio = portfolio_risk.assess(candidates)
    log.info("Portfolio risk: %s", portfolio_risk.summary_line(portfolio))

    macro_titles = [n["title"] for n in news_items][:6]
    market_notes = "Market regime: " + regime["note"]
    if macro_titles:
        market_notes += " | Headlines: " + " | ".join(t[:80] for t in macro_titles)
    report = reports.build_run_report(results, market_notes, portfolio)
    print("\n" + report)
    reports.save_report(report, "run")

    # Excel export + email (email only fires per config.EMAIL_MODE; both are
    # no-ops if their prerequisites/secrets are absent, never fatal).
    try:
        import excel_export
        import notify
        xlsx = excel_export.export(results)
        notify.send_report(results, report, xlsx)
    except Exception as e:
        log.warning("Excel/email step failed: %s", e)

    log.info("=== Engine run finished ===")
    return results


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    db.init_db()
    if cmd == "run":
        full_run()
    elif cmd == "schedule":
        import scheduler
        scheduler.start()
    elif cmd == "morning":
        text = reports.morning_report()
        print(text); reports.save_report(text, "morning")
    elif cmd == "evening":
        backtester.update_outcomes()
        text = reports.evening_report()
        print(text); reports.save_report(text, "evening")
        try:
            import notify
            notify.send_text(f"PSX Evening Summary {datetime.now():%Y-%m-%d}", text)
        except Exception as e:
            log.warning("Evening email step failed: %s", e)
    elif cmd == "backtest":
        sym = sys.argv[2].upper() if len(sys.argv) > 2 else "PSO"
        res = backtester.backtest(sym)
        res.pop("detail", None)            # keep the console summary readable
        for v in ("in_sample", "out_of_sample"):
            res.get(v, {}).pop("equity_curve", None)
        res.pop("equity_curve", None)
        import json; print(json.dumps(res, indent=2, default=str))
    elif cmd == "metrics":
        # Whole-universe backtest with profit metrics (expectancy / profit
        # factor / max drawdown) + out-of-sample verdict per symbol.
        res = backtester.backtest_portfolio()
        agg = res["aggregate"]
        print(f"\n=== Strategy edge across {res['symbols_traded']} symbols "
              f"({agg.get('trades', 0)} trades) ===")
        print(f"Expectancy/trade: {agg.get('expectancy_pct')}%  |  "
              f"Profit factor: {agg.get('profit_factor')}  |  "
              f"Win rate: {agg.get('win_rate_pct')}%  |  "
              f"Max drawdown: {agg.get('max_drawdown_pct')}%  |  "
              f"Total return: {agg.get('total_return_pct')}%")
        print("\nPer symbol:")
        for s, m in sorted(res["per_symbol"].items(),
                           key=lambda kv: (kv[1].get("expectancy_pct") or 0),
                           reverse=True):
            print(f"  {s:<7} trades={m['trades']:<3} "
                  f"exp={m['expectancy_pct']}%  pf={m['profit_factor']}  "
                  f"win={m['win_rate_pct']}%  maxDD={m['max_drawdown_pct']}%")
        print("\n" + res["warning"])
    elif cmd == "portfolio":
        # Book-level risk from the latest stored Buys: heat + sector caps.
        cap = int(sys.argv[2]) if len(sys.argv) > 2 else 1_000_000
        cands = []
        for s in config.STOCKS:
            r = db.last_run(s)
            if r and r["signal"] in ("Buy", "Strong Buy"):
                cands.append({"symbol": s, "score": r["final_score"],
                              "signal": r["signal"], "price": r["price"],
                              "stop": r["stop_loss"],
                              "sector": config.SECTORS.get(s, "Unknown")})
        res = portfolio_risk.assess(cands, capital=cap)
        print(f"\n=== Portfolio risk (capital PKR {cap:,}) ===")
        print(portfolio_risk.summary_line(res))
        print("\nAdmitted:")
        for a in res["admitted"]:
            print(f"  {a['symbol']:<7} {a['shares']:>6,} sh  "
                  f"PKR {a['value']:>12,.0f}  heat {a['heat_pct']:.2f}%  "
                  f"[{a['sector']}]")
        if res["deferred"]:
            print("Deferred (cap would be breached):")
            for d in res["deferred"]:
                print(f"  {d['symbol']:<7} — {d['reason']}")
    elif cmd == "fundamentals":
        import fundamentals_fetcher
        p = fundamentals_fetcher.fetch_all()
        n = len(p["data"]); fields = sum(len(v) for v in p["data"].values())
        print(f"Fundamentals refreshed: {n}/{len(config.STOCKS)} stocks, "
              f"{fields} ratios, as_of {p['as_of']}")
    elif cmd == "accuracy":
        rows = db.signal_accuracy_summary()
        print("\n=== Signal accuracy (with sample-size reliability) ===")
        for r in rows:
            wr = "n/a" if r["win_rate_pct"] is None else f"{r['win_rate_pct']}%"
            print(f"  {r['signal']:<11} n={r['n_total']:<4} win={wr:<7} "
                  f"sample={r['n_confidence']}")
        low = [r for r in rows if r["n_confidence"] == "low"]
        if low:
            print("\n⚠ Low-sample signals (read these win rates as NOISE, not "
                  "edge — too few graded trades to trust):")
            for r in low:
                print(f"   - {r['signal']}: only {r['n_total']} graded outcome(s)")
        print("\nIndicator accuracy:", db.indicator_stats())
    elif cmd == "regrade":
        res = backtester.regrade_all()
        print(f"Re-graded {res['regraded']} runs; {res['flipped']} outcomes changed.")
        print("Signal accuracy now:", db.signal_accuracy())
    elif cmd == "accumulating":
        rows = db.accumulating_now(lookback=10, min_streak=1)
        if not rows:
            print("No stocks currently flagged as accumulation candidates.")
        else:
            print(f"\n=== Accumulation candidates ({len(rows)}) ===")
            for r in rows:
                reasons = json.loads(r["reasons"] or "[]")
                print(f"  {r['symbol']:<7}  signal={r['signal']:<11} "
                      f"score={r['final_score']}  price={r['price']}  — "
                      + "; ".join(reasons))
    elif cmd == "history":
        sym = sys.argv[2].upper() if len(sys.argv) > 2 else "PSO"
        for r in db.run_history(sym, 20):
            print(f"{r['run_time'][:16]} {r['symbol']} score={r['final_score']} "
                  f"signal={r['signal']} conf={r['confidence']}% "
                  f"outcome={r['outcome']}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
