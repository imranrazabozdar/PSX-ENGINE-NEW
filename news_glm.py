"""news_glm.py — Rate each symbol's last-24h headlines with GLM-4.5-flash
(ZhipuAI free tier) into one of: highly_positive / positive / neutral /
negative / highly_negative. Writes news_glm_ratings.json.

Zero score weight: the rating is a SECOND OPINION shown on the dashboard next
to the engine's Buy/Avoid so the user can eyeball whether the LLM's read of
the news agrees. Never fed into the score.

Token-frugal: ONE batched request for every symbol that actually has fresh
headlines. Prompt is compact; response is small JSON. Skips silently when
GLM_API_KEY is unset, when news_raw_24h.json is absent, or when no symbol has
any credible headlines. Never fabricates a rating — a symbol with no fresh
news is simply absent from the output file.
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone

import requests

import config
import news_feed

log = logging.getLogger("news_glm")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

GLM_ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
GLM_MODEL = os.environ.get("GLM_MODEL", "glm-4.5-flash")
# The bigmodel.cn endpoint is mainland-China-hosted; the hop from GitHub's US
# runners is slow enough that a single batched rating regularly overruns a 60s
# read timeout. Give it more headroom and retry once on a network timeout (but
# NOT on an HTTP error like 401 — a bad key should fail fast, not retry).
GLM_TIMEOUT = int(os.environ.get("GLM_TIMEOUT", "120"))
GLM_ATTEMPTS = 2
OUT_PATH = os.path.join(config.BASE_DIR, "news_glm_ratings.json")
VALID = {"highly_positive", "positive", "neutral", "negative", "highly_negative"}


def _collect_headlines(limit_per_sym=4):
    """Return {SYM: [title, ...]} for every symbol with credible fresh headlines.
    Reuses news_feed.raw_headlines so the credibility filter matches the UI."""
    out = {}
    for sym in config.STOCKS:
        items = news_feed.raw_headlines(sym, limit=limit_per_sym)
        titles = [it["title"] for it in items if it.get("title")]
        if titles:
            out[sym] = titles
    return out


def _build_prompt(by_sym):
    lines = [
        "You are a Pakistan-equities news classifier. For EACH symbol below, "
        "read the headlines and return ONE rating from this exact set: "
        "highly_positive, positive, neutral, negative, highly_negative. "
        "Judge only the DIRECT impact on the listed company's share price on "
        "the next PSX session. Ignore generic macro chatter unless it clearly "
        "hits this stock. Return ONLY valid JSON, no prose, exactly this shape:",
        '{"SYMBOL": {"rating": "positive", "reason": "one short clause"}, ...}',
        "",
        "SYMBOLS AND HEADLINES:",
    ]
    for sym, titles in by_sym.items():
        lines.append(f"\n{sym}:")
        for t in titles:
            lines.append(f"- {t}")
    return "\n".join(lines)


def _call_glm(prompt, api_key):
    last_err = None
    for attempt in range(1, GLM_ATTEMPTS + 1):
        try:
            r = requests.post(
                GLM_ENDPOINT,
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json={"model": GLM_MODEL,
                      "messages": [{"role": "user", "content": prompt}],
                      "temperature": 0.1,
                      "response_format": {"type": "json_object"}},
                timeout=GLM_TIMEOUT,
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = e
            log.warning("GLM attempt %d/%d network-failed: %s",
                        attempt, GLM_ATTEMPTS, e)
    raise last_err


def _sanitize(raw, expected_syms):
    """Keep only entries with a known symbol and a valid rating."""
    out = {}
    for sym, verdict in (raw or {}).items():
        s = str(sym).upper()
        if s not in expected_syms or not isinstance(verdict, dict):
            continue
        rating = str(verdict.get("rating", "")).lower().strip().replace("-", "_")
        if rating not in VALID:
            continue
        reason = str(verdict.get("reason", ""))[:200]
        out[s] = {"rating": rating, "reason": reason}
    return out


def main():
    api_key = os.environ.get("GLM_API_KEY") or os.environ.get("ZHIPU_API_KEY")
    if not api_key:
        log.warning("GLM_API_KEY not set — skipping GLM news ratings")
        return 0

    by_sym = _collect_headlines()
    if not by_sym:
        log.info("No credible headlines to rate — skipping GLM call")
        return 0

    log.info("Rating %d symbols with %s", len(by_sym), GLM_MODEL)
    try:
        raw = _call_glm(_build_prompt(by_sym), api_key)
    except Exception as e:
        log.error("GLM call failed: %s", e)
        return 1

    ratings = _sanitize(raw, set(by_sym.keys()))
    if not ratings:
        log.warning("GLM returned no valid ratings — writing empty file")

    payload = {"as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "model": GLM_MODEL, "count": len(ratings), "ratings": ratings}
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    log.info("Wrote %d ratings -> %s", len(ratings), OUT_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
