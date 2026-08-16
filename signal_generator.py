"""signal_generator.py — Converts final score + risk assessment into one of:
Strong Buy / Buy / Watch / Hold / Avoid / Exit.

PURE-TECHNICAL MODE (config.PURE_TECHNICAL, 2026-08-12): decisions come from
price/volume only. News- and sentiment-derived vetoes (bad_news,
manipulation_risk) are still WARNED about but no longer downgrade a signal —
risk_manager stops emitting them as vetoes, so the branches below go quiet.
The chase guard (config.CHASE_GUARD_ENABLED) is OFF and the pullback/extension
reference EMA is config.PULLBACK_EMA_SPAN (50, was 20): both deliberate risk-up
choices by the user.

Overrides ALWAYS beat the score:
  * shariah issue            -> Exit (if held) / Avoid
  * technical breakdown      -> Exit / Avoid
  * poor risk/reward         -> downgrade Buy to Watch
  * bad news / manipulation risk -> downgrade Buy to Watch (OFF in pure-technical)

Tier 2 additions:
  * Confluence gate  — Strong Buy needs ≥3/4 independent dimensions (trend,
                       momentum, volume, structure); Buy needs ≥2. Below → Watch.
  * Strong Buy confirmation — capped at Buy on its FIRST appearance; the next
                              run still scoring Strong Buy confirms it. (No
                              numeric streak/conviction count is tracked.)

Anti-chase additions (don't buy at the peak):
  * Overextension gate — price too far above the reference EMA (or parabolic
                         momentum) steps the signal down one notch. DISABLED by
                         config.CHASE_GUARD_ENABLED = False; still reported.
  * Thin-headroom (poor_rr) — REAL room-to-resistance:risk below the minimum
                         (price jammed under a ceiling) → Watch. (Via risk_manager,
                         which now reads technical.headroom_rr, not the ≈2.0 proj R:R.)
  * Pullback entry  — an extended setup held at Watch shows its buy-zone (the
                       50-EMA band); when price later retraces INTO that zone with
                       the uptrend intact, a cooled Watch/Hold is upgraded to Buy
                       (buy the dip, don't chase the peak). Stateless across runs.
  * Earnings blackout — within EARNINGS_BLACKOUT_DAYS of a KNOWN result date, a
                       fresh Buy/Strong Buy is held at Watch (binary event risk).
                       Only acts when a date is known; never invents a blackout.
"""

import logging
import config
import database as db

log = logging.getLogger("signal")

T = config.SIGNAL_THRESHOLDS


def _confluence(technical):
    """0-4: how many independent signal dimensions agree with a bullish trade.

    Four INDEPENDENT dimensions (each captures a different market mechanism):
      1. Trend    — price above its 50-EMA (intermediate trend is up)
      2. Momentum — RSI in healthy zone AND MACD histogram positive (both agree)
      3. Volume   — OBV trending up (smart money accumulating, not distributing)
      4. Structure— price above nearest support AND no breakdown in progress

    A score of 4 means trend, momentum, volume and structure all line up.
    A Buy signal with confluence 1 is a weak coincidence; with 4 it is a real setup.
    """
    price = technical.get("price") or 0
    score, dims = 0, []

    ema50 = technical.get("ema50")
    if ema50 and price > ema50:
        score += 1; dims.append("trend")

    rsi = technical.get("rsi")
    macd_h = technical.get("macd_hist")
    if (rsi is not None and 40 <= rsi <= 74
            and macd_h is not None and macd_h > 0):
        score += 1; dims.append("momentum")

    if technical.get("obv_up"):
        score += 1; dims.append("volume")

    support = technical.get("support")
    if support and price > support and not technical.get("breakdown"):
        score += 1; dims.append("structure")

    return score, dims


def early_watch(final_score, technical, shariah):
    """Is this name BUILDING — worth watching before it reaches the Buy band?

    Returns (bool, reason). Deliberately narrow: only CMF (real high/low money
    flow) showed forward edge in the graded history; the OBV-based accumulation
    heuristics measured NEGATIVE and are excluded (see config.EARLY_WATCH_*).

    This never returns a tradeable signal — it is a monitoring tier that buys
    lead time. Structure must be intact (no breakdown, above support) so it
    cannot flag a falling knife.
    """
    if not config.EARLY_WATCH_ENABLED or not shariah["eligible_for_ranking"]:
        return False, ""
    cmf = technical.get("cmf")
    lo, hi = config.EARLY_WATCH_SCORE_BAND
    price, support = technical.get("price"), technical.get("support")
    rs = technical.get("relative_strength")
    if (cmf is None or cmf <= config.EARLY_WATCH_MIN_CMF
            or final_score is None or not (lo <= final_score < hi)
            or technical.get("breakdown")
            or not (price and support and price > support)
            or (rs is not None and rs < config.EARLY_WATCH_MIN_RS)):
        return False, ""
    return True, (f"EARLY: money flow building (CMF {cmf:+.2f}) while the score "
                  f"is still {final_score:.0f} — below the Buy band. Structure "
                  "intact. Watch for the score to confirm; NOT a buy signal, and "
                  "not yet validated out-of-sample (graded on the 7-day horizon).")


