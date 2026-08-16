# PSX Shariah-Compliant Stock Analysis Engine

A Python decision-support engine for Pakistan Stock Exchange stocks that are
verified shariah compliant. It scores stocks out of 100, generates disciplined
signals, stores every run, learns from outcomes, and enforces strict risk
management.

**It is NOT financial advice and it cannot guarantee profit or zero loss.**
No system can. It labels every setup low / medium / high risk and requires
manual confirmation before any buy.

---

## 1. What it does

Every run (manual or every 10 minutes during market hours) it:

1. Verifies shariah status against the official KMI-30 constituent list
   (screening date 2025-12-31, effective 2026-05-25) plus documented
   exceptions (FABL as a converted full Islamic bank). Anything unverified is
   marked **Needs manual verification** and excluded from the ranking.
2. Fetches latest price/volume from the **PSX official public data portal**
   (dps.psx.com.pk) and public RSS news (Business Recorder, Dawn, Profit,
   Mettis). No logins, no scraping bypass, every value source-tagged.
3. Scores each stock out of 100:
   - **40%** Macro + industry + fundamentals context + news
   - **30%** Public sentiment (with hype / pump-and-dump / panic flags)
   - **30%** Technical analysis (RSI, MACD, EMA 20/50/200, Bollinger, OBV,
     ADX-proxy, support/resistance, breakout/breakdown, volume spikes,
     stop-loss and target zones, risk/reward)
4. Applies risk filters and produces: **Strong Buy / Buy / Watch / Hold /
   Avoid / Exit**, with confidence %, position sizing, and warnings.
5. Stores everything in SQLite, then later fills in real prices after 1/3/7
   days and grades whether each signal worked — adjusting future
   **confidence** (never the 40/30/30 weights) and warning about overfitting
   on small samples.
6. Prints and saves a clean markdown report; 9 AM and 9 PM summary reports.

Default universe: PSO, TREET, FABL, AIRLINK + verified KMI-30 picks
MEBL, SYS, LUCK, FFC, OGDC, MARI.

## 2. Install (Windows-friendly)

```bat
:: 1. Install Python 3.10+ from python.org (tick "Add to PATH")
:: 2. Open Command Prompt in the project folder, then:
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## 3. Run

```bat
python main.py run            :: one full analysis + report
python main.py schedule       :: auto every 10 min + 9AM/9PM reports
python main.py morning        :: morning report
python main.py evening        :: evening report + outcome grading
python main.py backtest PSO   :: technical backtest for a symbol
python main.py accuracy       :: signal & indicator accuracy stats
python main.py history PSO    :: stored run history
streamlit run dashboard.py    :: interactive dashboard
```

Reports are saved in `reports_out/`, the database is `psx_engine.db`, and
logs go to `engine.log`.

## 4. Configure

Everything lives in `config.py`:

- **Stocks**: edit `DEFAULT_STOCKS` / `ADDITIONAL_STOCKS` and `SECTORS`.
- **Shariah list**: `KMI30_VERIFIED` + `KMI30_VERIFICATION_DATE`. KMI-30 is
  recomposed semi-annually — update this from the PSX notification and the
  engine warns automatically when the snapshot is stale.
- **Macro anchors**: fill `MACRO_ANCHORS` (SBP policy rate, CPI, USD/PKR,
  reserves) with values and as-of dates from official releases.
- **Data sources**: `NEWS_FEEDS`, PSX endpoint URLs.
- **Risk rules**: `RISK` (max risk per trade, min RR, liquidity floor, etc.).
- **Schedule**: interval, market hours, report times.

Optional extra sentiment input: create `public_comments/SYMBOL.txt` and paste
public, legally accessible comments (one per line) from open polls/forums you
are permitted to use. The engine never scrapes login-protected content.

## 5. How scoring works

Final = 0.40 × MacroNews + 0.30 × Sentiment + 0.30 × Technical.

- Macro/news blends macro-headline polarity (35%), sector-driver headlines
  (30%) and company headlines (35%).
- Sentiment maps polarity of public mentions to 0–100, shrunk toward neutral
  when mention volume is low (silence is never treated as bullish).
- Technical awards points for trend (EMAs), RSI zone, MACD, momentum,
  volume/OBV, breakout status and trend strength, normalised to 0–100.

Confidence starts at 70%, drops 12 pts per weak data section, rises when all
three sections agree, and shifts up to ±15 pts based on the stock's real
historical signal win rate (capped hard when fewer than 10 graded signals
exist — overfitting protection).

## 6. How signals work

| Final score | Base signal |
|---|---|
| ≥ 80 + technical confirmation + acceptable risk | Strong Buy |
| 70–80 | Buy |
| 60–70 | Watch |
| 50–60 | Hold |
| < 50 | Avoid |

Overrides beat scores: technical breakdown → Exit/Avoid; material bad news →
Avoid; shariah unverified → Avoid; poor risk/reward, hype/pump risk, High
risk level, or confidence < 45% downgrade Buys to Watch. Every Buy requires
manual confirmation.

## 7. Risk management

Position size = (capital × max-risk-per-trade) ÷ (entry − stop), capped at
the max-position percentage. No leverage, never all-in, diversify across
sectors. Warnings cover illiquidity, volatility/gap risk, breakdowns, news
shocks, manipulation/hype, panic selling, and over-excitement.

## 8. Limitations (read this)

- PSX has no free official streaming API; the public portal data can lag or
  fail. The engine then uses the last stored value **with a visible warning**
  — it never invents numbers.
- Audited fundamentals (margins, debt, cash flow, dividends) are not
  auto-ingested; the macro module says so and tells you to check the latest
  quarterly report before buying.
- Sentiment is limited to legally accessible public text; coverage of small
  caps may be thin, and the engine reduces confidence accordingly.
- Shariah status is a snapshot of official screenings; ratios change every
  quarter. Re-verify before acting — the engine warns when stale.
- Backtests are in-sample, technical-only, and labelled as such.
- ADX and candle patterns are close-price proxies (the public EOD feed lacks
  high/low data) and are labelled proxies in the output.

## 9. Risk warning

Equity investing can lose some or all of your capital. This tool exists to
impose discipline — stop losses, sizing, diversification, and honest
uncertainty — not to predict the future. Treat every output as a hypothesis
to verify, consult a licensed advisor for personal decisions, and never trade
money you cannot afford to lose.

## 10. Interpreting the output

- **Final score / section scores**: where strength or weakness comes from.
- **Confidence %**: how much to trust the score given data quality and the
  engine's own track record on that stock. Below ~50%, treat as research only.
- **Data quality**: "weak: ..." means a section had thin inputs this run.
- **Entry zone**: between support and EMA-20 — never chase above resistance.
- **Stop / targets / RR**: pre-commit to the stop; skip setups under 2:1.
- **Risk level**: High means stand aside unless you have independent reasons.
- **Watch next**: the specific levels and events that would change the call.
