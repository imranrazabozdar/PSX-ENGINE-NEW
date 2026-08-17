# PSX Research Terminal 2.0 — upgrade notes

## What I compared

**Your engine** (`psx_report` / `psx_brain` / `psx_scan` / `psx_live` / `psx_export`
/ `psx_pro`) — a Flask terminal with a genuinely deep indicator engine and a
written-reasoning layer.

**The GitHub project** (`psx-engine-main`, 26 modules, ~6,400 lines) — a
Streamlit engine built around shariah screening, a 40/30/30 score, SQLite
persistence and outcome grading.

## The honest verdict on the comparison

Your indicator engine is **substantially better than theirs** and I did not
replace any of it. Their `technical_analyzer.py` has RSI, MACD, EMA, Bollinger,
OBV and an ADX *proxy*. Yours has Ichimoku, Supertrend, Keltner, Donchian, VPT,
Force Index, MFI, CMF, regression quality, z-score, pivots, Fibonacci, candle
patterns, multi-timeframe resampling and a real ADX. Their scoring is a weighted
average; yours produces argued reasoning with named traps. Copying their
technicals into yours would have been a downgrade.

What they had that you did not was everything **above and around** the chart.
That is what I built, as new modules that import yours rather than edits that
could break what works.

| Capability | You had | They had | v2.0 |
|---|---|---|---|
| Indicators, multi-timeframe | Strong | Weak | unchanged — yours |
| Written reasoning / traps | Strong | Weak | unchanged — yours |
| Live tape, breadth, block trades | Strong | None | unchanged — yours |
| Excel / PDF export | Strong | Basic | unchanged — yours |
| **Wyckoff structure** | None | None | **new — `psx_wyckoff.py`** |
| Market-regime gate | Breadth only, not wired to verdicts | Yes | **new — `psx_context.py`** |
| Relative strength | 3-month excess only | Blended 1m/3m/6m, 0-100 | **new — blended** |
| Shariah screening | None | Yes | **new — `psx_context.shariah`** |
| Fundamentals scoring | Raw tables only | Absolute thresholds | **new — peer-relative** |
| Risk veto layer | None | Yes | **new — `psx_risk.py`** |
| Position size in shares/PKR | % only | Yes | **new** |
| Book-level heat + sector caps | None | Yes | **new — `psx_risk.book`** |
| Signal journal + outcome grading | None | Yes | **new — `psx_memory.py`** |
| Portfolio P/L ledger | None | Yes | **new** |

## New files

| File | What it does |
|---|---|
| `psx_wyckoff.py` | The Wyckoff analyst. Finds the trading range, classifies it, labels PS/SC/AR/ST/Spring/UT/UTAD/SOS/SOW/LPS/LPSY, assigns Phase A–E, grades every Spring and Upthrust High/Med/Low against the classic criteria, applies the three laws, projects cause-and-effect targets, states PSX-specific risk. Price and volume only. |
| `psx_context.py` | Regime gate, blended relative strength, shariah status, peer-relative fundamentals. |
| `psx_risk.py` | Per-trade veto layer with real share counts; book-level heat, sector and position caps. |
| `psx_memory.py` | SQLite journal of every verdict, outcome grading, confidence feedback, positions ledger. |
| `psx_verdict.py` | The composite. Makes the chart read and the Wyckoff read **argue** instead of averaging. |
| `psx_pro_v2.py` | Your dashboard plus six new tabs. Every v1 route and tab is untouched. |
| `test_wyckoff.py` | 24 assertions against synthetic bars with a known structure. All pass. |

## How to run

```bash
pip install flask psxdata pandas numpy openpyxl reportlab
python psx_pro_v2.py                  # then open the printed address
```

No new dependencies — `sqlite3` is in the standard library. Environment knobs:

```bash
CAPITAL=750000       # capital used for position sizing (default 1,000,000)
PSX_DB=psx_v2.db     # signal journal + positions ledger
FUND_FILE=fundamentals.json    # optional local fundamentals cache
SHARIAH_FILE=shariah.json      # optional {"compliant": ["SYM", ...]}
APP_PASSWORD=...     # as before
```

