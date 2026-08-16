# Daily Authentic-News Routine — "Run the repo news"

Produces `news_signals.json` — the authentic, Claude-judged news layer the PSX
engine reads (via `news_feed.py`) for the `sentiment` slot (weighted **20%** of
the final signal — see `config.WEIGHTS`) and the bad-news veto.

## Trigger
User says **"Run the repo news"** in chat (any morning after 09:00 PKT). Claude:
1. Calls `mcp__github__actions_run_trigger` for `news.yml` (workflow_dispatch on
   `main`). The Action runs `python news_fetcher.py`, which fetches the
   last-24h headlines and commits `news_raw_24h.json`.
2. Pulls the committed `news_raw_24h.json`, reads each item, applies the
   judgment rules below, writes `news_signals.json`.
3. Commits + pushes `news_signals.json` to `main`. Next engine.yml run picks it
   up automatically; the dashboard's signals then carry the 20% news weight.

The fetcher runs in CI because Pakistani news hosts return `403 host_not_allowed`
from Claude's sandbox; CI is unrestricted.

## Window — strictly 24h
`news_fetcher.py` sets `WINDOW_HOURS = 24` and `config.NEWS_SIGNALS_MAX_AGE_HOURS
= 24`. Anything older than 24h is dropped at fetch AND ignored at read.

## Authentic sources ONLY (`config.NEWS_SOURCE_ALLOWLIST`)
- `brecorder.com` — Business Recorder
- `dawn.com` — Dawn Business
- `mettisglobal.news` — Mettis Global
- `profit.pakistantoday.com.pk` — Profit Pakistan Today
- `news.google.com` — Google News RSS (aggregator; the underlying URL still
  must resolve to one of the four sources above to count)

**Never** use any other site — no PSX portal, no social media, forums, or rumor
sites. If a claim appears only off-allowlist, treat it as unverified.

## EXCLUDE routine financial results & dividends
The engine ALREADY auto-fetches fundamentals (P/E, EPS growth, ROE, debt/equity,
dividend yield). News that merely re-reports quarterly/annual **results, profit
%, EPS, revenue, or dividend declarations is DOUBLE-COUNTING and must be
SKIPPED.** This news layer is for EVENT signal that fundamentals cannot capture:
- corporate actions: M&A, acquisitions, public offers, stake sales, spin-offs
- discoveries / new wells / new plants / capacity expansion / major contracts
- regulatory & policy: tariff/gas-price/duty changes, OGRA/SBP/SECP actions,
  circular-debt or subsidy decisions affecting the company
- index changes (KMI/MII30/JSMFI inclusion/removal)
- management/governance shocks, litigation, default/restructuring, plant outages
- geopolitical / macro shocks with a clear single-stock channel (oil price for
  E&P names, gas-tariff for fertilisers, IMF/SBP rate path for banks)

If a stock's only news is "profit up X%, dividend Rs Y" → OMIT it. If an article
mixes an event WITH results, keep ONLY the event in the summary.

## Per-symbol judgment schema
For each symbol with real, sourced 24h news, write:

| field | values |
|---|---|
| `score` | 0–100; 50 = neutral. Sober — a normal contract win is ~60–65, a transformational discovery 70–80, a default/major hit 15–25. |
| `direction` | `positive` \| `negative` \| `neutral` |
| `materiality` | `normal` \| `material_negative` \| `material_positive`. `material_negative` triggers Buy→Watch veto — reserve for genuinely serious news. |
| `confidence` | `high` (official filing / clear result) \| `medium` \| `low` (thin/indirect — prefer `low` to guessing) |
| `summary` | one plain-English line |
| `headlines` | 1–3 real titles |
| `sources` | their URLs (allowlist hosts only) |
| `earnings_date` | optional `YYYY-MM-DD` if a source reports an upcoming board meeting / result announcement; engine holds fresh Buys at Watch within ~5d of it. Omit if unknown — never guess. |

Symbols with no real 24h news are OMITTED — `news_feed.py` treats absent
symbols as neutral. Do NOT invent neutral filler.

## Schema
```json
{
  "as_of": "2026-06-18T09:30:00+05:00",
  "generated_by": "claude-news-routine",
  "window_hours": 24,
  "source_allowlist": ["brecorder.com", "dawn.com", "mettisglobal.news",
                       "profit.pakistantoday.com.pk"],
  "macro_summary": "One-line market-wide note from the macro feeds (optional).",
  "signals": {
    "OGDC": {
      "score": 62,
      "direction": "positive",
      "materiality": "normal",
      "confidence": "high",
      "summary": "Bobi Deep-1 oil & gas discovery in Sindh — fresh reserve-add catalyst.",
      "headlines": ["Bobi Deep-1: OGDC makes significant oil and gas discovery"],
      "sources": ["https://www.brecorder.com/news/40423877/..."]
    }
  }
}
```

## Commit
```
git add news_signals.json
git commit -m "News routine $(date -u +'%Y-%m-%dT%H:%MZ')"
git pull --rebase -X theirs --autostash origin main || git rebase --abort
git push
```

## Notes
- The engine's 15-min loop only READS this file; it never fetches news.
- Freshness: engine ignores the file if older than 24h
  (`NEWS_SIGNALS_MAX_AGE_HOURS`). If you skip a day, news contributes neutral
  until the next run.
- PSX market days are Mon–Fri.
