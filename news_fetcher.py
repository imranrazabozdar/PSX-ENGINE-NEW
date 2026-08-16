"""news_fetcher.py — Fetch raw last-24h headlines for every symbol in
config.STOCKS plus the macro Pakistan-press feeds. Writes news_raw_24h.json.

Triggered by the daily 'Run the repo news' workflow (.github/workflows/news.yml).
This module ONLY collects raw headlines — it does NOT judge them. Claude reads
the raw JSON, applies the news_routine.md rules, and writes news_signals.json
(the file the engine actually consumes via news_feed.py).

Split exists because the sandbox where Claude runs can't reach Pakistani news
hosts (403 host_not_allowed); GitHub Actions can. So fetch runs in CI, judgment
runs in the session.
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

import requests

import config

log = logging.getLogger("news_fetcher")

# Default daily window is 24h (the routine's standing spec). Override via env
# NEWS_WINDOW_HOURS for one-off backfills (e.g. 96 for a 4-day rebuild).
WINDOW_HOURS = int(os.environ.get("NEWS_WINDOW_HOURS") or "24")  # "" (cron) → 24
TIMEOUT = 15
UA = {"User-Agent": "Mozilla/5.0 (psx-engine news-routine; +github)"}

# Macro feeds — used to surface market-wide stories not tied to a single stock.
MACRO_FEEDS = [
    ("Business Recorder", "https://www.brecorder.com/feeds/latest-news"),
    ("Dawn Business",     "https://www.dawn.com/feeds/business"),
    ("Profit Pakistan Today", "https://profit.pakistantoday.com.pk/feed/"),
]


def _parse_pub(s):
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _strip_html(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()


def _allowed(url):
    return any(host in (url or "") for host in config.NEWS_SOURCE_ALLOWLIST)


def _fetch_rss(url):
    r = requests.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return ET.fromstring(r.content)


def _items_from_rss(root, cutoff):
    """Yield (title, link, published_iso, summary) for items newer than cutoff."""
    for item in root.iter("item"):
        title = _strip_html(item.findtext("title", ""))
        link = (item.findtext("link", "") or "").strip()
        pub = _parse_pub(item.findtext("pubDate", ""))
        if not title or not link or not pub:
            continue
        if pub < cutoff:
            continue
        summary = _strip_html(item.findtext("description", ""))[:400]
        yield title, link, pub.isoformat(), summary


def fetch_for_symbol(symbol, cutoff):
    """Google News RSS query, allowlist-filtered, last-24h."""
    query = config.COMPANY_NEWS_QUERY.get(symbol, f"{symbol} PSX Pakistan")
    url = config.GOOGLE_NEWS_RSS.format(query=quote_plus(query))
    out = []
    try:
        root = _fetch_rss(url)
    except Exception as e:
        log.warning("Google News RSS failed for %s: %s", symbol, e)
        return out
    for title, link, pub_iso, summary in _items_from_rss(root, cutoff):
        if not config.headline_matches_company(symbol, title, summary):
            # Google News token-matches the query loosely (a "National Foods"
            # story matches the "National Refinery" query); require the headline
            # to actually name this company before attributing it to the symbol.
            continue
        if not _allowed(link) and not _allowed(summary):
            # Google News wraps the source URL inside the description; if neither
            # the link nor the description names an allowlisted host, skip.
            continue
        out.append({"symbol": symbol, "title": title, "url": link,
                    "published": pub_iso, "summary": summary,
                    "source": "google_news_rss"})
    return out


def fetch_macro(cutoff):
    out = []
    for name, url in MACRO_FEEDS:
        try:
            root = _fetch_rss(url)
        except Exception as e:
            log.warning("macro feed %s failed: %s", name, e)
            continue
        for title, link, pub_iso, summary in _items_from_rss(root, cutoff):
            out.append({"symbol": "_macro", "title": title, "url": link,
                        "published": pub_iso, "summary": summary,
                        "source": name})
    return out


def run(output_path="news_raw_24h.json"):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    items = []
    for sym in config.STOCKS:
        items.extend(fetch_for_symbol(sym, cutoff))
    items.extend(fetch_macro(cutoff))
    items.sort(key=lambda x: x["published"], reverse=True)
    payload = {"fetched_at": now.isoformat(),
               "window_hours": WINDOW_HOURS,
               "cutoff": cutoff.isoformat(),
               "universe": config.STOCKS,
               "allowlist": config.NEWS_SOURCE_ALLOWLIST,
               "count": len(items),
               "items": items}
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    log.info("Wrote %s with %d items", output_path, len(items))
    return payload


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run()
