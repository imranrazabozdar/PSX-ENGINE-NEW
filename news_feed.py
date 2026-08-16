"""news_feed.py — Reads the authentic news feed produced by the daily Claude
news routine (news_signals.json) and exposes per-symbol verdicts to the engine.

The routine (see news_routine.md) reads real articles from an allowlist of
authentic PSX/financial sources, judges each stock's news, and writes a verdict
WITH source URLs. This module only READS that file — it never fetches news
itself, so the 15-min engine loop stays fast and offline-safe.

Freshness contract: if the file is missing, malformed, or older than
config.NEWS_SIGNALS_MAX_AGE_HOURS, get() returns None for every symbol and the
caller falls back to RSS/VADER scoring. Authentic-but-absent is never faked.

Per-symbol verdict schema (values the engine relies on):
    score        float 0-100  (50 = neutral; >50 positive, <50 negative)
    direction    "positive" | "negative" | "neutral"
    materiality  "normal" | "material_negative" | "material_positive"
    confidence   "high" | "medium" | "low"
    summary      str   one-line plain-English reason
    headlines    list[str]
    sources      list[str]  (URLs — authenticity is traceable, not asserted)
"""

import json
import logging
import os
from datetime import datetime, timezone

import config

log = logging.getLogger("news_feed")

_CACHE = {"mtime": None, "data": None}


def _parse_as_of(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def load_signals():
    """Return (signals_dict, meta) where signals_dict maps SYMBOL -> verdict.
    Returns ({}, meta) when the file is missing, malformed, or stale."""
    path = config.NEWS_SIGNALS_PATH
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {}, {"status": "absent"}
    except (json.JSONDecodeError, OSError) as e:
        log.warning("news_signals.json unreadable (%s) — using RSS/VADER fallback", e)
        return {}, {"status": "malformed"}

    as_of = _parse_as_of(raw.get("as_of"))
    if as_of is not None:
        age_h = (datetime.now(timezone.utc) - as_of).total_seconds() / 3600
        if age_h > config.NEWS_SIGNALS_MAX_AGE_HOURS:
            log.warning("news_signals.json is %.1fh old (> %dh) — RSS/VADER fallback",
                        age_h, config.NEWS_SIGNALS_MAX_AGE_HOURS)
            return {}, {"status": "stale", "age_hours": round(age_h, 1)}

    signals = {k.upper(): v for k, v in (raw.get("signals") or {}).items()}
    return signals, {"status": "ok", "as_of": raw.get("as_of"),
                     "count": len(signals)}


def get(symbol):
    """Per-symbol authentic verdict dict, or None if unavailable/stale."""
    signals, _ = load_signals()
    return signals.get(symbol.upper())


def status_line():
    """One-line health string for reports/logs."""
    _, meta = load_signals()
    if meta["status"] == "ok":
        return f"Authentic news feed: {meta['count']} symbols, as of {meta['as_of']}."
    return f"Authentic news feed unavailable ({meta['status']}) — RSS/VADER fallback."


# --------------------------------------------------------------------------
# RAW headline window (UNSCORED). Reads news_raw_24h.json — the auto-fetched
# last-24h headlines that news.yml collects on a schedule (no manual routine,
# no LLM judgment). Used purely to SHOW real, source-linked headlines per
# symbol so the user can cross-verify by eye. Never feeds the score.
# --------------------------------------------------------------------------
_RAW_CACHE = {"mtime": None, "data": None}


def _publisher(item):
    """Best-effort clean publisher name. Google News titles arrive as
    'Headline - Business Recorder'; prefer the explicit macro `source`, else
    the suffix after the last ' - '."""
    src = (item.get("source") or "").strip()
    if src and src != "google_news_rss":
        return src
    title = item.get("title") or ""
    if " - " in title:
        return title.rsplit(" - ", 1)[1].strip()
    return "source"


def _clean_title(item):
    title = item.get("title") or ""
    return title.rsplit(" - ", 1)[0].strip() if " - " in title else title


def load_raw():
    """Return (payload, meta). Empty when the raw file is missing/malformed."""
    path = getattr(config, "NEWS_RAW_PATH", None)
    if not path:
        import os
        path = os.path.join(config.BASE_DIR, "news_raw_24h.json")
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}, {"status": "absent"}
    fetched = _parse_as_of(raw.get("fetched_at"))
    age_h = None
    if fetched is not None:
        age_h = round((datetime.now(timezone.utc) - fetched).total_seconds() / 3600, 1)
    return raw, {"status": "ok", "fetched_at": raw.get("fetched_at"),
                 "age_hours": age_h, "count": raw.get("count", 0)}


