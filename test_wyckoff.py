#!/usr/bin/env python3
"""Validate psx_wyckoff against synthetic bars built as EXPLICIT price paths
with a known Wyckoff structure. Levels are hard-coded so the detector's
answers can be checked against ground truth, not against a random walk."""
import numpy as np
import pandas as pd
import psx_wyckoff as W

rng = np.random.default_rng(11)
BASE_VOL = 1_000_000


def mk(close, low, high, vmult=1.0, op=None):
    return [op if op is not None else (low + high) / 2, high, low, close,
            BASE_VOL * vmult * rng.uniform(0.92, 1.08)]


def frame(rows):
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="B")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"],
                        index=idx)


def osc(rows, lo, hi, n, vlo=0.7, vhi=1.2):
    """Phase B oscillation between two levels, no directional bias."""
    for _ in range(n):
        c = rng.uniform(lo + 1, hi - 1)
        sp = rng.uniform(0.8, 1.8)
        rows.append(mk(c, c - sp * rng.uniform(.3, .7), c + sp * rng.uniform(.3, .7),
                       rng.uniform(vlo, vhi)))


def accumulation():
    """Markdown -> SC -> AR -> ST -> Phase B -> Spring+test -> SOS -> LPS.
    Range support ~= 66, resistance ~= 78."""
    r = []
    for c in np.linspace(100, 71, 40):                  # markdown
        r.append(mk(c, c - 1.2, c + 0.8, rng.uniform(0.9, 1.4)))
    r.append(mk(68.5, 64.0, 71.5, 4.4))                 # SELLING CLIMAX
    for c in [70, 72.5, 74.5, 76.5, 78.0, 77.5]:        # AUTOMATIC RALLY
        r.append(mk(c, c - 1.5, c + 1.2, 1.15))
    for c in [74, 71, 68.5, 67.0, 66.4]:                # ST back to support
        r.append(mk(c, c - 1.0, c + 0.7, 0.55))
    osc(r, 66, 78, 36)                                  # PHASE B
    for c in [70, 68.5, 67.2]:                          # drift into support
        r.append(mk(c, c - 0.8, c + 0.5, 0.75))
    r.append(mk(67.4, 63.2, 67.9, 0.85))                # SPRING (light volume)
    r.append(mk(69.5, 67.0, 70.0, 1.05))                # closes back inside
    for c in [70.5, 71.2, 70.8]:
        r.append(mk(c, c - 0.8, c + 0.6, 0.75))
    r.append(mk(68.2, 65.9, 68.8, 0.45))                # TEST: higher low, less vol
    for c in [70, 72]:
        r.append(mk(c, c - 0.9, c + 0.7, 0.8))
    r.append(mk(80.5, 72.5, 81.0, 2.7))                 # SOS - jumps the creek
    r.append(mk(84.0, 80.0, 84.6, 2.2))
    for c in [82.5, 81.0, 80.2, 80.6]:                  # LPS on drying volume
        r.append(mk(c, c - 0.9, c + 0.6, 0.55))
    return frame(r)


