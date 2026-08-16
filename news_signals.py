"""
news_signals.py

Free, API-key-free PSX news signal generator.

Input:
    news_raw_24h.json

Output:
    news_signals.json

The output format is intentionally compatible with news_feed.py:
{
  "as_of": "...",
  "signals": {
    "PSO": {
      "score": 0-100,
      "direction": "positive|negative|neutral",
      "materiality": "normal|material_positive|material_negative",
      "confidence": "high|medium|low",
      "summary": "...",
      "headlines": ["..."],
      "sources": ["..."]
    }
  }
}

This is a deterministic financial-news classifier. It does not pretend to
be an LLM and does not invent facts. When evidence is weak it stays neutral.
"""

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
RAW = BASE / "news_raw_24h.json"
OUT = BASE / "news_signals.json"

# Financial terms are deliberately grouped by likely market direction.
POSITIVE = {
    "profit": 2.0, "profits": 2.0, "earnings growth": 2.5,
    "revenue growth": 2.0, "growth": 1.2, "surge": 1.8,
    "rally": 1.5, "record": 1.2, "dividend": 1.8,
    "bonus shares": 1.5, "buyback": 2.0, "contract": 1.5,
    "order": 1.2, "award": 1.2, "expansion": 1.2,
    "discovery": 2.0, "reserves": 1.5, "production increase": 1.8,
    "lower costs": 1.5, "debt reduction": 1.8, "upgrade": 1.8,
    "approval": 1.0, "license": 1.0, "export": 1.2,
    "strong demand": 1.5, "higher sales": 1.5, "higher profit": 2.0,
    "cash dividend": 1.8, "interim dividend": 1.8,
}

NEGATIVE = {
    "loss": -2.2, "losses": -2.2, "decline": -1.4,
    "plunge": -2.4, "crash": -3.0, "default": -3.0,
    "downgrade": -1.8, "penalty": -1.8, "fine": -1.8,
    "lawsuit": -1.5, "fraud": -3.0, "scandal": -2.8,
    "shutdown": -2.2, "closure": -2.0, "strike": -1.2,
    "lower profit": -2.0, "earnings decline": -2.2,
    "debt burden": -1.6, "debt default": -3.0,
    "production cut": -1.8, "production decline": -1.8,
    "regulatory action": -1.5, "regulatory penalty": -2.0,
    "cash crunch": -2.5, "liquidity crisis": -3.0,
    "fire": -1.0, "accident": -1.2, "explosion": -2.0,
}

# Terms that make an otherwise positive/negative headline less reliable.
LOW_INFORMATION = {
    "meeting", "board meeting", "conference", "appointment",
    "annual general meeting", "agm", "notice", "calendar",
}

HIGH_IMPACT = {
    "default", "fraud", "scandal", "discovery", "reserves",
    "major contract", "acquisition", "merger", "dividend",
    "buyback", "shutdown", "license", "regulatory penalty",
    "earnings", "profit", "loss",
}


def clean(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def score_headline(title, summary=""):
    text = clean(title + " " + summary).lower()

    pos = 0.0
    neg = 0.0
    hits = []

    for phrase, weight in POSITIVE.items():
        if phrase in text:
            pos += weight
            hits.append(("positive", phrase, weight))

    for phrase, weight in NEGATIVE.items():
        if phrase in text:
            neg += abs(weight)
            hits.append(("negative", phrase, weight))

    # Avoid treating generic "market rises" headlines as a company signal
    # unless the article has company-specific evidence.
    informational = any(x in text for x in LOW_INFORMATION)

    raw = pos - neg
    if informational and abs(raw) < 2:
        raw *= 0.35

    # Saturate into a stable [-1, +1] range.
    polarity = max(-1.0, min(1.0, raw / 6.0))

    impact = "high" if any(
        phrase in text for phrase in HIGH_IMPACT
    ) else ("medium" if hits else "low")

    return polarity, hits, impact


def make_signal(symbol, items):
    usable = []
    for item in items:
        title = clean(item.get("title"))
        if not title:
            continue
        usable.append(item)

    if not usable:
        return {
            "score": 50.0,
            "direction": "neutral",
            "materiality": "normal",
            "confidence": "low",
            "summary": "No fresh company-specific headlines found.",
            "headlines": [],
            "sources": [],
        }

    polarities = []
    all_hits = []
    high_impact = False

    for item in usable:
        p, hits, impact = score_headline(
            item.get("title", ""), item.get("summary", "")
        )
        polarities.append(p)
        all_hits.extend(hits)
        high_impact = high_impact or impact == "high"

    # Recent headline evidence gets a small majority vote rather than allowing
    # one sensational headline to dominate the entire score.
    avg = sum(polarities) / len(polarities)

    # More headlines improve confidence, but returns diminish quickly.
    confidence = (
        "high" if len(usable) >= 5 and abs(avg) >= 0.18
        else "medium" if len(usable) >= 2 and abs(avg) >= 0.10
        else "low"
    )

    score = round(50.0 + avg * 50.0, 1)

    if score >= 58:
        direction = "positive"
    elif score <= 42:
        direction = "negative"
    else:
        direction = "neutral"

    if high_impact and direction == "positive" and score >= 65:
        materiality = "material_positive"
    elif high_impact and direction == "negative" and score <= 35:
        materiality = "material_negative"
    else:
        materiality = "normal"

    # Build a transparent explanation from the strongest detected terms.
    positives = [x[1] for x in all_hits if x[0] == "positive"]
    negatives = [x[1] for x in all_hits if x[0] == "negative"]

    if direction == "positive":
        reason = "Positive financial signals detected"
        if positives:
            reason += ": " + ", ".join(positives[:3])
    elif direction == "negative":
        reason = "Negative financial signals detected"
        if negatives:
            reason += ": " + ", ".join(negatives[:3])
    else:
        reason = "Mixed or insufficient financial evidence; treated as neutral."

    headlines = [clean(x.get("title")) for x in usable[:8]]
    sources = []
    for x in usable:
        url = clean(x.get("url"))
        if url and url not in sources:
            sources.append(url)
        if len(sources) >= 8:
            break

    return {
        "score": score,
        "direction": direction,
        "materiality": materiality,
        "confidence": confidence,
        "summary": reason[:200],
        "headlines": headlines,
        "sources": sources,
    }


def main():
    if not RAW.exists():
        raise SystemExit("news_raw_24h.json not found; run news_fetcher.py first.")

    raw = json.loads(RAW.read_text(encoding="utf-8"))
    items = raw.get("items") or []

    by_symbol = defaultdict(list)
    for item in items:
        symbol = clean(item.get("symbol")).upper()
        if symbol:
            by_symbol[symbol].append(item)

    signals = {}
    for symbol, symbol_items in sorted(by_symbol.items()):
        signals[symbol] = make_signal(symbol, symbol_items)

    # Include every symbol represented by the raw feed. Do not invent signals
    # for stocks for which the feed returned no item.
    payload = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "source": "news_raw_24h.json",
        "model": "local-financial-rule-engine-v1",
        "api_required": False,
        "signals": signals,
    }

    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"News signals generated: {len(signals)} symbols")
    print(f"Output: {OUT}")
    print(f"as_of: {payload['as_of']}")


if __name__ == "__main__":
    main()