def raw_headlines(symbol, limit=5):
    """List of {title, url, publisher, published} for this symbol's last-24h
    headlines (deduped by cleaned title). Empty list if none / file absent.
    UNSCORED — for manual cross-verification only."""
    raw, meta = load_raw()
    if meta["status"] != "ok":
        return []
    credible = [p.lower() for p in getattr(config, "NEWS_DISPLAY_PUBLISHERS", [])]
    sym = symbol.upper()
    out, seen = [], set()
    for it in raw.get("items", []):
        if (it.get("symbol") or "").upper() != sym:
            continue
        # Relevance gate: drop loosely-matched headlines that don't actually name
        # this company (cleans raw files fetched before the fetch-time gate too).
        if not config.headline_matches_company(sym, it.get("title"), it.get("summary")):
            continue
        t = _clean_title(it)
        key = t.lower()
        if not t or key in seen:
            continue
        pub = _publisher(it)
        # Display filter: only credible desks (the fetch-time host allowlist is
        # bypassed by Google News redirect links). Skip if no allowlist set.
        if credible and not any(c in pub.lower() for c in credible):
            continue
        seen.add(key)
        out.append({"title": t, "url": it.get("url", ""),
                    "publisher": pub, "published": it.get("published")})
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------
# GLM ratings (news_glm_ratings.json) — a SECOND OPINION from GLM-4.5-flash
# on the last-24h headlines. Zero score weight; shown next to the engine's
# signal so the user can see whether the LLM agrees. Missing/stale file →
# returns None for every symbol.
# --------------------------------------------------------------------------
def load_glm_ratings():
    path = os.path.join(config.BASE_DIR, "news_glm_ratings.json")
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}, {"status": "absent"}
    as_of = _parse_as_of(raw.get("as_of"))
    age_h = None
    if as_of is not None:
        age_h = round((datetime.now(timezone.utc) - as_of).total_seconds() / 3600, 1)
        if age_h > config.NEWS_SIGNALS_MAX_AGE_HOURS:
            return {}, {"status": "stale", "age_hours": age_h}
    ratings = {k.upper(): v for k, v in (raw.get("ratings") or {}).items()}
    return ratings, {"status": "ok", "as_of": raw.get("as_of"),
                     "age_hours": age_h, "count": len(ratings),
                     "model": raw.get("model")}


def glm_rating(symbol):
    """Per-symbol GLM rating dict {rating, reason} or None."""
    ratings, _ = load_glm_ratings()
    return ratings.get(symbol.upper())


def glm_status_line():
    _, meta = load_glm_ratings()
    if meta["status"] != "ok":
        return f"GLM news rating unavailable ({meta['status']})."
    age = meta.get("age_hours")
    age_s = f"{age:.1f}h old" if age is not None else "age unknown"
    return (f"GLM news rating ({meta.get('model') or 'glm'}): "
            f"{meta['count']} symbols, {age_s} — second opinion, unweighted.")


def raw_status_line():
    _, meta = load_raw()
    if meta["status"] != "ok":
        return "Raw news window unavailable (not fetched yet)."
    age = meta.get("age_hours")
    age_s = f"{age:.1f}h old" if age is not None else "age unknown"
    return f"Raw news window: {meta['count']} headlines, {age_s} (unscored)."
