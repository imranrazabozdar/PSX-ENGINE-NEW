"""excel_export.py — Export the latest run's ranking and signals to .xlsx.

Produces a single 'Signals' sheet ordered by final score (shariah-verified
stocks only), suitable for emailing or opening on a phone. Uses openpyxl.
"""

import os
import logging
from datetime import datetime

import pandas as pd

import config

log = logging.getLogger("excel_export")


def _row(rank, r):
    t = r["technical"]
    s = r["scoring"]
    sig = r["signal"]
    risk = r["risk"]
    return {
        "Rank": rank,
        "Symbol": r["symbol"],
        "Shariah": r["shariah"]["status"].split("(")[0].strip(),
        "Final": s["final_score"],
        "Macro": s["breakdown"]["macro_news"],
        "Sentiment": s["breakdown"]["sentiment"],
        "Technical": s["breakdown"]["technical"],
        "Price": t.get("price"),
        "Support": t.get("support"),
        "Resistance": t.get("resistance"),
        "Stop": t.get("stop_loss"),
        "Target1": t.get("target1"),
        "Target2": t.get("target2"),
        "Risk": risk["risk_level"],
        "Signal": sig["signal"],
        "Confidence%": sig["confidence"],
        "Why": "; ".join(sig.get("reasons", []))[:300],
        "Main risk": (risk["warnings"][0] if risk.get("warnings") else "")[:300],
    }


def export(results, path=None):
    """Write the ranked results to an .xlsx and return its path."""
    ranked = sorted([r for r in results if r["shariah"]["eligible_for_ranking"]],
                    key=lambda r: r["scoring"]["final_score"], reverse=True)
    df = pd.DataFrame([_row(i, r) for i, r in enumerate(ranked, 1)])
    os.makedirs(config.EXCEL_DIR, exist_ok=True)
    path = path or os.path.join(
        config.EXCEL_DIR, f"psx_signals_{datetime.now():%Y%m%d_%H%M}.xlsx")
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        df.to_excel(xl, index=False, sheet_name="Signals")
    log.info("Excel written: %s", path)
    return path
