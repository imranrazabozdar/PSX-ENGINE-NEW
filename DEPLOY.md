# PSX Engine — Cloud Deployment Guide

This puts the engine in the cloud so it runs **with your laptop off**, emails you
the Excel, and serves a **password-protected dashboard** you can open from your
phone. All free tier.

**Architecture:** GitHub Actions runs the engine on a schedule → emails you →
commits fresh data → Streamlit Community Cloud serves the dashboard from the repo.

Your details (already set up):
- GitHub user: `fahadalipersonal313-ai`  •  private repo created
- Email (send + receive): `your-gmail@gmail.com`
- Streamlit linked to GitHub ✔  •  App Password + dashboard password saved ✔

---

## Part A — Push the code to your GitHub repo

**Easiest: GitHub Desktop (GUI)**
1. Install GitHub Desktop from **desktop.github.com**, sign in with your GitHub account.
2. **File → Add local repository** → choose this folder:
   `C:\Users\hp\Documents\psx_shariah_engine\psx_engine`
3. If it says "this isn't a Git repository", click **create a repository** → **Create**.
   (The included `.gitignore` already excludes `venv/`, logs, and secrets.)
4. Bottom-left: type a summary like `Initial commit` → **Commit to main**.
5. Top bar: **Publish branch** → choose your **existing private repo** → **Publish**.

> Tell me if you'd rather use the command line — I can run the `git init` /
> `commit` for you locally so you only do the final authenticated `push`.

**Verify:** refresh your repo page on github.com — you should see all the `.py`
files, `requirements.txt`, and the `.github/workflows/` folder.

---

## Part B — Add the 3 email secrets to GitHub

These let the cloud send you email. The App Password goes here — **not in chat.**

1. On github.com open your repo → **Settings** → (left) **Secrets and variables**
   → **Actions** → **New repository secret**.
2. Add these three, one at a time (name must match **exactly**):

   | Name | Value |
   |---|---|
   | `SMTP_USER` | `your-gmail@gmail.com` |
   | `SMTP_APP_PASSWORD` | your 16-char App Password (no spaces) |
   | `EMAIL_TO` | `your-gmail@gmail.com` |

3. Click **Add secret** for each. (You can't read them back — that's expected.)

---

## Part C — Turn on and test the scheduler

1. Repo → **Actions** tab. If prompted, click **"I understand my workflows,
   enable them"**.
2. You'll see **PSX Engine Loop** and **PSX Evening Summary**.
3. Click **PSX Engine Loop** → **Run workflow** → **Run workflow** (this triggers
   it now instead of waiting for 09:45 PKT).
4. Watch it go green (~2–3 min). Because the manual run uses `EMAIL_MODE=actionable`,
   you'll get an email **only if** a Buy/Strong Buy/Exit appears. To force a test
   email, see the tip below.

**Schedule (already configured, PKT):** first run **09:45**, then **every 10 min**
to **15:50**, Mon–Fri; **evening summary 21:00**.
⚠️ GitHub cron is best-effort — runs can lag 5–15 min. That's the free-tier trade-off.

> **Force a test email now:** Actions → PSX Evening Summary → **Run workflow**.
> It uses `EMAIL_MODE=always`, so it emails you the summary regardless.

---

## Part D — Publish the password-protected dashboard

1. Go to **share.streamlit.io** → **Create app** → **Deploy a public app from GitHub**.
2. Fill in:
   - **Repository:** `fahadalipersonal313-ai/<your-repo-name>`
   - **Branch:** `main`
   - **Main file path:** `dashboard.py`
3. Click **Advanced settings → Secrets** and paste exactly (TOML format):
   ```toml
   DASHBOARD_PASSWORD = "your-dashboard-password"
   ```
4. Click **Deploy**. First build takes ~2–4 min.
5. You get a public link like `https://<something>.streamlit.app`.
   Open it on your **iPhone browser** → it asks for the password → you're in.

> The app reboots automatically each time the scheduler commits fresh data, so
> the dashboard stays current. Free apps may "sleep" after idle — just reopen the
> link to wake it (a few seconds).

---

## Part E — Daily use
- **Phone dashboard:** open your `*.streamlit.app` link, enter the password.
- **Email:** you get the Excel when something actionable appears, plus the 21:00 summary.
- **On-demand run:** Actions → PSX Engine Loop → Run workflow.
- **Change stocks / risk rules:** edit `config.py`, commit/push — the cloud picks it up.

## Notes & limits
- Timing is approximate (GitHub cron jitter). For exact timing, a ~$5/mo VPS with
  real cron is the upgrade path.
- Scheduled workflows auto-pause after 60 days of **zero** repo activity — any
  commit (or a manual run) resets that.
- The dashboard shows data as of the **last committed run**, not a live tick.
- Sentiment uses public Google News per company; coverage of thin-traded names
  (e.g. TREET) can be sparse — the engine lowers confidence accordingly.

## Reliable scheduling via cron-job.org (set up 2026-06-12)

GitHub's own `schedule:` cron fired **zero** times on this private repo, so the
15-minute cadence is driven externally: a free https://cron-job.org account
POSTs to the GitHub API, which starts the workflow via `workflow_dispatch`.

Auth: a **fine-grained PAT** (Settings → Developer settings → Personal access
tokens → Fine-grained), Repository access = only `psx-engine`, Permissions =
**Actions: Read and write** (Metadata: read is added automatically). The token
lives ONLY in cron-job.org's header config. If it expires, runs silently stop —
re-create the token and update the jobs.

Each cron-job.org job (all timezone **Asia/Karachi**, treat 204 as success):
- URL: `https://api.github.com/repos/fahadalipersonal313-ai/psx-engine/actions/workflows/engine.yml/dispatches`
  (evening job: same URL with `evening.yml`)
- Method: POST; body (raw JSON): `{"ref":"main"}`
- Headers: `Authorization: Bearer <TOKEN>`, `Accept: application/vnd.github+json`,
  `Content-Type: application/json`

| Job | Schedule (PKT) | Days | Workflow |
|---|---|---|---|
| Morning kickoff | 09:45 | Mon–Fri | engine.yml |
| Market loop | minutes 0,15,30,45, hours 10–15 | Mon–Fri | engine.yml |
| Evening summary | 21:00 | Mon–Fri | evening.yml |

**Minutes budget (free private repo = 2,000 min/month):** each engine run bills
~3 min with the slim `requirements-ci.txt` (~5 min with the full install — do
NOT point the workflows back at `requirements.txt`). 25 runs/day ≈ 1,700
min/month — inside the free tier, with little headroom. If GitHub ever emails
about hitting the Actions limit, either widen the interval to 20–30 min or make
the repo public (public repos have unlimited Actions minutes).