def generate(symbol, final_score, confidence, risk, shariah, technical,
             regime=None, prev_signal=None, days_to_earnings=None,
             regime_pct_above=None):
    """Generate a trading signal.

    prev_signal: the most recent stored signal — used by the Strong Buy
    confirmation gate (a fresh Strong Buy is held at Buy until the NEXT run
    still scores Strong Buy). No streak/conviction count is tracked or shown:
    it proved misleading (15-min polling let raw run-counts look like many
    independent confirmations when they were really one session)."""
    reasons, override = [], None

    # No usable price this run -> not analysable. Emit an explicit "No data"
    # signal so a fetch failure can never masquerade as a Hold/Watch with a
    # bogus 0.00 price/stop/target sitting in the ranking.
    price = technical.get("price")
    if not price or price <= 0:
        return {"signal": "No data",
                "reasons": ["No usable price for this symbol this run — "
                            "excluded from ranking until the feed returns."],
                "confidence": 0, "confluence": 0, "confluence_dims": []}

    if not shariah["eligible_for_ranking"]:
        override = "Avoid"
        reasons.append("Shariah status unverified — excluded by policy")

    if "breakdown" in risk["vetoes"]:
        prev = db.last_run(symbol)
        override = "Exit" if prev and prev.get("signal") in \
            ("Buy", "Strong Buy", "Hold") else "Avoid"
        reasons.append("Technical breakdown below support")

    if override:
        return {"signal": override, "reasons": reasons,
                "confidence": min(confidence, 60),
                "confluence": 0, "confluence_dims": []}

    # ---- score-based base signal
    if final_score >= T["strong_buy"]:
        base = "Strong Buy"
        reasons.append(f"Final score {final_score} ≥ {T['strong_buy']} with "
                       f"technical {technical['classification']}")
        if technical["classification"] not in ("Strong bullish", "Bullish"):
            base = "Buy"
            reasons.append("Downgraded: score high but technicals not confirming")
    elif final_score >= T["buy"]:
        base = "Buy"; reasons.append(f"Score {final_score} in Buy band 70-80")
    elif final_score >= T["watch"]:
        base = "Watch"; reasons.append(f"Score {final_score} in Watch band 60-70")
    elif final_score >= T["hold"]:
        base = "Hold"; reasons.append(f"Score {final_score} in Hold band 50-60")
    else:
        base = "Avoid"; reasons.append(f"Score {final_score} below 50")

    # ---- Hysteresis dead-band: a raw score grazing a band edge (e.g. 70.3 one
    # run, 69.7 the next) shouldn't flip the signal — that's scoring noise, not
    # a real change. Require crossing the threshold by HYSTERESIS_BAND points
    # before changing direction. Only acts on one-notch transitions; multi-notch
    # moves and hard vetoes (breakdown/shariah) bypass it. Applied BEFORE the
    # confirmation/confluence/chase gates so those still operate normally on top.
    _band = getattr(config, "HYSTERESIS_BAND", 0)
    if _band > 0 and prev_signal in ("Strong Buy", "Buy", "Watch", "Hold", "Avoid"):
        _RANK = {"Strong Buy": 4, "Buy": 3, "Watch": 2, "Hold": 1, "Avoid": 0}
        _pr, _br = _RANK.get(prev_signal), _RANK.get(base)
        _thr = {"Strong Buy": T["strong_buy"], "Buy": T["buy"],
                "Watch": T["watch"], "Hold": T["hold"]}
        if _pr is not None and _br is not None and abs(_pr - _br) == 1:
            if _pr > _br:
                # one-notch DOWNGRADE. The dead-band sits ENTIRELY ABOVE the
                # threshold (enter at _t+band, exit at _t) rather than straddling
                # it (2026-08-12). Straddling let a stale Buy persist down to
                # _t-band: with the threshold raised to 75, scores of 73-74 —
                # exactly the 30%-win band the raise was meant to exclude — were
                # still being emitted as Buy. Anti-flap is preserved by the
                # upgrade side below, which still requires clearing _t+band.
                _t = _thr.get(prev_signal)
                if _t is not None and final_score >= _t:
                    base = prev_signal
                    reasons.append(
                        f"Hysteresis: score {final_score} still at/above the "
                        f"{prev_signal} threshold ({_t}) — held at {prev_signal}")
            else:
                # one-notch UPGRADE — require clearing the new threshold by the
                # band, not just grazing it (avoids flapping the other way)
                _t = _thr.get(base)
                if _t is not None and final_score < _t + _band:
                    base = prev_signal
                    reasons.append(
                        f"Hysteresis: score {final_score} only just clears the "
                        f"{base} threshold ({_t}) — held at {prev_signal} until "
                        f"it breaks {_t + _band}+")

    # ---- Tier 2: confirmation gate (before confluence so we check intent, not result)
    # A new Strong Buy on its first appearance is held at Buy — the market has
    # to CONFIRM it on the next run. This prevents chasing a one-run spike.
    if base == "Strong Buy" and prev_signal != "Strong Buy":
        base = "Buy"
        reasons.append("Downgraded Strong Buy→Buy: first run at this level — "
                       "needs one more consecutive confirmation")

    # ---- Confluence: MEASURED and reported, but no longer a gate (2026-08-12).
    # The four dimensions were assumed independent; graded outcomes say they are
    # not. Day-deduped Buy win rate by confluence: 2/4 won 17% (n=24), 3/4 won
    # 26% (n=47), 4/4 won 25% (n=108) — flat. The gate was rejecting setups
    # without buying accuracy (trend and structure are near-collinear: price
    # above its 50-EMA is usually also above support). Still stored and shown
    # as context so the dimensions remain visible per card.
    confluence, conf_dims = _confluence(technical)

    # ---- Overextension (chase) guard: don't buy a stretched, parabolic move at
    # the peak — that's where profit-takers hand you the bag. Far above EMA20 or
    # very high 20-day momentum steps the signal down one notch and tells the user
    # to wait for the pullback the profit-taking creates. (The "thin room to
    # resistance" case is handled by the poor_rr veto in the soft downgrades.)
    _zlo, _zhi = technical.get("buy_zone_low"), technical.get("buy_zone_high")
    _span = technical.get("buy_zone_ema_span", config.PULLBACK_EMA_SPAN)
    _zone = (f" Buy-zone PKR {_zlo}–{_zhi} (pullback to {_span}-EMA)."
             if _zlo and _zhi else "")
    # Regime-aware chase guard. In a broad rally most names sit well above their
    # 20-EMA — treating that as "extended" would downgrade the whole leadership
    # group to Watch and make the engine miss the move. So in a confirmed risk-on
    # regime the chase thresholds widen (×extension_riskon_multiplier); only a
    # genuinely parabolic move is still stepped down. In neutral/risk-off the
    # guard stays tight. (technical['extended'] keeps its strict definition for the
    # buy-zone/accumulation logic; only the SIGNAL action adapts here.)
    _ext_pct = technical.get("ext_pct")
    _mom = technical.get("momentum_20d")
    # The widening scales with RALLY STRENGTH: ramp the multiplier linearly from
    # 1.0 (index just crossed above its 50-EMA — a shaky breakout, loosen barely)
    # up to the configured ceiling (index _full_pct above its EMA — a strong,
    # confirmed bull, loosen fully). A mild rally relaxes the guard a little; a
    # powerful one relaxes it a lot.
    _mult = 1.0
    if regime == "risk-on":
        _ceil = config.RISK.get("extension_riskon_multiplier", 1.0)
        _full = config.RISK.get("extension_riskon_full_pct", 8.0) or 8.0
        _strength = 1.0 if regime_pct_above is None else \
            max(0.0, min(1.0, regime_pct_above / _full))
        _mult = 1.0 + (_ceil - 1.0) * _strength
    _ext_lim = config.RISK["max_extension_pct"] * _mult
    _mom_lim = config.RISK["max_extension_momentum_pct"] * _mult
    _chase = ((_ext_pct is not None and _ext_pct > _ext_lim)
              or (_mom is not None and _mom > _mom_lim))
    _relaxed = (f" (chase guard ×{_mult:.2f} for risk-on rally)"
                if _mult > 1.0 else "")
    if _chase and not config.CHASE_GUARD_ENABLED:
        # Guard off (2026-08-12): note the stretch, don't act on it.
        _mom_txt = f", 20d momentum {_mom:+.1f}%" if _mom is not None else ""
        reasons.append(
            f"Note (chase guard OFF): extended {_ext_pct}% above EMA{_span}"
            f"{_mom_txt} — buying strength, accept the higher "
            f"drawdown risk.{_zone}")
    elif _chase:
        if base == "Strong Buy":
            base = "Buy"; reasons.append(
                f"Downgraded Strong Buy→Buy: extended {_ext_pct}% "
                f"above EMA{_span}{_relaxed} — chase risk, a pullback entry is safer.{_zone}")
        elif base == "Buy":
            base = "Watch"; reasons.append(
                f"Downgraded Buy→Watch: price extended above EMA{_span} (chase risk{_relaxed}) "
                f"— wait for a pullback before acting.{_zone}")

    # ---- soft downgrades (earnings, regime, concentration, rr, confidence, RS)
    # `bad_news` / `manipulation_risk` branches remain for when PURE_TECHNICAL is
    # turned off; risk_manager stops emitting those vetoes while it is on.
    # The old `risk_level == "High"` branch was REMOVED (2026-08-12): any veto
    # forces risk_level High, and every veto already has its own branch above it,
    # so the branch could never fire (confirmed: all 203 graded Buys were "Low").
    _earnings_soon = (days_to_earnings is not None
                      and 0 <= days_to_earnings <= config.EARNINGS_BLACKOUT_DAYS)
    if base in ("Strong Buy", "Buy"):
        if _earnings_soon:
            base = "Watch"; reasons.append(
                f"Downgraded: earnings/result due in ~{days_to_earnings}d — binary "
                "event risk, don't open a fresh position into the announcement")
        elif config.REGIME_GATE_ENABLED and regime == "risk-off":
            base = "Watch"; reasons.append(
                f"Downgraded: market regime risk-off ({config.BENCHMARK_INDEX} below "
                f"its {config.REGIME_EMA_SPAN}-EMA) — don't buy into a falling market")
        elif "concentrated" in risk["vetoes"]:
            base = "Watch"; reasons.append(
                "Downgraded: this name is already over the single-name cap in your "
                "book — adding compounds concentration risk. Trim or hold, don't add")
        elif "poor_rr" in risk["vetoes"]:
            base = "Watch"; reasons.append("Downgraded: risk/reward below minimum")
        elif "manipulation_risk" in risk["vetoes"]:
            base = "Watch"; reasons.append("Downgraded: hype/pump risk — verify first")
        elif "bad_news" in risk["vetoes"]:
            base = "Watch"; reasons.append("Downgraded: material negative news — "
                                           "verify the headline before acting")
        elif confidence < 45:
            base = "Watch"; reasons.append("Downgraded: confidence below 45% "
                                           "(weak data or poor history)")
        elif (technical.get("cmf") is not None
              and technical["cmf"] <= config.BUY_MIN_CMF):
            base = "Watch"; reasons.append(
                f"Downgraded: money flow not confirming (CMF {technical['cmf']:+.2f} "
                f"≤ {config.BUY_MIN_CMF:.2f}) — price is rising without real "
                "buying pressure behind it (graded history: CMF-negative Buys "
                "beat the market 61% vs 83% when flow confirms, and carried the "
                "worse drawdowns)")
        elif (technical.get("relative_strength") is not None
              and technical["relative_strength"] < config.RS_LAGGARD_VETO):
            base = "Watch"; reasons.append(
                f"Downgraded: relative strength {technical['relative_strength']:.0f} "
                f"< {config.RS_LAGGARD_VETO} — market laggard (graded history: "
                "RS<55 Buys won 21%, RS 70+ won 36%); buy leaders, not laggards")

    # The pullback-entry UPGRADE was removed 2026-08-12: the Buys it produced
    # (final_score below the Buy band) won 9% (n=57) against a 38% market base
    # rate. technical['pullback_ready'] and the buy-zone are still computed and
    # shown as manual context — the engine just no longer acts on them.

    if base in ("Strong Buy", "Buy"):
        reasons.append("Manual confirmation REQUIRED before placing any order")

    return {"signal": base, "reasons": reasons, "confidence": confidence,
            "confluence": confluence, "confluence_dims": conf_dims,
            "buy_zone_low": technical.get("buy_zone_low"),
            "buy_zone_high": technical.get("buy_zone_high")}