def distribution():
    """Markup -> BC -> AR -> ST -> Phase B -> UTAD -> SOW -> LPSY.
    Range support ~= 122, resistance ~= 134."""
    r = []
    for c in np.linspace(100, 129, 40):                 # markup
        r.append(mk(c, c - 0.8, c + 1.2, rng.uniform(0.9, 1.4)))
    r.append(mk(131.5, 128.5, 136.0, 4.2))              # BUYING CLIMAX
    for c in [130, 127.5, 125.5, 123.5, 122.0, 122.6]:  # AUTOMATIC REACTION
        r.append(mk(c, c - 1.2, c + 1.5, 1.15))
    for c in [126, 129, 131.5, 133.0, 133.6]:           # ST back to resistance
        r.append(mk(c, c - 0.7, c + 1.0, 0.55))
    osc(r, 122, 134, 36)                                # PHASE B
    for c in [130.5, 132.0, 133.2]:                     # narrowing into resistance
        r.append(mk(c, c - 0.5, c + 0.5, 0.65))
    r.append(mk(133.0, 132.4, 137.4, 2.4))              # UTAD: pokes up, closes low
    r.append(mk(130.5, 130.0, 133.5, 1.5))              # back inside
    for c in [129.0, 128.2]:
        r.append(mk(c, c - 0.8, c + 0.6, 0.8))
    r.append(mk(119.5, 119.0, 128.0, 2.8))              # SOW - through the ice
    r.append(mk(115.0, 114.5, 119.8, 2.3))
    for c in [117.5, 118.6, 118.0]:                     # LPSY: feeble, light volume
        r.append(mk(c, c - 0.7, c + 0.9, 0.5))
    return frame(r)


def trending():
    """Clean markup, no range at all. Detector must refuse to label events."""
    r = []
    for c in np.linspace(100, 260, 140):
        r.append(mk(c, c - 1.4, c + 1.8, rng.uniform(0.8, 1.3)))
    return frame(r)


def show(name, df, expect, full=False):
    r = W.analyse(name, df, timeframe="daily")
    print("=" * 78)
    print(f"{name}   EXPECTED: {expect}")
    print("=" * 78)
    print(r["narrative"] if full else "\n".join(r["narrative"].split("\n")[:34]))
    print()
    return r


if __name__ == "__main__":
    a = show("SYNTH-ACCUM", accumulation(), "Accumulation, Phase D/E, Spring+SOS+LPS", full=True)
    d = show("SYNTH-DISTRIB", distribution(), "Distribution, Phase D/E, UTAD+SOW")
    t = show("SYNTH-TREND", trending(), "no range -> trend-only, zero events")

    print("#" * 78, "\nASSERTIONS")
    checks = [
        ("accum: classified accumulation", "ccumulation" in a["structure"]),
        ("accum: Spring found", len(a["springs"]) > 0),
        ("accum: Spring graded Medium/High",
         any(s["probability"] != "Low" for s in a["springs"])),
        ("accum: Spring has a successful test",
         any(s.get("test") for s in a["springs"])),
        ("accum: Selling Climax found", any(e["kind"] == "SC" for e in a["events"])),
        ("accum: Automatic Rally found", any(e["kind"] == "AR" for e in a["events"])),
        ("accum: Secondary Test found", any(e["kind"] == "ST" for e in a["events"])),
        ("accum: SOS found", len(a["sos"]) > 0),
        ("accum: SOS jumped the creek", any(s.get("jumped_creek") for s in a["sos"])),
        ("accum: LPS found", a["lps"] is not None),
        ("accum: phase D or E", a["phase"] in ("D", "E")),
        ("accum: bias bullish", "ullish" in a["bias"]),
        ("accum: support near 66", abs(a["range"]["support"] - 66) < 3),
        ("accum: resistance near 78", abs(a["range"]["resistance"] - 78) < 3.5),
        ("distrib: classified distribution", "istribution" in d["structure"]),
        ("distrib: UT/UTAD found", len(d["upthrusts"]) > 0),
        ("distrib: Buying Climax found", any(e["kind"] == "BC" for e in d["events"])),
        ("distrib: SOW found", len(d["sow"]) > 0),
        ("distrib: SOW broke the ice", any(s.get("broke_ice") for s in d["sow"])),
        ("distrib: phase D or E", d["phase"] in ("D", "E")),
        ("distrib: bias bearish", "earish" in d["bias"]),
        ("trend: no range identified", t["range"] is None),
        ("trend: no events labelled", len(t["events"]) == 0),
        ("trend: no spring invented", len(t["springs"]) == 0),
    ]
    bad = 0
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        bad += 0 if ok else 1
    print(f"\n{len(checks)-bad}/{len(checks)} passed")