`python test_wyckoff.py` runs the Wyckoff test suite.

## The six new tabs

- **DEEP READ** — the composite for the selected stock: chart score, structure
  score, fundamentals score, relative strength, regime, shariah, the risk checks,
  and a real share count with the rupee loss if the stop fills. Shows every
  downgrade and why. Journals itself automatically.
- **WYCKOFF** — weekly and daily structure side by side, with a phase strip, a
  range bar showing where price sits, an event table, per-criterion tick-boxes for
  each Spring and Upthrust, the three laws, and the full written read.
- **WYCKOFF SCAN** — screen your list, KSE-100 or the whole market for structures,
  ranked by structural readiness. Click a row for the full read.
- **BOOK RISK** — sizes every current BUY candidate together and shows where the
  heat, sector and position caps bind. Deferred names keep their reason.
- **PORTFOLIO** — your real holdings marked to live prices, with realised win rate,
  profit factor and expectancy on closed trades.
- **TRACK RECORD** — the journal, verdict outcomes, and which individual signals
  are actually working, with anything under 20 observations marked as noise.

## Design decisions you may want to argue with

1. **The composite can only downgrade, never upgrade.** More evidence should make
   you more selective, not more confident. A `BUY` from `psx_brain` can become
   `WAIT`; a `WAIT` can never become a `BUY`.

2. **Disagreement is reported, not averaged.** Chart bullish + structure
   distributive forces a downgrade and says so, because indicators lag
   distribution — they look fine while it happens. Structure bullish + chart not
   yet turned is labelled "early": smaller size, stop under the range floor.

3. **Learning moves confidence only, never the weights.** If your Springs stop
   working, confidence falls and the model stays visible and unchanged, so you
   can see it being wrong instead of having it quietly rewrite itself.

4. **The Wyckoff module refuses to label.** "No valid Spring" and "no trading
   range" are normal outputs. A support break that does not close back inside
   within three bars is reported as a **breakdown**, explicitly not a Spring, and
   a poke above resistance that closes near its high is rejected outright rather
   than graded Low. Penetrations within five bars of each other are merged into
   one event, because five "Springs" in six sessions is one shakeout described
   five times.

5. **Range boundaries are statistical, then refined by the Automatic Rally.**
   The 8th/92nd percentile of lows and highs gets the first cut; where an AR
   exists it overrides, because in the method the AR sets the creek. Without that
   refinement a late breakout drags resistance up and price reads as "100% of the
   way up the range" when it has already left.

6. **Shariah absence is not non-compliance.** Anything not in the KMI-30 snapshot
   returns "needs manual verification", stated as an absence of evidence.
   **Update `KMI30` and `KMI30_AS_OF` in `psx_context.py`** after each semi-annual
   recomposition — the module warns you once the snapshot passes 200 days.

## Known limits

- **The KMI-30 list in `psx_context.py` is a snapshot I typed from memory of the
  general constituent set. Verify it against the current PSX notification before
  you rely on the shariah tab for anything.** It is the one place in this build
  where a wrong value would be quietly wrong rather than loudly missing.
- Cause-and-effect targets approximate a point-and-figure horizontal count from
  range width times duration. They are magnitude estimates, labelled as such.
- Fundamentals fall back to absolute scales when no sector peer data exists.
  Populate `fundamentals.json` as `{"data": {"PSO": {"pe": 3.8, "roe": 15.6,
  "de": 1.07, "div_yield": 2.9, "eps_growth": 211}}}` to get peer-relative
  scoring.
- Outcome grading needs forward bars to exist, so a verdict journalled today
  cannot be graded for at least seven sessions. Press **GRADE PENDING** weekly.
- Wyckoff on end-of-day bars cannot see intraday spread behaviour, which
  discretionary Wyckoff analysis leans on. Close location is available; the
  sequence within the day is not.
- I could not reach `dps.psx.com.pk` from my sandbox, so every new module was
  validated against synthetic bars with a known structure and against your live
  code paths with a stubbed loader — not against real PSX data. Run
  `test_wyckoff.py` and then spot-check two or three names you know well by eye
  before trusting the Wyckoff labels.
