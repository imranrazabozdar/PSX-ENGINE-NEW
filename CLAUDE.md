# PSX Shariah Engine — Working Memory

A Shariah-compliant PSX (Pakistan Stock Exchange) equity analysis engine. KMI-30
focus, with a broader KMI All-Share universe.

## Hard rules (NEVER violate)
- **Never fabricate data.** Shariah status, news, earnings dates, prices, OHLC,
  benchmark moves — all sourced from real data or explicitly labelled
  unavailable. Missing data stays NULL, never a synthesized value.
- **No protection bypass.** Public PSX DPS endpoints + public RSS only.
- **No backwards-compat shims, no dead code, minimal comments.** Comment only
  the non-obvious WHY.
- **Manual confirmation required before any trade** — this is decision support,
  not auto-trading.

## News: auto-fetched, UNSCORED window (2026-07-15)

News carries **0% score weight**. The `news.yml` workflow now runs on a
**weekday-morning cron** (09:05 / 09:35 / 11:05 PKT, staggered) — no manual
prompt needed — and commits `news_raw_24h.json`. The dashboard reads that raw
file via `news_feed.raw_headlines(sym)` and shows the last-24h headlines per
stock as an **unscored window for manual cross-verification** (Watchlist "Full
detail" expander + Stock detail tab). `news_feed.raw_headlines` filters to
credible desks by publisher NAME (`config.NEWS_DISPLAY_PUBLISHERS`) because the
fetch-time host allowlist is bypassed by Google News redirect links (every link
is `news.google.com`, so off-desk publishers leak into the raw file). The
LLM-judged `news_signals.json` routine below is now OPTIONAL — only relevant if
news weights are ever restored.

**Relevance-anchor gate (2026-07-15):** Google News RSS token-matches the
company query loosely, so a *"National Foods expands in UAE"* headline matched
NRL's *"National Refinery"* query and got attributed to (and GLM-rated as) NRL
— a cross-company mis-attribution. `config.COMPANY_NEWS_ANCHORS` now holds a
distinctive name phrase per symbol and `config.headline_matches_company()`
(word-boundary matched, flexible whitespace) gates every headline. Applied at
BOTH fetch time (`news_fetcher.fetch_for_symbol`) AND read time
(`news_feed.raw_headlines`, which GLM consumes) so already-committed raw files
are cleaned without a re-fetch. Ambiguous bare tickers are omitted on purpose
(NRL is also National Rugby League). Trade-off: a few ticker-only legit
headlines are missed (conservative) — correct for an UNWEIGHTED feed where
mis-attribution is the real harm. Result: raw feed went from ~209
loosely-matched items to ~8 correctly-attributed ones.

**All 5 previously-unnamed symbols now have curated anchors (2026-08-13):**
SLM `service long march`, SLGL `secure logistics`, THCCL `thatta cement`,
GHNI `ghandhara industries`, GAL `ghandhara automobiles`.
- SLGL's registered name is HYPHENATED ("Secure Logistics-Trax Group Ltd.") and
  `headline_matches_company` joins tokens with `\s+`, so a 3-word
  "secure logistics trax" anchor would NEVER match the real name. Two words
  match both the hyphenated and spaced forms.
- GHNI/GAL use TWO-word anchors because both companies share the surname
  "Ghandhara": a bare "Ghandhara" headline deliberately matches NEITHER rather
  than both. Verified across reversed pairs and an all-caps form.

**Open discrepancy — do not fix as a side effect:** `SECTORS` still maps GHNI to
"Glass/Holding" and GAL to "Textile/Synthetic Fibre", but both are automotive
assemblers. Those labels drive the sector-exposure cap in `portfolio_risk`, so
re-bucketing two symbols changes book-level risk limits and needs a deliberate
decision.

## GLM second opinion (2026-07-15, unweighted)

`news_glm.py` runs after the news fetch in `news.yml` (needs `GLM_API_KEY`
secret set in the repo — ZhipuAI free tier, `glm-4.5-flash`). One batched
request rates every symbol that has fresh credible headlines as
`highly_positive | positive | neutral | negative | highly_negative` and writes
`news_glm_ratings.json`. `news_feed.glm_rating(sym)` reads it. The dashboard
shows a `🤖 GLM` pill next to `📰` on each actionable-card, plus the GLM
reason, so the user can cross-check whether the LLM agrees with the engine's
Buy/Avoid. **Zero score weight** — informational only. Missing key / stale
file → pill shows `GLM: —` and nothing else changes.

**Timeout fix (2026-07-15):** `open.bigmodel.cn` is a mainland-China endpoint;
from GitHub's US runners the batched call regularly overran the old 60s read
timeout and `news_glm_ratings.json` never got written (the step is wrapped in
`|| true`, so it looked "green" while silently failing — check the step LOG,
not just its conclusion). `GLM_TIMEOUT` is now 120s (env-overridable) with ONE
retry on `Timeout`/`ConnectionError` only — an HTTP error like a 401 from a bad
key still fails fast, never retried. Confirmed live: run wrote 19 ratings.
Diagnosing key issues: a bad key = instant ~1s **401**; a slow-endpoint problem
= ~60s+ **read timeout**. Different symptoms, different fixes.

**GLM ratings live in TWO places (2026-07-15):** the per-card `🤖 GLM` pill
(actionable Buy/Exit cards only) AND a **`🤖 GLM news read` panel** on the main
page (`dashboard.py`, after the staleness banner, a **collapsed-by-default**
expander) that lists EVERY rated symbol with pill + reason, sorted
positive→negative, via `news_feed.load_glm_ratings()`. The panel exists because
actionable cards are empty in a risk-off market, which hid the second opinion
entirely — the panel always surfaces it. Still zero score weight.

## Dashboard: regime what-if toggle (2026-07-15)

**On the MAIN page (2026-07-15):** `🔀 Regime what-if` is a horizontal
`st.radio` sitting right above the Market-regime tile (moved OUT of the sidebar,
where it was buried on mobile) — `Actual | Assume risk-on | Assume risk-off`.
On each Buy/Strong Buy card it prints a one-line note of what the
signal WOULD be under the assumed regime (risk-off → soft-downgrade to Watch
via the regime gate; risk-on → holds, chase guard loosens). Approximation, not
a re-run — the stored score/vetoes drive it. Never mutates stored signals.

**Risk-on surfaces regime-gated Buys (2026-07-15):** selecting `Assume risk-on`
while the market is really risk-off now REVERSES the risk-off regime gate for
display: a `Watch` whose stored `main_reason` contains the exact phrase
`"market regime risk-off"` resurfaces as a `Buy` (never Strong Buy — the
pre-gate tier is unknown, so take the conservative one). Driven by a new
`display_signal` column that feeds the Actionable tile, Top pick, Action-today
cards + compact table; every promoted card/section is loudly labelled
what-if/verify-manually. The phrase match is exact, so confluence/chase/
earnings/rr/score-band Watches are NEVER promoted (tested). **Stored `signal`
is untouched; Portfolio heat still uses the real signals.** Caveat: the regime
gate is first in the soft-downgrade `elif` chain, so a promoted Buy may still
carry a secondary veto (poor_rr/RS) the engine never evaluated — hence the
verify-manually labels. `_display_signal()` in `dashboard.py`.

## "Run the repo news" — optional LLM-judged routine (only if weights restored)

User says **"Run the repo news"** any morning after 09:00 PKT → Claude:
1. Triggers `.github/workflows/news.yml` (workflow_dispatch on `main`) via
   `mcp__github__actions_run_trigger`. CI runs `python news_fetcher.py` which
   fetches last-24h headlines from Google News RSS per symbol (filtered to the
   allowlist) + Business Recorder / Dawn Business / Profit Pakistan Today /
   Mettis macro feeds, and commits `news_raw_24h.json`.
2. Pulls `news_raw_24h.json`, applies `news_routine.md` rules (exclude routine
   results/dividends; score 0–100; direction/materiality/confidence; sources
   from allowlist only), writes `news_signals.json`, commits + pushes.
3. Triggers `engine.yml` so the dashboard reflects fresh news-weighted signals.

**News weight is ZERO as of 2026-07-15** — the user turned news off because the
headline-driven score swings were noise (a single live-blog headline could flip
a symbol run-to-run). `config.WEIGHTS` is now **technical 1.0**, fundamentals
0.0, macro_news 0.0, sentiment 0.0 — `final_score == technical score`. The news
routine, `news_signals.json`, macro and sentiment sections are all STILL
computed and shown, and still drive the `bad_news` / `manipulation_risk` SAFETY
vetoes in `risk_manager` (those only downgrade a Buy→Watch, never fabricate),
but news no longer MOVES the score. To re-enable, restore e.g. technical 0.55 /
macro_news 0.20 / sentiment 0.25. Fundamentals was zeroed earlier (2026-06-19):
confirmed manually. `NEWS_SIGNALS_MAX_AGE_HOURS = 24` still gates stale files.

## Architecture (top-down)

`main.py` orchestrates one run:
1. Fetch market news, per-company news, benchmark index (KMI30).
2. `market_regime.assess_regime()` → risk-on/risk-off + `pct_above` (% the
   index sits above its 50-EMA).
3. For each stock: shariah check → quote/EOD → technical → sentiment →
   macro/news → fundamentals → relative strength → `scoring_engine.compute()` →
   `risk_manager.assess()` (now regime-aware) → `signal_generator.generate()`.
4. Save to SQLite (`psx_engine.db`); `backtester.update_outcomes()` fills
   forward prices and grades old runs (learning loop).

## Signal pipeline (signal_generator.generate)

Order of operations:
1. **No-data guard**: missing price → `"No data"` signal.
2. **Hard overrides** (always beat the score): shariah issue → `Avoid`;
   technical breakdown below support → `Exit` (if held) / `Avoid`.
3. **Score → base band**: `≥80 Strong Buy`, `≥75 Buy`, `≥60 Watch`,
   `≥50 Hold`, else `Avoid`. Strong Buy needs technicals confirming.
4. **Hysteresis dead-band** (`HYSTERESIS_BAND=2`): the band sits ENTIRELY
   ABOVE the threshold — enter at `threshold+2`, exit at `threshold`. It used
   to straddle (exit at `threshold-2`), which let a stale Buy persist at 73-74
   after the threshold moved to 75 — exactly the 30%-win band the raise was
   meant to exclude. Anti-flap is preserved by the upgrade side.
5. **Strong Buy confirmation gate**: a fresh Strong Buy is held at Buy until
   the very next run still scores Strong Buy. No numeric streak/conviction
   count is tracked or shown anywhere (removed — see below).
6. **Confluence — MEASURED, NOT A GATE (2026-08-12).** 4 dims: trend
   (price>50-EMA), momentum (RSI 40-74 AND MACD hist>0), volume (OBV up),
   structure (price>support AND no breakdown). The gate is REMOVED: graded
   outcomes were flat across it (2/4 won 17%, 3/4 26%, 4/4 25%) because the
   dims are not independent (trend and structure are near-collinear). Still
   computed, stored and shown per card.
7. **Chase guard — DISABLED 2026-08-12** (`CHASE_GUARD_ENABLED = False`). The
   extension is still computed and printed on the card as a `chase guard OFF`
   note, but it no longer steps a signal down. The regime-aware multiplier logic
   is retained behind the flag; flip the flag to restore it.
8. **Soft downgrades** (Buy/Strong Buy → Watch, first match wins): earnings
   blackout (≤5d), risk-off regime, `concentrated`, `poor_rr`, confidence<45,
   RS laggard. `bad_news` / `manipulation_risk` no longer fire (PURE_TECHNICAL).
   The `risk_level == "High"` branch was REMOVED — any veto forces High and
   every veto has its own branch above it, so it could never fire.
9. **Pullback-entry upgrade — REMOVED 2026-08-12.** The Buys it created
   (score below the Buy band) won 9% (n=57) vs a 38% market base rate. The
   SETUP (`pullback_ready` + buy-zone) is still computed and displayed as
   manual context; the engine no longer acts on it.
10. **Money-flow confirmation (2026-08-13, `BUY_MIN_CMF = 0.0`)**: a Buy whose
    CMF is ≤0 → Watch. Price rising without real buying pressure behind it.
    On 7-day graded history this improved beat rate 70→83%, median +2.63→+4.70%
    AND the worst case −4.3→−1.8% — filters rarely improve both. Halves the Buy
    count by design. CMF=None never vetoes.
    **Measured and REJECTED as safety filters** (both remove the BEST trades):
    rejecting 20-day run-ups >25% (rejected subset beat 92%, +9.87%) and
    rejecting stops wider than 8% (rejected subset beat 79%). Third independent
    confirmation that on this data buying strength works.
11. **RS laggard veto**: Buy/Strong Buy with `relative_strength <
    RS_LAGGARD_VETO (55, raised from 45 on 2026-08-12)` → Watch. RS<55 won 21%,
    RS 70+ won 36%; a 70 cut adds no accuracy once score≥75 applies but halves
    trade count. RS=None never vetoes (missing data can't block).

## Pure technicals + 50-EMA reference (2026-08-12)

User-directed risk-up. Three knobs in `config.py`:

- **`PURE_TECHNICAL = True`** — signals now come from price/volume ONLY. The
  score was already 100% technical (`WEIGHTS` technical 1.0), but news and
  sentiment could still MOVE a signal through the `bad_news` /
  `manipulation_risk` vetoes in `risk_manager`. Those are now emitted as
  WARNINGS only (still shown in the dashboard for manual cross-check) and are
  excluded from the `hard` count that sets `risk_level` — otherwise a bad
  headline would have kept downgrading Buys via the "High risk" branch.
  Structural gates are UNTOUCHED: shariah, breakdown, `poor_rr`, earnings
  blackout, regime, RS laggard.
- **`CHASE_GUARD_ENABLED = False`** — the engine no longer refuses to buy
  strength (see pipeline step 7).
- **`PULLBACK_EMA_SPAN = 50`** (was 20) — the reference EMA for BOTH the
  extension measure (`ext_pct`) and the pullback buy-zone (`ref_ema × 0.96` to
  `× 1.03`, floored at support). A deeper retracement = a wider, riskier zone.
  Because price inside a 50-EMA zone can sit slightly BELOW the 50-EMA, the old
  `price > ema50` trend test in `pullback_ready` would contradict the zone; it
  is replaced by "reference EMA rising over the last 10 sessions" plus the
  200-EMA test. The pullback RSI window widened 40-62 → 35-65 to match.

`technical['buy_zone_ema_span']` carries the span through to the dashboard/
signal reasons, so labels follow the config instead of being hardcoded.
`reports.py`'s "Entry zone" column now prints the real buy-zone (it was showing
support–EMA20, which was never the actual zone).

**Not changed:** `WEIGHTS` (already technical 1.0), the confluence gate,
hysteresis, Strong Buy confirmation, RS laggard veto, pullback quality gates
(`PULLBACK_MIN_SCORE`/`MIN_RS`) — those are technical/statistical, not news.

## Signal quality audit (2026-08-12) — the veto layer was inverting the edge

Measured on 43,470 stored rows, **day-deduped** (15-min polling inflates raw
counts ~20x — always dedupe to one row per symbol per day before believing any
win rate). Graded 3-day forward, compared to the SAME-DAY cohort median so the
market regime is controlled for (50% = no skill):

| cohort | n | beat market | median excess |
|---|---|---|---|
| signals actually emitted as Buy (old rules) | 97 | 36% | −0.63% |
| raw candidates score ≥70 | 198 | 56% | +0.37% |
| raw candidates score ≥75 | 81 | 63% | +0.85% |
| **new stack: score ≥75 AND RS ≥55** | 71 | **66%** | **+1.18%** |

**The raw technical score always had edge; the veto/gate layer was selecting the
worst subset of it.** Emitted Buys underperformed a coin flip while the
candidate pool they came from beat the market. That is the single most important
fact in this file — before adding any new gate, measure the emitted cohort
against the candidate pool, not against nothing.

Score band is the strongest discriminator (day-deduped, 3-day win): 70-75 → 30%
(n=66), 75-80 → 68% (n=28), 80+ → 86% (n=7). Two-thirds of Buys were coming
from the worst band, hence the 70 → 75 threshold move.

## Early warning / lead time (2026-08-13)

User asked for signals "well ahead of time, not when the price has already
hiked". Before building anything, every leading indicator the engine already
computes was measured on graded history (7-day forward vs SAME-DAY cohort
median, day-deduped; 50% = no skill):

| candidate | 3d beat | 7d beat | verdict |
|---|---|---|---|
| CMF > 0.10 | 58% | **61%** (+2.07%) | the ONLY one with edge |
| CMF > 0.10 inside score 60-75 | — | **75%** (+2.70%, n=16) | small but consistent |
| accumulation_candidate | 47% | 53% | no edge |
| OBV bullish divergence | 44% | 45% | NEGATIVE |
| OBV up while price flat | 40% | 37% | NEGATIVE |
| score velocity (3d rise >5) | — | 45% | NEGATIVE |

**Score velocity does not work** — a fast-rising score predicts nothing. Neither
do the OBV-based accumulation heuristics, which the dashboard had been showing
as bullish tags; the Accumulation-watch caption now says so explicitly.

`signal_generator.early_watch()` implements the one thing that measured: CMF >
`EARLY_WATCH_MIN_CMF` inside `EARLY_WATCH_SCORE_BAND` (55-75, below the Buy
band), structure intact (no breakdown, price > support), RS ≥ 45. It returns
`(bool, reason)` and is stored per run (`early_watch`, `early_reason`) and shown
in a `🔭 Early watch` dashboard section. **It is NOT a signal and never becomes
a Buy** — it is a monitoring tier that buys lead time and deliberately leaves
the validated Buy stack untouched.

**7-day grading added** (`outcome_7d`, `backtester._beat_market_7d`,
`db.cohort_forward_move(..., days=7)`): a lead signal needs room to play out, so
3 days cannot judge it. Stored SEPARATELY from `outcome` so the Buy/Avoid stats
keep their 3-day definition. In a few weeks this gives real evidence on whether
the early tier works — until then it is labelled unproven in the UI.

**Note the counter-evidence on "buy before the hike":** score≥75 candidates that
had ALREADY run >8% in the prior 5 sessions beat the market 92% with +8.77%
median excess (n=13, small), while Buys taken on 5-day dips lost (-1.30%, n=35).
On this sample momentum PERSISTED and buying early/cheap was worse. That is the
opposite of the intuition behind the request — worth re-testing as data grows
before acting on it either way.

## Confidence honesty (2026-07-15)

`scoring_engine.historical_confidence_adjust` counts ONLY strictly-graded
signals (Buy/Strong Buy/Avoid/Exit). Watch/Hold outcomes use the loose
"didn't lose >3%" rule (80-90% survival rates, not edge) and were inflating
every symbol's confidence toward the +15 cap.

## Conviction streak — removed

The dashboard used to show a "🔥 N-run/N-day streak" badge per stock. Removed
entirely: even day-bucketed, it kept giving a false sense of independent
confirmation. `db.signal_streak()` is gone; `conviction_streak` stays in the
`runs` schema (old rows only) but nothing writes to it anymore. The Strong Buy
confirmation gate (above) achieves the same "don't chase a one-run spike"
goal without surfacing a number that looks like a track record.

## Risk vetoes (risk_manager.assess)

- `breakdown` — price below support
- `poor_rr` — real headroom_rr below `min_headroom_rr` (1.5 baseline).
  **Regime-aware:** in risk-on, threshold ramps DOWN to floor 1.1 by
  `headroom_rr_riskon_full_pct=8.0` (% the benchmark sits above its EMA).
- `bad_news`, `manipulation_risk` — content-driven (warnings only under
  PURE_TECHNICAL)
- `concentrated` (2026-08-12) — this symbol is already above
  `RISK["max_existing_concentration_pct"]` (25%) of the REAL book read from
  `portfolio.json`. Per-trade sizing is blind to existing holdings, so an
  80%-of-account position kept producing clean Buys. Blocks ADDING only;
  no portfolio file / no position → never fires.

## Learning loop (backtester)

- `update_outcomes()` fills `price_1d/3d/7d` from real EOD; grades once 3-day
  price exists; credits/blames sub-indicators in `indicator_accuracy`.
- `_signal_worked()` grading rules:
  - **Buy/Strong Buy**: BEAT the real KMI30 3-day forward move without a stop
    hit (same benchmark → cohort-median → fallback chain as Avoid). Changed
    2026-08-12 from an absolute ">1% in 3 days", which mostly measured the
    market: it scored Buys at 22% against a 38% base rate for "any symbol rose
    >1%", so Buy and Avoid were not comparable. Re-graded: Buy win 22% → 39%.
  - **Avoid/Exit**: stock underperformed the **REAL KMI30 benchmark**
    forward move (3-day). Falls back to **cohort median** (engine's own
    universe) when the index isn't reachable. Final fallback: "did not rise"
    (chg<0). Three honest fallbacks, never fabricated.
  - **Watch/Hold**: loose grade — didn't lose >3%
- `regrade_all()` (`python main.py regrade`) wipes indicator_accuracy and
  re-grades EVERY completed run under current rules. Run this whenever
  grading rules change.

## Accuracy stats

`db.signal_accuracy_summary()` returns rows with `n_confidence`
(`high`/`medium`/`low`) — small-N win rates are flagged as NOISE, not edge.
CLI `python main.py accuracy` shows this with explicit warnings.

## Dashboard staleness

- `DATA_FRESHNESS_AMBER_HOURS=4` → tile turns amber, banner warns
- `DATA_FRESHNESS_RED_HOURS=24` → tile turns red, error banner

**Password-safe auto-refresh (2026-07-15):** on Streamlit Cloud the running
server serves the git snapshot from its last deploy; an open tab needs a full
reload to reconnect after a redeploy and re-read the committed DB. `_auto_refresh`
reloads every `DASHBOARD_REFRESH_SECONDS` (300). It USED to be disabled whenever
`DASHBOARD_PASSWORD` was set (a reload forced re-login) → the user had to reboot
manually. Now login stamps a non-reversible hashed token (`_auth_token`) into the
URL query string (`?k=…`); `window.location.reload()` preserves it, so the tab
re-authenticates itself across both the timed reload and Streamlit Cloud
redeploys (which drop server sessions). Trade-off: the token is a bearer
credential in the URL — fine for a single-user personal dashboard, noted in-code.
If the user rejects the URL-token approach, the fallback is host-independent:
pull latest run rows from a small committed JSON via GitHub raw with a short TTL.

## Dashboard trade-plan cards

Each Buy-signal card has an inline "📋 Full detail" expander (no extra data
fetch — uses fields already on the row: full reason, main risk, shariah
status, regime, support/resistance, buy-zone). Chart + per-stock backtest
still live only in the 📈 Stock detail tab to avoid an EOD fetch per card.

## Key files

- `config.py` — all knobs (thresholds, weights, risk caps, stocks).
- `signal_generator.py` — signal decision logic (the heart).
- `risk_manager.py` — veto layer + position sizing.
- `market_regime.py` — KMI30-driven regime + relative strength.
- `technical_analyzer.py` — TA score + flags (ext_pct, momentum_20d,
  headroom_rr, confluence inputs, accumulation candidates).
- `scoring_engine.py` — weighted final_score + confidence.
- `backtester.py` — learning loop + historical replay (in-sample/OOS/walk-forward).
- `database.py` — SQLite (tracked binary `psx_engine.db`).
- `dashboard.py` — Streamlit UI.
- `main.py` — CLI entry: `run / schedule / morning / evening / backtest SYMBOL /
  metrics / portfolio / accuracy / regrade / accumulating / history SYMBOL /
  fundamentals`.

## Environment notes

- PSX DPS (`dps.psx.com.pk`) returns **403 Forbidden** from this sandbox.
  All live analysis uses stored data via `db.last_run()` / `db.run_history()`.
- The cloud GitHub Action runs the engine automatically and commits
  `psx_engine.db` frequently → expect binary rebase conflicts. Resolve via
  `git checkout --theirs psx_engine.db`, then re-run any maintenance commands
  (e.g., `python main.py regrade`) and re-push.

### NEVER run a DB maintenance command while the engine loop is live
`engine.yml` loops every 15 min doing run → commit → `git pull --rebase -X
theirs` → push. In a rebase `theirs` is the commit being replayed — the LOOP's
own DB — so **the loop's copy wins every conflict and silently discards any DB
you pushed**. This ate a full `regrade` on 2026-08-13: the code change was in
`main`, but all 38,310 re-graded outcomes reverted to the old rule, and the only
symptom was the Buy win rate reading 22% again instead of 39%.

Safe procedure for `regrade` (or anything else that rewrites the DB):
1. Cancel the in-progress `engine.yml` run and WAIT for status `completed`.
2. `git pull origin main` to get the loop's final DB.
3. Run the maintenance command, commit, push.
4. Re-dispatch `engine.yml` — the fresh checkout starts from your DB.

Schema migrations self-heal (the next run's `init_db` re-adds missing columns),
but row DATA does not. After any regrade, VERIFY it stuck by re-reading the
win rate — do not assume the push held.

## Universe (KMI-30 verified + KMI All-Share)

See `KMI30_VERIFIED`, `KMIALLSHR_VERIFIED`, `OTHER_COMPLIANT` in config.py.
Re-verify each semi-annual recomposition (KMI30 effective 2026-05-25;
KMI All-Share effective 2026-06-05).

## Open / parked ideas

- Per-symbol-type backtest split (training vs evaluation window) — currently
  the in-sample/OOS split exists in `backtester.backtest()` but live signal
  accuracy stats are all in-sample.
- Earnings dates remain manual (`EARNINGS_DATES = {}` in config + optional
  `earnings_date` field in `news_signals.json`).

## Cross-account handoff — "continue where other account stopped"

This section is the resume point for any Claude account. It is committed to
`main`, so a fresh session sees it via git. **When the user says "continue where
other account stopped," read this section first, then `git pull origin main` to
get the latest state.** Keep this section current at the end of each work
session (edit the dates/state, commit, push).

**Last updated:** 2026-08-13 (early-warning tier + 7-day grading — see
"Early warning / lead time"). Previously 2026-08-12b (SIGNAL-QUALITY AUDIT — see "Signal quality audit"
below: Buy threshold 70→75, confluence gate removed, pullback upgrade removed,
RS veto 45→55, dead High-risk branch removed, concentration veto added, Buy
grading made benchmark-relative). Earlier same day (pure-technical mode: news/sentiment vetoes
downgraded to warnings; chase guard off; pullback/extension reference EMA
20 → 50). Previously: 2026-07-15 (news relevance-anchor gate stops cross-company
mis-attribution; regime what-if moved to main page; password-safe auto-refresh.
Earlier same-day: GLM free-tier key live + timeout fix, GLM-news-read panel,
risk-on what-if surfaces regime-gated Buys; deep signal-quality audit — pullback
quality gate, RS laggard veto, strict-history confidence).

### Current working context
- All recent work is committed directly to `main`. Today's code commits (all on
  `main`): `2ebc492` news_glm timeout/retry, `1529226` GLM panel + what-if
  overlay, `b4473aa` news relevance-anchor gate, `8d07ce3` regime toggle→main
  page + password-safe auto-refresh + GLM panel collapsed.
- **Reviewed but NOT changed (user's call):** #4 "is it 360°?" — the technical
  analyzer IS multi-indicator (RSI/MACD/EMA20-50-200/Bollinger/OBV/ADX/ATR/CMF/
  S-R/momentum/volume/candles/4-dim confluence), but `config.WEIGHTS` is
  technical 1.0 / fundamentals 0.0 / macro 0.0 / sentiment 0.0, so final_score
  is 100% technical — NO fundamental/valuation input. User chose to KEEP it
  100% technical (do not re-enable fundamentals without a data audit + explicit
  OK). As of 2026-08-12 the engine is PURELY technical end-to-end — see the
  "Pure technicals + 50-EMA reference" section; the reference EMA for the
  pullback zone / extension is now the 50-EMA and the chase guard is off.
- **GLM free-tier second opinion is LIVE.** `GLM_API_KEY` secret is set and
  valid; `news_glm_ratings.json` is written each news run (19 symbols last run).
  If it goes dark again, read the `news.yml` GLM step LOG (it's `|| true`, so the
  step conclusion lies): 401 = bad key, 60s+ timeout = slow China endpoint.
- News routine is fully operational and has been run daily (latest: commit
  `3f382f5`, "News routine 2026-06-24"). Follow the two-stage pipeline in the
  "Run the repo news" section above, and ALWAYS run the URL-verification script
  (below) before committing `news_signals.json`.
- Live PSX DPS is 403 in-sandbox → all live analysis uses stored data via
  `db.last_run()` / `db.run_history()`, and independent analysis is qualitative
  (never fabricate live prices/valuations).

### News URL-verification script (MANDATORY before every news commit)
```python
import json
d = json.load(open('news_signals.json'))
raw = json.load(open('news_raw_24h.json'))
raw_urls = set(it['url'] for it in raw['items'])
bad = [(s, u) for s, v in d['signals'].items() for u in v['sources'] if u not in raw_urls]
print('Unverified URLs:', bad)   # MUST be []
```
Common trap: copying URLs from a truncated `[:80]`-sliced exploration print.
Fix by patching each source from the raw fetch programmatically, never retyping.

### In-flight / recent analysis threads
- **PSO** (user's portfolio is ~83% PSO, avg ~363.8, in loss): covered backtest
  mechanics, relative-strength calc, and a 6-month averaging strategy. Key take:
  concentration is the real risk, not PSO itself; tranche around the 344.54 stop,
  diversify into PRL. Engine last had PSO at "Avoid" (score 45, news-driven).
- **KMI-30 independent top picks (2026-06-24):** MARI (top conviction — Shams-1
  gas catalyst, cleanest E&P balance sheet), MEBL (rate-cycle Islamic bank),
  OGDC (Sahito-1 catalyst but oil-price hedged), a fertilizer name (EFERT/FFC,
  defensive income). Avoid pure oil-beta (PPL) and PSO into falling oil.
- **MARI deep-dive → `analysis/MARI_verification_checklist.md`** (committed). Six
  numbers to verify (Shams-1 volume, valuation multiple, dividend, RRR, net cash,
  % market-linked output) with a buy/wait/pass decision rule. This is the current
  active deliverable — next step is filling those six numbers from PSX/financials.

### Parked (only resume if user asks)
- Item #5 from an earlier "start with 2, then 3 and then 5" instruction: a PSO
  confluence-dimension breakdown (trend/momentum/volume/structure) — never done.
