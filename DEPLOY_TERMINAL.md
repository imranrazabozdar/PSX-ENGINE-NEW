# PSX Research Terminal 2.0 — deployment

This is the **Flask** terminal (`psx_pro_v2.py`), which is a different
application from the Streamlit engine documented in `DEPLOY.md`. Both live in
this repo and do not interfere with each other.

| | Streamlit engine | Research Terminal 2.0 |
|---|---|---|
| Entry point | `app.py` → `dashboard.py` | `psx_pro_v2.py` |
| Framework | Streamlit | Flask |
| Host | Streamlit Community Cloud | Render (or any WSGI host) |
| Requirements file | `requirements.txt` | `requirements-terminal.txt` |
| Data | committed `psx_engine.db` | live PSX via `psxdata` + CSV cache |

**Streamlit Community Cloud cannot host this app.** It only serves Streamlit
scripts. The Flask terminal needs an ordinary web host — the app already reads
`$PORT` and probes `RENDER_SERVICE_NAME`, so Render is the path of least
resistance.

---

## Deploy on Render

1. Sign in at **render.com** with the GitHub account that owns this repo.
2. **New → Blueprint**, pick this repository. Render reads `render.yaml` and
   fills in the build and start commands.
3. Set **`APP_PASSWORD`** in the Render dashboard (Environment tab). It is
   marked `sync: false` so it is never committed. With it set, every route is
   behind HTTP basic auth; leave it empty and the terminal is public.
4. Adjust **`CAPITAL`** (default `1000000` PKR) — it drives every share count
   and the book-level heat caps.
5. **Create**. First build takes a few minutes.

Any host works the same way; the `Procfile` covers Railway, Fly and
Heroku-style platforms.

```bash
gunicorn psx_pro_v2:app --workers 1 --threads 8 --timeout 300 --bind 0.0.0.0:$PORT
```

**One worker is deliberate.** Scan progress, the watchlist and the regime
cache are module-level dicts. A second worker keeps its own copy and the UI
flips between two different scans. Scale with threads, not workers.

## Run locally

```bash
pip install -r requirements-terminal.txt
python psx_pro_v2.py          # http://localhost:5000
python test_wyckoff.py        # 24 assertions, all passing
```

Environment knobs: `CAPITAL`, `PSX_DB` (journal + positions ledger),
`PSX_CACHE_DIR`, `FUND_FILE`, `SHARIAH_FILE`, `APP_PASSWORD`, `LIVE_TTL`,
`SCAN_WORKERS`.

---

## Read this before trusting a verdict

**Five of the thirteen modules were written from scratch on 2026-08-17 and
have no backtest behind them.** The v2.0 bundle shipped
`psx_pro_v2 / psx_wyckoff / psx_verdict / psx_context / psx_risk / psx_memory`
but not the five modules they import. Those five —
`psx_brain`, `psx_report`, `psx_live`, `psx_scan`, `psx_export` — were
reimplemented against the call surface the bundle expects.

- `psx_brain.analyse()` **is the engine that emits BUY / AVOID**. Its
  indicators are standard published formulas and are arithmetically correct.
  Its `SCORE_WEIGHTS` and `CUTOFFS` are judgement calls with **zero
  validation**. They sit at the top of the file so they can be seen and
  changed rather than trusted.
- `psx_brain.compare()` is the one piece of the original that survived and is
  reproduced as written.
- `psx_live` is built on the PSX **screener**, which is an end-of-day table
  with an intraday refresh — *not* a real-time tick tape. The original called
  `psxterminal.com`, whose response shape is not documented anywhere in the
  surviving code; guessing at it would have failed silently. Block-trade
  detection is therefore **not emitted at all** rather than faked from daily
  aggregates.
- Nothing here fabricates a number. Every module returns `None` and says so
  when its feed is unreachable.

Before sizing real positions on this, backtest the emitted verdicts against
graded history — and measure the *emitted* cohort against the *candidate pool*
it was drawn from, not against nothing. This repo's own signal-quality audit
found emitted Buys winning 36% while the pool they came from won 56%: an
untested threshold layer sitting on a real edge destroyed it. That is the
exact shape of the risk here.

## Verify before relying on the shariah tab

`psx_context.KMI30` is a **typed-from-memory snapshot**, flagged as such by its
own author, and `KMI30_AS_OF` is `2025-12-31`. The module warns once the
snapshot passes 200 days. Check it against the current PSX KMI-30 notification
before treating any "Compliant" label as verified. Absence from the list is
reported as *needs manual verification*, never as non-compliance.

## Known operational limits

- **PSX blocks datacentre IPs.** `dps.psx.com.pk` returns 403 from most cloud
  hosts. Hit `/diag` after deploying — it distinguishes "no outbound network"
  from "PSX refuses this server". If PSX is blocked, `psx_report` falls back to
  its CSV cache and the terminal keeps working on stored bars.
- **Render's free tier has an ephemeral filesystem.** The signal journal
  (`psx_v2.db`) and the price cache are wiped on redeploy. Attach a persistent
  disk, or accept that the TRACK RECORD tab restarts empty.
- Outcome grading needs forward bars, so a verdict journalled today cannot be
  graded for at least seven sessions. Press **GRADE PENDING** weekly.
