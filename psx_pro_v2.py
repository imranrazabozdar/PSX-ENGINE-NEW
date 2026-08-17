#!/usr/bin/env python3
"""
psx_pro_v2.py — PSX Research Terminal 2.0.

v1 was a technical terminal. v2 adds the layers above the chart:

  WYCKOFF     a full structural read per symbol (range, phase, Spring/UTAD
              grading, cause-and-effect targets) plus a market-wide Wyckoff
              screen — psx_wyckoff.py
  CONTEXT     market-regime gate, blended 1m/3m/6m relative strength, shariah
              status, peer-relative fundamentals — psx_context.py
  RISK        per-trade veto layer with real share counts, plus BOOK-level
              heat and sector caps — psx_risk.py
  MEMORY      every verdict journalled to SQLite, graded against what price
              actually did, fed back as a confidence adjustment; positions
              ledger with live P/L — psx_memory.py
  COMPOSITE   chart + structure + fundamentals in one score, where the two
              methods ARGUE rather than average — psx_verdict.py

The original v1 routes and tabs are untouched, so nothing you already rely on
changes. Everything new sits behind the new tabs.

FEATURES
  · Left rail: live watchlist with prices + change, grouped by sector
  · Add / remove any symbol, persisted to watchlist.json
  · Click any stock -> full analysis with verdict, reasoning and trade plan
  · "SCAN SELECTED" -> ranks your chosen stocks against each other with
    written commentary explaining the ordering
  · Market scan (KSE-100 or whole market) with breadth regime
  · All commentary generated locally by psx_brain — free, instant, offline

RUN
    pip install flask psxdata pandas numpy
    python psx_pro.py
    open the printed address (works on your phone on the same WiFi)
"""

import json
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd

from flask import (Flask, jsonify, render_template_string, request,
                   send_file)

import psx_brain
import psx_context
import psx_export
import psx_live
import psx_memory
import psx_report
import psx_risk
import psx_scan
import psx_verdict
import psx_wyckoff

app = Flask(__name__)


class _NumpyJSON(app.json_provider_class):
    """numpy scalars and pandas Timestamps come out of the indicator engine all
    over the place and are not JSON serializable. Converting them here means no
    route has to remember to cast, and adding a new one cannot reintroduce the
    bug."""

    @staticmethod
    def default(o):
        import numpy as _np
        if isinstance(o, _np.integer):
            return int(o)
        if isinstance(o, _np.floating):
            f = float(o)
            return f if f == f and abs(f) != float("inf") else None
        if isinstance(o, _np.bool_):
            return bool(o)
        if isinstance(o, _np.ndarray):
            return o.tolist()
        if isinstance(o, (pd.Timestamp, datetime)):
            return o.isoformat()
        if isinstance(o, pd.Series):
            return o.tolist()
        if o is pd.NaT or (isinstance(o, float) and o != o):
            return None
        return app.json_provider_class.default(o)


app.json = _NumpyJSON(app)

APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()


@app.before_request
def _gate():
    """If APP_PASSWORD is set, require HTTP basic auth on everything."""
    if not APP_PASSWORD:
        return None                      # no password configured -> open
    if request.path in ("/manifest.json", "/icon.svg", "/sw.js", "/robots.txt"):
        return None
    auth = request.authorization
    if auth and auth.password == APP_PASSWORD:
        return None
    return app.response_class(
        "Authentication required.", 401,
        {"WWW-Authenticate": 'Basic realm="PSX Terminal"'})


@app.route("/robots.txt")
def robots():
    return app.response_class("User-agent: *\nDisallow: /\n",
                              mimetype="text/plain")

WATCH_FILE = os.environ.get("WATCH_FILE", "watchlist.json")
LISTS_FILE = os.environ.get("LISTS_FILE", "lists.json")
RANK_FILE = os.environ.get("RANK_FILE", "last_rank.json")
DEFAULT = ['TOMCL', 'PSO', 'THCCL', 'DGKC', 'LUCK', 'CHCC', 'MLCF', 'AGP', 'SEARL', 'PACE', 'SLGL', 'MUGHAL', 'KOHC', 'SSGC', 'AIRLINK', 'LOADS', 'BAFL', 'FCCL', 'NBP', 'HBL', 'BOP', 'PREMA', 'TREET', 'TBL', 'HASCOL', 'SNGP', 'FNEL', 'TRG', 'SYS', 'KEL', 'NPL', 'AKBL', 'WAVESAPP', 'PAEL', 'MDTL', 'LOTCHEM', 'IMAGE', 'ILP', 'UBL', 'BNL', 'BAHL', 'DFML', 'BML', 'MCB', 'FFC', 'MARI', 'POL', 'PPL', 'CNERGY', 'OGDC', 'HUBC', 'FCL', 'ENGROH', 'YOUW', 'FABL', 'MEBL', 'BIPL', 'SNBL', 'GAL', 'SPSL', 'ATRL', 'FATIMA', 'POWER', 'CPHL', 'PIOC', 'ZAL', 'HMB', 'BFAGRO', 'NATF', 'BGL', 'SAZEW', 'TGL', 'PIBTL', 'BECO', 'PTC', 'DSL', 'SLM', 'PASL', 'DCL', 'ASL', 'ASTL', 'WTL', 'NRL', 'FFL', 'CSIL']

STATE = {"rows": [], "status": "idle", "progress": "", "when": None}
PRICES = {}          # sym -> {price, chg, sector}
ANALYSIS = {}        # sym -> brain result
BENCH = None

SECTORS = {
    "Cement": ["LUCK", "MLCF", "DGKC", "FCCL", "CHCC", "KOHC", "PIOC", "ACPL",
               "THCCL", "GWLC", "BWCL", "FLYNG", "POWER", "JVDC"],
    "Commercial Banks": ["HBL", "MCB", "UBL", "BAFL", "AKBL", "BOP", "MEBL",
                         "NBP", "FABL", "BAHL", "SNBL", "JSBL", "SILK", "BIPL",
                         "HMB", "SCBPL", "BOK", "BGL"],
    "Oil & Gas Exploration": ["OGDC", "PPL", "POL", "MARI"],
    "Oil & Gas Marketing": ["PSO", "APL", "SHEL", "SNGP", "SSGC", "HTL", "BPL",
                            "HASCOL"],
    "Refinery": ["ATRL", "NRL", "PRL", "CNERGY", "PACE"],
    "Power Generation": ["HUBC", "KEL", "KAPCO", "NPL", "NCPL", "PKGP", "LOTCHEM",
                         "ALTN", "SPWL", "TSPL", "EPQL", "POWER"],
    "Fertiliser": ["FFC", "EFERT", "FATIMA", "ENGRO", "FFBL", "AHCL", "AGL",
                   "ENGROH", "FCL"],
    "Engineering & Steel": ["MUGHAL", "ISL", "ASTL", "INIL", "AGHA", "LOADS",
                            "ITTEFAQ", "DSL", "CSAP", "KSBP", "ASL", "BECO",
                            "PASL", "DCL", "SLM"],
    "Technology": ["SYS", "TRG", "NETSOL", "AVN", "PTC", "TPLRF1", "TELE",
                   "WTL", "OCTOPUS", "HUMNL", "CSIL"],
    "Pharmaceuticals": ["AGP", "SEARL", "GLAXO", "HINOON", "CPHL", "HALEON",
                        "FEROZ", "ABOT", "MACTER", "SAPT"],
    "Automobile": ["HCAR", "INDU", "PSMC", "MTL", "GHNI", "SAZEW", "ATLH",
                   "AGIL", "THALL", "LOADS"],
    "Textile Composite": ["NML", "NCL", "GATM", "ILP", "KTML", "GADT", "FML",
                          "STML", "CTM", "SITC"],
    "Transport & Logistics": ["SLGL", "GDL", "PIBTL", "TOMCL", "PICT", "AIRLINK"],
    "Food & Personal Care": ["NESTLE", "UPFL", "FFL", "BFAGRO", "MFL", "AICL",
                             "QUICE", "SHEZ", "TREET", "PREMA", "FNEL", "NATF",
                             "ZAL", "FFL"],
    "Chemical": ["ICI", "LOTCHEM", "EPCL", "SITARA", "ARPL", "DYNO", "NRSL",
                 "BERGER", "GGL"],
    "Insurance": ["AICL", "IGIHL", "JLICL", "EFUG", "TPLI", "PAKRI"],
    "Paper & Board": ["PKGS", "CPPL", "MERIT", "SEPL"],
    "Miscellaneous": ["GAL", "SRVI", "PAEL", "SIEM", "MIRKS", "GHGL", "TGL",
                      "IMAGE", "WAVESAPP", "MDTL", "BNL", "BML", "DFML", "TBL",
                      "SPSL", "YOUW", "SAZEW"],
}
SECTOR_OF = {s: k for k, v in SECTORS.items() for s in v}

# Filled at runtime from PSX itself — always current, never guessed.
ALL_TICKERS = []
SECTOR_MAP_LIVE = {}
UNIVERSE_SOURCE = "not loaded"
UNIVERSE_ERR = None

CAPITAL = float(os.environ.get("CAPITAL", 1_000_000))
REGIME_CACHE = {"when": None, "data": None}
WYCK_SCAN = {"rows": [], "status": "idle", "progress": "", "when": None,
             "done": 0, "total": 0}

AUTO = {"on": False, "every": 15, "last": None, "next": None, "runs": 0,
        "mode": "watchlist", "partial": "drop"}   # "watchlist" (light) or "kse100" (heavier)
WATCH_RANK = {"ranked": [], "commentary": "", "when": None,
              "status": "idle", "progress": "", "done": 0, "total": 0}


def _load_rank():
    """Restore the last ranking so a restart doesn't force a full rescan."""
    try:
        if os.path.exists(RANK_FILE):
            d = json.load(open(RANK_FILE))
            if d.get("ranked"):
                WATCH_RANK.update(ranked=d["ranked"],
                                  commentary=d.get("commentary", ""),
                                  when=d.get("when"), status="done",
                                  progress="restored from last run")
    except Exception:
        pass


def _save_rank():
    try:
        json.dump({"ranked": WATCH_RANK["ranked"],
                   "commentary": WATCH_RANK["commentary"],
                   "when": WATCH_RANK["when"]}, open(RANK_FILE, "w"))
    except Exception:
        pass


def load_universe(force=False):
    """
    Pull the FULL list of PSX-listed symbols (and sectors where PSX exposes
    them) straight from the exchange. Reports WHY it fell back, if it does.
    """
    global ALL_TICKERS, SECTOR_MAP_LIVE, UNIVERSE_SOURCE, UNIVERSE_ERR
    if ALL_TICKERS and not force:
        return ALL_TICKERS
    def _direct_symbols():
        """Fetch the symbol list ourselves with browser-like headers.
        Some servers reject library default user-agents."""
        import json as _j
        import urllib.request
        req = urllib.request.Request(
            "https://dps.psx.com.pk/symbols",
            headers={"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                                    "Chrome/126.0.0.0 Safari/537.36"),
                     "Accept": "application/json, text/plain, */*",
                     "Accept-Language": "en-US,en;q=0.9",
                     "Referer": "https://dps.psx.com.pk/"})
        with urllib.request.urlopen(req, timeout=25) as r:
            data = _j.loads(r.read().decode("utf-8", "ignore"))
        out = []
        if isinstance(data, list):
            for row in data:
                if isinstance(row, dict):
                    sym = row.get("symbol") or row.get("Symbol") or row.get("code")
                    sec = row.get("sectorName") or row.get("sector") or row.get("Sector")
                    if sym:
                        sym = str(sym).strip().upper()
                        out.append(sym)
                        if sec:
                            SECTOR_MAP_LIVE[sym] = str(sec).strip()
                elif isinstance(row, str):
                    out.append(row.strip().upper())
        return out

    try:
        import psxdata
        try:
            t = psxdata.tickers()
        except Exception as lib_err:
            direct = _direct_symbols()          # try our own fetch before giving up
            if len(direct) >= 50:
                ALL_TICKERS = sorted(set(direct))
                UNIVERSE_SOURCE, UNIVERSE_ERR = "live (direct)", None
                return ALL_TICKERS
            raise lib_err

        syms = []
        if isinstance(t, pd.DataFrame):
            cols = list(t.columns)
            low = [str(c).lower() for c in cols]
            # find the symbol column
            scol = None
            for key in ("symbol", "ticker", "scrip", "code", "company symbol"):
                for i, c in enumerate(low):
                    if key in c:
                        scol = cols[i]
                        break
                if scol:
                    break
            if scol is None:
                # fall back: pick the column that looks most like tickers
                best, bestn = cols[0], -1
                for c in cols:
                    v = t[c].astype(str).str.strip()
                    n = int(v.str.fullmatch(r"[A-Z0-9]{2,12}").sum())
                    if n > bestn:
                        best, bestn = c, n
                scol = best
            syms = [str(x).strip().upper() for x in t[scol].dropna()]

            seccol = next((cols[i] for i, c in enumerate(low)
                           if "sector" in c or "industry" in c), None)
            if seccol:
                for _, r in t.iterrows():
                    sym = str(r[scol]).strip().upper()
                    sec = str(r[seccol]).strip()
                    if sym and sec and sec.lower() not in ("nan", "none", ""):
                        SECTOR_MAP_LIVE[sym] = sec
        elif isinstance(t, pd.Series):
            syms = [str(x).strip().upper() for x in t.dropna()]
        elif isinstance(t, dict):
            syms = [str(x).strip().upper() for x in t.keys()]
        else:
            syms = [str(x).strip().upper() for x in t]

        # keep only plausible tickers
        syms = [s for s in syms if s and 1 < len(s) <= 12 and s.replace(".", "").isalnum()]
        syms = sorted(set(syms))

        if len(syms) < 50:
            raise ValueError(f"only {len(syms)} tickers parsed — unexpected shape "
                             f"({type(t).__name__}, cols="
                             f"{list(t.columns)[:6] if isinstance(t, pd.DataFrame) else 'n/a'})")

        # enrich sectors from sectors() if tickers() had none
        if not SECTOR_MAP_LIVE:
            try:
                sdf = psxdata.sectors()
                if isinstance(sdf, pd.DataFrame):
                    sc = list(sdf.columns)
                    sl = [str(c).lower() for c in sc]
                    sy = next((sc[i] for i, c in enumerate(sl)
                               if "symbol" in c or "ticker" in c), None)
                    se = next((sc[i] for i, c in enumerate(sl)
                               if "sector" in c or "name" in c), None)
                    if sy and se:
                        for _, r in sdf.iterrows():
                            SECTOR_MAP_LIVE[str(r[sy]).strip().upper()] = str(r[se]).strip()
            except Exception:
                pass

        ALL_TICKERS = syms
        UNIVERSE_SOURCE, UNIVERSE_ERR = "live", None
    except Exception as e:
        ALL_TICKERS = sorted(SECTOR_OF.keys())
        UNIVERSE_SOURCE, UNIVERSE_ERR = "fallback", str(e)
    return ALL_TICKERS


_LIVE_SECTORS = {"map": None, "at": 0}


def sector_for(sym):
    # prefer real exchange sectors from the live feed
    if time.time() - _LIVE_SECTORS["at"] > 3600:
        try:
            m = psx_live.sector_map()
            if m:
                _LIVE_SECTORS["map"] = m
        except Exception:
            pass
        _LIVE_SECTORS["at"] = time.time()
    live = (_LIVE_SECTORS["map"] or {}).get(sym.upper())
    return live or SECTOR_MAP_LIVE.get(sym) or SECTOR_OF.get(sym) or "Other"


def load_lists():
    if os.path.exists(LISTS_FILE):
        try:
            d = json.load(open(LISTS_FILE))
            if isinstance(d, dict) and d:
                return d
        except Exception:
            pass
    return {"Main": list(DEFAULT)}


def save_lists():
    json.dump(LISTS, open(LISTS_FILE, "w"))


LISTS = load_lists()
CURRENT = list(LISTS.keys())[0]


class _Watch(list):
    """Always mirrors the currently-selected named list."""
    def sync(self):
        self[:] = LISTS.get(CURRENT, [])
        return self


WATCH = _Watch(LISTS.get(CURRENT, []))
_load_rank()


def save_watch(lst):
    LISTS[CURRENT] = list(lst)
    save_lists()


def get_bench():
    global BENCH
    if BENCH is None:
        try:
            import psxdata
            k = psxdata.indices("KSE100")
            BENCH = k.close if hasattr(k, "close") else None
        except Exception:
            BENCH = False
    return BENCH or None


def quote(sym):
    """Latest price + % change, cached briefly."""
    try:
        df = psx_report.load_from_psx(sym, 1)
        last, prev = df.close.iloc[-1], df.close.iloc[-2]
        return {"sym": sym, "price": round(float(last), 2),
                "chg": round(float((last / prev - 1) * 100), 2),
                "sector": sector_for(sym)}
    except Exception:
        return {"sym": sym, "price": None, "chg": None,
                "sector": sector_for(sym)}


def market_open():
    """PSX trades Mon-Fri, roughly 09:15-15:30 PKT (UTC+5)."""
    from datetime import timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=5)))
    if now.weekday() > 4:
        return False
    mins = now.hour * 60 + now.minute
    return 9 * 60 + 15 <= mins <= 15 * 60 + 30


def scan_watchlist():
    """
    Rank the user's own list. Runs in a BACKGROUND thread so it can never
    hit the web-server request timeout — the UI polls for progress.
    """
    syms = list(WATCH)
    WATCH_RANK.update(status="running", done=0, total=len(syms),
                      progress=f"0/{len(syms)}")
    t0 = time.time()

    def work(sym):
        try:
            return psx_brain.analyse(sym, psx_report.load_from_psx(sym, 3),
                                     get_bench(), AUTO.get("partial", "drop"))
        except Exception:
            return None

    res = []
    n = int(os.environ.get("SCAN_WORKERS", "6"))
    n = max(2, min(n, max(2, len(syms))))
    try:
        with ThreadPoolExecutor(max_workers=n) as ex:
            for r in ex.map(work, syms):
                WATCH_RANK["done"] += 1
                WATCH_RANK["progress"] = (f"{WATCH_RANK['done']}/{len(syms)} · "
                                          f"{time.time()-t0:.0f}s")
                if r:
                    res.append(r)
        out = psx_brain.compare(res)
        WATCH_RANK["ranked"] = [{k: v for k, v in r.items() if k != "report"}
                                for r in out["ranked"]]
        WATCH_RANK["commentary"] = out["commentary"]
        WATCH_RANK["when"] = datetime.now().strftime("%d %b %H:%M:%S")
        WATCH_RANK["status"] = "done"
        WATCH_RANK["progress"] = (f"{len(res)} ranked in {time.time()-t0:.0f}s")
        _save_rank()
    except Exception as e:
        WATCH_RANK["status"] = "error"
        WATCH_RANK["progress"] = str(e)[:200]
    return WATCH_RANK


def auto_loop():
    """Background: re-scan and re-rank on a timer while the market is open."""
    while True:
        try:
            if AUTO["on"]:
                due = AUTO["next"] is None or time.time() >= AUTO["next"]
                if due and market_open() and STATE["status"] != "scanning":
                    if AUTO["mode"] == "kse100":
                        run_scan("KSE100", 0.2)
                    else:
                        scan_watchlist()
                    AUTO["last"] = datetime.now().strftime("%H:%M:%S")
                    AUTO["runs"] += 1
                    AUTO["next"] = time.time() + AUTO["every"] * 60
                elif due and not market_open():
                    # market shut: check again in 5 minutes, don't burn requests
                    AUTO["next"] = time.time() + 300
        except Exception:
            pass
        time.sleep(10)


threading.Thread(target=auto_loop, daemon=True).start()


# ---------------------------------------------------------------- routes

@app.route("/")
def home():
    return render_template_string(PAGE)


@app.route("/manifest.json")
def manifest():
    return jsonify({
        "name": "PSX Research Terminal",
        "short_name": "PSX",
        "description": "Technical scanner and ranking engine for the "
                       "Pakistan Stock Exchange",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#070a0f",
        "theme_color": "#0e131c",
        "icons": [
            {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml",
             "purpose": "any maskable"},
        ],
    })


@app.route("/icon.svg")
def icon():
    svg = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 512 512'>"
           "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
           "<stop offset='0' stop-color='#3d8bff'/>"
           "<stop offset='1' stop-color='#7c5cff'/></linearGradient></defs>"
           "<rect width='512' height='512' rx='96' fill='#0e131c'/>"
           "<path d='M96 352 L192 240 L272 304 L416 144' stroke='url(#g)' "
           "stroke-width='34' fill='none' stroke-linecap='round' "
           "stroke-linejoin='round'/>"
           "<circle cx='416' cy='144' r='26' fill='#00e07a'/>"
           "<text x='256' y='448' font-family='sans-serif' font-size='84' "
           "font-weight='700' fill='#e8edf5' text-anchor='middle'>PSX</text>"
           "</svg>")
    return app.response_class(svg, mimetype="image/svg+xml")


@app.route("/sw.js")
def service_worker():
    js = ("self.addEventListener('install',e=>self.skipWaiting());"
          "self.addEventListener('activate',e=>self.clients.claim());"
          "self.addEventListener('fetch',e=>{});")
    return app.response_class(js, mimetype="application/javascript")


@app.route("/lists")
def lists_all():
    return jsonify(ok=True, lists={k: v for k, v in LISTS.items()},
                   current=CURRENT)


@app.route("/lists/select/<name>", methods=["POST"])
def list_select(name):
    global CURRENT
    if name in LISTS:
        CURRENT = name
        WATCH.sync()
    return jsonify(ok=True, current=CURRENT, items=LISTS.get(CURRENT, []))


@app.route("/lists/create", methods=["POST"])
def list_create():
    global CURRENT
    d = request.json or {}
    name = str(d.get("name", "")).strip()[:40]
    if not name:
        return jsonify(ok=False, error="name required")
    syms = [s.upper().strip() for s in d.get("symbols", []) if s.strip()]
    LISTS[name] = syms
    CURRENT = name
    WATCH.sync()
    save_lists()
    return jsonify(ok=True, current=CURRENT, lists=LISTS)


@app.route("/lists/delete/<name>", methods=["POST"])
def list_delete(name):
    global CURRENT
    if name in LISTS and len(LISTS) > 1:
        del LISTS[name]
        CURRENT = list(LISTS.keys())[0]
        WATCH.sync()
        save_lists()
    return jsonify(ok=True, current=CURRENT, lists=LISTS)


@app.route("/watchlist")
def watchlist():
    WATCH.sync()
    # ONE request for every price, via the live feed
    snap = psx_live.market_snapshot()
    if snap:
        out = []
        for s_ in WATCH:
            d = snap.get(s_.upper()) or {}
            out.append({"sym": s_, "price": d.get("price"),
                        "chg": d.get("chg"), "sector": sector_for(s_)})
        for q in out:
            PRICES[q["sym"]] = q
        return jsonify(ok=True, items=out, sectors=SECTORS, source="live")

    # fallback: individual psxdata lookups
    with ThreadPoolExecutor(max_workers=8) as ex:
        out = list(ex.map(quote, WATCH))
    for q in out:
        PRICES[q["sym"]] = q
    return jsonify(ok=True, items=out, sectors=SECTORS)


@app.route("/watch/add/<sym>", methods=["POST"])
def watch_add(sym):
    sym = sym.upper().strip()
    if sym and sym not in WATCH:
        WATCH.append(sym)
        save_watch(WATCH)
    return jsonify(ok=True, list=WATCH)


@app.route("/watch/remove/<sym>", methods=["POST"])
def watch_remove(sym):
    sym = sym.upper().strip()
    if sym in WATCH:
        WATCH.remove(sym)
        save_watch(WATCH)
    return jsonify(ok=True, list=WATCH)


@app.route("/cache")
def cache_info():
    import glob
    d = psx_report.CACHE_DIR
    files = glob.glob(os.path.join(d, "*.csv"))
    size = sum(os.path.getsize(f) for f in files) / 1e6
    newest = max((os.path.getmtime(f) for f in files), default=0)
    return jsonify(ok=True, dir=d, symbols=len(files),
                   size_mb=round(size, 1),
                   updated=(datetime.fromtimestamp(newest).strftime("%d %b %H:%M")
                            if newest else "never"))


@app.route("/cache/clear", methods=["POST"])
def cache_clear():
    import glob
    n = 0
    for f in glob.glob(os.path.join(psx_report.CACHE_DIR, "*.csv")):
        try:
            os.remove(f); n += 1
        except Exception:
            pass
    return jsonify(ok=True, removed=n)


@app.route("/diag")
def diag():
    """Tell us WHY PSX is unreachable — status codes, headers, geo-block or not."""
    import json as _j
    out = {"where": os.environ.get("RENDER_SERVICE_NAME", "local"), "tests": []}

    def probe(url, headers=None, label=""):
        r = {"url": url, "label": label}
        try:
            import urllib.request
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read(400)
                r.update(status=resp.status, bytes=len(body),
                         sample=body[:180].decode("utf-8", "ignore"))
        except Exception as e:
            r.update(status="ERROR", error=f"{type(e).__name__}: {e}"[:200])
        out["tests"].append(r)

    BROWSER = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/126.0.0.0 Safari/537.36"),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://dps.psx.com.pk/",
    }
    probe("https://dps.psx.com.pk/symbols", None, "psx symbols, no headers")
    probe("https://dps.psx.com.pk/symbols", BROWSER, "psx symbols, browser headers")
    probe("https://dps.psx.com.pk/", BROWSER, "psx homepage")
    probe("https://www.google.com/", BROWSER, "control (is outbound net alive?)")

    try:
        import psxdata
        out["psxdata"] = getattr(psxdata, "__version__", "installed")
    except Exception as e:
        out["psxdata"] = f"import failed: {e}"

    ok = [t for t in out["tests"] if t.get("status") == 200]
    if any(t["label"].startswith("control") and t.get("status") == 200 for t in out["tests"]) \
            and not any("psx" in t["label"] and t.get("status") == 200 for t in out["tests"]):
        out["verdict"] = ("Outbound internet works but PSX refuses this server. "
                          "Almost certainly PSX geo-blocking or blocking datacentre "
                          "IPs. Hosting abroad will not fix this.")
    elif not ok:
        out["verdict"] = "No outbound network at all from this host."
    else:
        out["verdict"] = "PSX reachable — the failure is in parsing, not the network."
    return jsonify(out)


@app.route("/universe")
def universe():
    """Every listed PSX symbol, grouped by sector — pulled live from PSX."""
    syms = load_universe()
    groups = {}
    for s in syms:
        groups.setdefault(sector_for(s), []).append(s)
    for k in groups:
        groups[k] = sorted(set(groups[k]))
    return jsonify(ok=True, count=len(syms), source=UNIVERSE_SOURCE,
                   error=UNIVERSE_ERR,
                   sectors={k: groups[k] for k in sorted(groups)})


@app.route("/watch/addmany", methods=["POST"])
def watch_addmany():
    syms = [s.upper().strip() for s in (request.json or {}).get("symbols", [])]
    added = 0
    for s in syms:
        if s and s not in WATCH:
            WATCH.append(s)
            added += 1
    save_watch(WATCH)
    return jsonify(ok=True, added=added, total=len(WATCH))


@app.route("/fundamentals/<sym>")
def fundamentals(sym):
    """
    Pull the company's financial reports straight from PSX and read them.
    Uses psxdata.fundamentals(), which scrapes PSX's own company data.
    """
    sym = sym.upper()
    try:
        import psxdata
        f = psxdata.fundamentals(sym)
    except Exception as e:
        return jsonify(ok=False, error=f"could not fetch fundamentals: {e}")

    out = {"symbol": sym, "tables": [], "metrics": {}, "notes": []}

    def add_table(name, df):
        try:
            d = df.copy()
            d.columns = [str(c) for c in d.columns]
            out["tables"].append({
                "name": name,
                "columns": list(d.columns)[:10],
                "rows": d.head(40).astype(str).values.tolist(),
            })
        except Exception:
            pass

    # psxdata may return a DataFrame or a dict of DataFrames — handle both
    if isinstance(f, dict):
        for k, v in f.items():
            if isinstance(v, pd.DataFrame) and not v.empty:
                add_table(str(k), v)
    elif isinstance(f, pd.DataFrame) and not f.empty:
        add_table("Financials", f)
    else:
        return jsonify(ok=False, error="PSX returned no fundamental data for "
                                       f"{sym} (thinly covered or delisted?)")

    # try to surface headline ratios wherever they appear
    wanted = ["eps", "p/e", "pe", "book value", "p/b", "dividend", "yield",
              "market cap", "shares", "roe", "revenue", "profit", "margin",
              "debt", "equity", "free float"]
    for t in out["tables"]:
        for row in t["rows"]:
            if not row:
                continue
            label = str(row[0]).strip().lower()
            for w in wanted:
                if w in label and len(row) > 1:
                    val = next((c for c in row[1:] if c and c != "nan"), None)
                    if val:
                        out["metrics"].setdefault(str(row[0]).strip(), val)
                    break

    if not out["metrics"]:
        out["notes"].append("No standard ratio labels found — the raw tables "
                            "below are what PSX published for this company.")
    out["notes"].append("Source: PSX company data via psxdata. Always verify "
                        "against the company's own report before acting.")
    out["notes"].append(f"Full filings: https://dps.psx.com.pk/company/{sym}")
    return jsonify(ok=True, **out)


@app.route("/analyse/<sym>")
def analyse_one(sym):
    sym = sym.upper()
    try:
        df = psx_report.load_from_psx(sym, 3)
        res = psx_brain.analyse(sym, df, get_bench(), AUTO.get('partial','drop'))
        ANALYSIS[sym] = res
        res["report"] = psx_report.report(sym, df, volume=True, candles=True,
                                          monthly=True, bench=get_bench(),
                                          structure=True)
        return jsonify(ok=True, **res)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@app.route("/compare", methods=["POST"])
def compare():
    syms = [s.upper() for s in (request.json or {}).get("symbols", [])]
    if not syms:
        return jsonify(ok=False, error="no symbols selected")

    def work(s):
        try:
            return psx_brain.analyse(s, psx_report.load_from_psx(s, 3), get_bench())
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=6) as ex:
        res = [r for r in ex.map(work, syms) if r]
    out = psx_brain.compare(res)
    return jsonify(ok=True, commentary=out["commentary"],
                   ranked=[{k: v for k, v in r.items() if k != "report"}
                           for r in out["ranked"]])


def run_scan(kind, min_vol):
    STATE.update(status="scanning", progress="fetching tickers...")
    try:
        uni = sorted(set(psx_scan.get_universe(kind)))
    except Exception as e:
        STATE.update(status="error", progress=str(e))
        return
    rows, done, t0 = [], 0, time.time()
    workers = int(os.environ.get("SCAN_WORKERS", "8"))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(psx_scan.evaluate, s, 2): s for s in uni}
        for f in as_completed(futs):
            done += 1
            r = f.result()
            if r:
                rows.append(r)
            if done % 10 == 0:
                STATE["progress"] = f"{done}/{len(uni)} · {len(rows)} usable · {time.time()-t0:.0f}s"
    rows = [r for r in rows if r["avgVolM"] >= min_vol]
    rows.sort(key=lambda r: r["SCORE"], reverse=True)
    STATE.update(rows=rows, status="done",
                 when=datetime.now().strftime("%d %b %H:%M"),
                 progress=f"{len(rows)} ranked in {time.time()-t0:.0f}s")


@app.route("/scan", methods=["POST"])
def scan():
    if STATE["status"] == "scanning":
        return jsonify(ok=False)
    d = request.json or {}
    threading.Thread(target=run_scan,
                     args=(d.get("universe", "KSE100"),
                           float(d.get("min_vol", 0.1))), daemon=True).start()
    return jsonify(ok=True)


@app.route("/auto", methods=["POST"])
def auto():
    d = request.json or {}
    AUTO["on"] = bool(d.get("on"))
    AUTO["every"] = max(2, int(d.get("every", 15)))
    AUTO["mode"] = d.get("mode", AUTO.get("mode", "watchlist"))
    if "partial" in d:
        AUTO["partial"] = d["partial"]
    AUTO["next"] = time.time() if AUTO["on"] else None
    return jsonify(ok=True, **auto_state())


def auto_state():
    return {"on": AUTO["on"], "every": AUTO["every"], "last": AUTO["last"],
            "runs": AUTO["runs"], "market_open": market_open(),
            "mode": AUTO.get("mode", "watchlist"),
            "rank_when": WATCH_RANK["when"], "n_watch": len(WATCH),
            "in_sec": int(max(0, (AUTO["next"] or time.time()) - time.time()))
            if AUTO["on"] else None}


@app.route("/autostatus")
def autostatus():
    return jsonify(ok=True, **auto_state())


@app.route("/status")
def status():
    return jsonify({k: STATE[k] for k in ("status", "progress", "when", "rows")})


@app.route("/ranklist", methods=["GET", "POST"])
def ranklist():
    """
    GET  -> current state (may be running; poll until status == done)
    POST -> start a fresh ranking in the background
    """
    # GET is read-only unless we have literally nothing cached.
    start = request.method == "POST" or (not WATCH_RANK["ranked"]
                                         and WATCH_RANK["status"] == "idle"
                                         and request.args.get("auto") == "1")
    if start and WATCH_RANK["status"] != "running":
        threading.Thread(target=scan_watchlist, daemon=True).start()
        time.sleep(0.3)
    return jsonify(ok=True, **WATCH_RANK)


def _export_symbols():
    q = request.args.get("syms", "").strip()
    if q:
        return [x for x in q.replace(",", " ").split() if x]
    return list(WATCH)


@app.route("/export/excel")
def export_excel():
    syms = _export_symbols()
    try:
        buf = psx_export.build_excel(syms, get_bench(), AUTO.get("partial", "drop"))
    except ImportError:
        return jsonify(ok=False, error="openpyxl not installed — run: "
                                       "pip install openpyxl"), 500
    except Exception as e:
        return jsonify(ok=False, error=str(e)[:300]), 500
    name = (syms[0] if len(syms) == 1 else f"{len(syms)}-stocks")
    return send_file(buf, as_attachment=True,
                     download_name=f"PSX_{name}_{datetime.now():%Y%m%d_%H%M}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument."
                              "spreadsheetml.sheet")


@app.route("/export/pdf")
def export_pdf():
    syms = _export_symbols()
    try:
        buf = psx_export.build_pdf(syms, get_bench(), AUTO.get("partial", "drop"))
    except ImportError:
        return jsonify(ok=False, error="reportlab not installed — run: "
                                       "pip install reportlab"), 500
    except Exception as e:
        return jsonify(ok=False, error=str(e)[:300]), 500
    name = (syms[0] if len(syms) == 1 else f"{len(syms)}-stocks")
    return send_file(buf, as_attachment=True,
                     download_name=f"PSX_{name}_{datetime.now():%Y%m%d_%H%M}.pdf",
                     mimetype="application/pdf")


@app.route("/live")
def live_tape():
    """Live tape signals for the current watchlist."""
    try:
        alerts = psx_live.tape_signals(list(WATCH))
        if alerts is None:
            return jsonify(ok=False,
                           error="Live feed unreachable — psxterminal.com not "
                                 "responding. Rankings still work from cached data.")
        return jsonify(ok=True, alerts=alerts,
                       summary=psx_live.tape_summary(alerts),
                       session=round(psx_live.session_progress() * 100),
                       market_open=market_open(),
                       when=datetime.now().strftime("%H:%M:%S"))
    except Exception as e:
        return jsonify(ok=False, error=str(e)[:200])


@app.route("/liveprices")
def live_prices():
    """One-request snapshot for the whole watchlist — replaces 85 scrapes."""
    snap = psx_live.market_snapshot()
    if not snap:
        return jsonify(ok=False)
    out = []
    for s_ in WATCH:
        d = snap.get(s_.upper())
        out.append({"sym": s_, "price": (d or {}).get("price"),
                    "chg": (d or {}).get("chg"),
                    "sector": sector_for(s_)})
    return jsonify(ok=True, items=out, when=datetime.now().strftime("%H:%M:%S"))


@app.route("/livebreadth")
def live_breadth():
    txt = psx_live.breadth_report()
    return jsonify(ok=bool(txt), text=txt or "Live breadth unavailable.")


@app.route("/breadth")
def breadth():
    live = psx_live.breadth_report()
    own = psx_report.market_breadth(STATE["rows"])
    if live:
        return jsonify(ok=True, text=live + "\n\n" + own)
    return jsonify(ok=True, text=own)



# ==========================================================================
# v2.0 — Wyckoff, context, risk, memory
# ==========================================================================

def _peers(sym):
    """Sector peers, so fundamentals are judged against comparable companies."""
    sec = sector_for(sym)
    return [s for s in SECTORS.get(sec, []) if s != sym][:14]


def regime_now(force=False):
    """Cached market regime — recomputed at most every 10 minutes."""
    now = time.time()
    if not force and REGIME_CACHE["data"] and REGIME_CACHE["when"] \
            and now - REGIME_CACHE["when"] < 600:
        return REGIME_CACHE["data"]
    r = psx_context.assess_regime(get_bench())
    REGIME_CACHE.update(when=now, data=r)
    return r


def _live_price(sym):
    snap = psx_live.market_snapshot(ttl=60) or {}
    d = snap.get(sym.upper())
    if d and d.get("price"):
        return float(d["price"])
    try:
        return float(psx_report.load_from_psx(sym, 1).close.iloc[-1])
    except Exception:
        return None


@app.route("/v2/analyse/<sym>")
def v2_analyse(sym):
    """Composite read: chart + Wyckoff + context + risk, and journal it."""
    sym = sym.upper()
    weekly = request.args.get("weekly", "0") == "1"
    try:
        df = psx_report.load_from_psx(sym, 3)
        r = psx_verdict.analyse(sym, daily=df, bench=get_bench(),
                                partial=AUTO.get("partial", "drop"),
                                sector_peers=_peers(sym), regime=regime_now(),
                                holdings=psx_memory.holdings(), capital=CAPITAL,
                                with_wyckoff=True, weekly_wyckoff=weekly,
                                memory=psx_memory)
    except Exception as e:
        return jsonify(ok=False, error=str(e)[:300])
    try:
        w = r.get("wyckoff") or {}
        psx_memory.record(r, w, r.get("context"), r.get("risk"))
        psx_memory.features_seen(sym, {
            "spring": bool(w.get("springs")),
            "sos": bool(w.get("sos")),
            "lps": bool(w.get("lps")),
            "phase_d": w.get("phase") == "D",
            "daily_trend_up": r["state"]["dTrend"] == "UP",
            "above_cloud": r["state"]["cloud"] == "above",
            "weekly_accum": (r["state"]["wVol"] or 0) >= 3,
        })
    except Exception:
        pass
    r["sector"] = sector_for(sym)
    return jsonify(ok=True, **r)


@app.route("/wyckoff/<sym>")
def wyckoff_one(sym):
    """Standalone Wyckoff read, daily and weekly side by side."""
    sym = sym.upper()
    try:
        df = psx_report.load_from_psx(sym, 3)
        b = get_bench()
        d = psx_wyckoff.analyse(sym, df, "daily", b)
        w = psx_wyckoff.analyse(sym, psx_report.to_weekly(df), "weekly", b)
    except Exception as e:
        return jsonify(ok=False, error=str(e)[:300])
    return jsonify(ok=True, symbol=sym, sector=sector_for(sym),
                   daily=d, weekly=w,
                   agree=(d.get("structure") == w.get("structure")))


def run_wyckoff_scan(symbols, tf="daily"):
    """Screen a list of symbols for Wyckoff structures, in the background."""
    WYCK_SCAN.update(status="running", rows=[], progress="starting…",
                     done=0, total=len(symbols))
    b = get_bench()
    rows = []

    def one(sym):
        try:
            df = psx_report.load_from_psx(sym, 3)
            if tf == "weekly":
                df = psx_report.to_weekly(df)
            return psx_wyckoff.summarise(psx_wyckoff.analyse(sym, df, tf, b))
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(one, s): s for s in symbols}
        for fut in as_completed(futs):
            WYCK_SCAN["done"] += 1
            r = fut.result()
            if r and not r.get("error"):
                rows.append(r)
            if WYCK_SCAN["done"] % 5 == 0:
                WYCK_SCAN["progress"] = (f"{WYCK_SCAN['done']}/{WYCK_SCAN['total']} "
                                         f"· {len(rows)} with usable structure")
    rows.sort(key=psx_wyckoff.rank_key, reverse=True)
    WYCK_SCAN.update(rows=rows, status="done", when=datetime.now().strftime("%H:%M"),
                     progress=f"{len(rows)} structures found")


@app.route("/wyckoff/scan", methods=["POST"])
def wyckoff_scan():
    if WYCK_SCAN["status"] == "running":
        return jsonify(ok=True, already=True)
    body = request.get_json(silent=True) or {}
    tf = body.get("tf", "daily")
    kind = body.get("kind", "watchlist")
    if kind == "watchlist":
        WATCH.sync()
        syms = list(WATCH)
    elif kind == "kse100":
        syms = psx_scan.get_universe("KSE100")
    else:
        load_universe()
        syms = ALL_TICKERS or []
    syms = [s.upper() for s in syms][:400]
    threading.Thread(target=run_wyckoff_scan, args=(syms, tf), daemon=True).start()
    return jsonify(ok=True, started=len(syms))


@app.route("/wyckoff/scanstatus")
def wyckoff_scanstatus():
    return jsonify(ok=True, **WYCK_SCAN)


@app.route("/v2/regime")
def v2_regime():
    r = regime_now(force=request.args.get("force") == "1")
    own = psx_live.breadth_report()
    return jsonify(ok=True, regime=r, breadth=own)


@app.route("/v2/book", methods=["POST"])
def v2_book():
    """Size every current candidate together and show where the caps bind."""
    ranked = WATCH_RANK.get("ranked") or []
    cands = []
    for r in ranked:
        L = r.get("levels") or {}
        if r.get("verdict") in ("BUY", "BUY ON TRIGGER"):
            cands.append({"symbol": r["symbol"], "score": r.get("score"),
                          "verdict": r.get("verdict"), "price": r.get("price"),
                          "stop": L.get("stop"), "sector": sector_for(r["symbol"])})
    if not cands:
        return jsonify(ok=False, error="No BUY or BUY ON TRIGGER candidates in the "
                                       "last ranking. Run a scan on MY LIST first.")
    return jsonify(ok=True, **psx_risk.book(cands, capital=CAPITAL))


@app.route("/portfolio")
def portfolio_view():
    pf = psx_memory.portfolio(_live_price)
    rl = psx_memory.realised()
    return jsonify(ok=True, capital=CAPITAL, open=pf, closed=rl)


@app.route("/portfolio/add", methods=["POST"])
def portfolio_add():
    b = request.get_json(silent=True) or {}
    try:
        pid = psx_memory.open_position(b["symbol"], b["qty"], b["avg_cost"],
                                      b.get("note", ""))
    except Exception as e:
        return jsonify(ok=False, error=str(e)[:200])
    return jsonify(ok=True, id=pid)


@app.route("/portfolio/close/<int:pid>", methods=["POST"])
def portfolio_close(pid):
    b = request.get_json(silent=True) or {}
    try:
        psx_memory.close_position(pid, b["exit_px"])
    except Exception as e:
        return jsonify(ok=False, error=str(e)[:200])
    return jsonify(ok=True)


@app.route("/portfolio/del/<int:pid>", methods=["POST"])
def portfolio_del(pid):
    psx_memory.delete_position(pid)
    return jsonify(ok=True)


@app.route("/learning")
def learning():
    sym = (request.args.get("sym") or "").upper() or None
    adj, note = psx_memory.confidence_adjustment(sym)
    return jsonify(ok=True, symbol=sym, adjustment=adj, note=note,
                   accuracy=psx_memory.accuracy(sym),
                   features=psx_memory.feature_scores(sym),
                   history=psx_memory.history(sym, 120))


@app.route("/learning/grade", methods=["POST"])
def learning_grade():
    try:
        r = psx_memory.grade(lambda s: psx_report.load_from_psx(s, 1))
    except Exception as e:
        return jsonify(ok=False, error=str(e)[:200])
    return jsonify(ok=True, **r)


@app.route("/v2/shariah/<sym>")
def v2_shariah(sym):
    return jsonify(ok=True, **psx_context.shariah(sym.upper()),
                   criteria=psx_context.SCREENING_CRITERIA)


# ---------------------------------------------------------------- UI

PAGE = r"""
<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PSX Research Terminal</title>
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#0e131c">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="PSX">
<link rel="apple-touch-icon" href="/icon.svg">
<link rel="icon" href="/icon.svg">
<style>
:root{
 --bg:#070a0f; --panel:#0e131c; --panel2:#131a26; --line:#1e2735;
 --txt:#e8edf5; --dim:#7d8899; --grn:#00e07a; --red:#ff4d5e; --amb:#ffb020;
 --acc:#3d8bff; --acc2:#7c5cff; --glow:0 0 24px rgba(61,139,255,.18);
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);
 font:14px/1.5 -apple-system,'Segoe UI',Roboto,sans-serif;
 background-image:radial-gradient(900px 500px at 12% -8%,rgba(61,139,255,.10),transparent),
                  radial-gradient(700px 400px at 95% 0%,rgba(124,92,255,.08),transparent);
 min-height:100vh}
header{display:flex;align-items:center;gap:14px;padding:14px 18px;
 background:rgba(14,19,28,.82);backdrop-filter:blur(12px);
 border-bottom:1px solid var(--line);position:sticky;top:0;z-index:50}
.logo{font-size:17px;font-weight:700;letter-spacing:.5px;
 background:linear-gradient(92deg,var(--acc),var(--acc2));
 -webkit-background-clip:text;-webkit-text-fill-color:transparent}
.tag{font-size:10px;color:var(--dim);border:1px solid var(--line);
 padding:3px 9px;border-radius:20px}
.live{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--grn);margin-left:auto}
.dot{width:7px;height:7px;border-radius:50%;background:var(--grn);
 animation:p 2s infinite}@keyframes p{0%,100%{opacity:1}50%{opacity:.25}}
.layout{display:grid;grid-template-columns:290px 1fr;gap:0;min-height:calc(100vh - 56px)}
aside{background:rgba(14,19,28,.6);border-right:1px solid var(--line);
 padding:14px;overflow-y:auto;max-height:calc(100vh - 56px);position:sticky;top:56px}
main{padding:18px;overflow-x:hidden}
.addbar{display:flex;gap:6px;margin-bottom:12px}
input,select{background:var(--panel2);border:1px solid var(--line);color:var(--txt);
 border-radius:9px;padding:9px 11px;font-size:13px;width:100%;outline:none}
input:focus{border-color:var(--acc);box-shadow:var(--glow)}
button{background:var(--panel2);border:1px solid var(--line);color:var(--txt);
 border-radius:9px;padding:9px 13px;font-size:13px;cursor:pointer;
 transition:.15s;white-space:nowrap;font-family:inherit}
button:hover{border-color:var(--acc);color:#fff}
button.p{background:linear-gradient(92deg,var(--acc),var(--acc2));border:none;
 font-weight:600;color:#fff}
button.p:hover{filter:brightness(1.14)}
.sec{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:1px;
 margin:14px 0 6px;padding-bottom:5px;border-bottom:1px solid var(--line)}
.wrow{display:flex;align-items:center;gap:8px;padding:8px 9px;border-radius:9px;
 cursor:pointer;transition:.12s;margin-bottom:2px}
.wrow:hover{background:var(--panel2)}
.wrow.sel{background:rgba(61,139,255,.14);box-shadow:inset 2px 0 0 var(--acc)}
.cb{width:15px;height:15px;border:1.5px solid var(--line);border-radius:4px;
 flex-shrink:0;display:grid;place-items:center;font-size:10px}
.cb.on{background:var(--acc);border-color:var(--acc);color:#fff}
.wsym{font-weight:600;font-size:13px;flex:1}
.wpx{font-size:12px;color:var(--dim)}
.wch{font-size:11px;min-width:52px;text-align:right}
.x{color:var(--dim);font-size:15px;opacity:0;padding:0 3px}
.wrow:hover .x{opacity:1}
.pos{color:var(--grn)}.neg{color:var(--red)}
.wyk{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin:10px 0}
.wyk div{background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:9px 11px}
.wyk b{display:block;font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.6px}
.wyk span{font-size:15px;font-weight:600}
.phase{display:inline-grid;grid-auto-flow:column;gap:4px;margin:6px 0 12px}
.ph{width:34px;height:26px;display:grid;place-items:center;border-radius:6px;
 background:var(--panel2);border:1px solid var(--line);font-size:11px;color:var(--dim)}
.ph.on{background:linear-gradient(92deg,var(--acc),var(--acc2));color:#fff;
 border-color:transparent;font-weight:700}
.rngbar{position:relative;height:30px;background:var(--panel2);border:1px solid var(--line);
 border-radius:8px;margin:10px 0 4px;overflow:hidden}
.rngbar i{position:absolute;top:0;bottom:0;width:3px;background:var(--amb)}
.rngbar u{position:absolute;top:0;bottom:0;left:0;background:rgba(61,139,255,.14)}
.rngbar em{position:absolute;font-size:10px;color:var(--dim);top:8px;font-style:normal}
.crit{list-style:none;font-size:12.5px;line-height:1.9}
.crit li:before{content:'\2715';color:var(--red);margin-right:8px;font-weight:700}
.crit li.y:before{content:'\2713';color:var(--grn)}
.prob{font-size:10px;padding:2px 8px;border-radius:20px;font-weight:700;letter-spacing:.4px}
.prob.High{background:rgba(0,224,122,.16);color:var(--grn)}
.prob.Medium{background:rgba(255,176,32,.16);color:var(--amb)}
.prob.Low{background:rgba(125,136,153,.16);color:var(--dim)}
table.t2{width:100%;border-collapse:collapse;font-size:12.5px}
table.t2 th{text-align:left;color:var(--dim);font-size:10px;text-transform:uppercase;
 letter-spacing:.7px;padding:7px 8px;border-bottom:1px solid var(--line)}
table.t2 td{padding:7px 8px;border-bottom:1px solid var(--line)}
table.t2 tr:hover td{background:var(--panel2)}
.tabs{display:flex;gap:7px;margin-bottom:16px;flex-wrap:wrap}
.tab{background:var(--panel);border:1px solid var(--line);color:var(--dim);
 padding:8px 15px;border-radius:22px;font-size:12px;cursor:pointer;transition:.15s}
.tab:hover{color:var(--txt)}
.tab.on{background:linear-gradient(92deg,var(--acc),var(--acc2));
 border-color:transparent;color:#fff;font-weight:600;box-shadow:var(--glow)}
.card{background:linear-gradient(160deg,var(--panel),rgba(19,26,38,.5));
 border:1px solid var(--line);border-radius:16px;padding:18px;margin-bottom:14px}
.vhead{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:14px}
.vsym{font-size:26px;font-weight:700}
.vpx{font-size:20px;color:var(--dim)}
.badge{padding:7px 16px;border-radius:24px;font-weight:700;font-size:13px;letter-spacing:.4px}
.buy{background:rgba(0,224,122,.15);color:var(--grn);border:1px solid rgba(0,224,122,.35)}
.trigger{background:rgba(61,139,255,.15);color:var(--acc);border:1px solid rgba(61,139,255,.35)}
.wait{background:rgba(255,176,32,.13);color:var(--amb);border:1px solid rgba(255,176,32,.32)}
.avoid{background:rgba(255,77,94,.13);color:var(--red);border:1px solid rgba(255,77,94,.32)}
.conf{margin-left:auto;text-align:right}
.confn{font-size:22px;font-weight:700}
.confl{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:1px}
.bar{height:5px;background:var(--panel2);border-radius:4px;overflow:hidden;margin-top:14px}
.bar i{display:block;height:100%;border-radius:4px;
 background:linear-gradient(90deg,var(--acc),var(--acc2))}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(108px,1fr));gap:9px;margin:15px 0}
.stat{background:var(--panel2);border:1px solid var(--line);border-radius:11px;padding:11px}
.stat b{display:block;font-size:9px;color:var(--dim);text-transform:uppercase;
 letter-spacing:.9px;margin-bottom:4px}
.stat span{font-size:15px;font-weight:600}
.rlist{list-style:none}
.rlist li{padding:8px 0 8px 22px;position:relative;font-size:13px;
 border-bottom:1px solid rgba(30,39,53,.5)}
.rlist li:last-child{border:none}
.rlist li:before{position:absolute;left:0;top:8px;font-size:12px}
.b4 li:before{content:'▲';color:var(--grn)}
.b5 li:before{content:'▼';color:var(--red)}
.b6 li:before{content:'!';color:var(--amb);font-weight:700}
h3{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:1.1px;
 margin-bottom:9px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{color:var(--dim);font-size:9.5px;text-transform:uppercase;letter-spacing:.8px;
 text-align:right;padding:9px 6px;border-bottom:1px solid var(--line);font-weight:500}
td{padding:10px 6px;text-align:right;border-bottom:1px solid rgba(30,39,53,.55)}
tr{cursor:pointer;transition:.12s}tr:hover td{background:rgba(61,139,255,.06)}
td.l,th.l{text-align:left}
.sym{font-weight:600;color:var(--acc)}
.pill{padding:2.5px 9px;border-radius:14px;font-size:10px;font-weight:600}
.up{background:rgba(0,224,122,.14);color:var(--grn)}
.dn{background:rgba(255,77,94,.14);color:var(--red)}
pre{white-space:pre-wrap;font:11.5px/1.6 ui-monospace,Menlo,Consolas,monospace;
 color:var(--dim);max-height:520px;overflow:auto}
.plan{display:grid;grid-template-columns:repeat(auto-fit,minmax(88px,1fr));gap:8px}
.plan div{background:var(--panel2);border-radius:9px;padding:10px;text-align:center;
 border:1px solid var(--line)}
.plan b{display:block;font-size:9px;color:var(--dim);text-transform:uppercase;
 letter-spacing:.7px;margin-bottom:3px}
.plan span{font-size:14px;font-weight:600}
.empty{text-align:center;padding:70px 20px;color:var(--dim)}
.empty .big{font-size:44px;margin-bottom:12px;opacity:.35}
.spin{color:var(--acc);text-align:center;padding:40px}
.note{font-size:11px;color:var(--dim);margin-top:12px;line-height:1.6;
 border-left:2px solid var(--line);padding-left:11px}
@media(max-width:820px){
 .layout{grid-template-columns:1fr}
 aside{max-height:none;position:static;border-right:none;
   border-bottom:1px solid var(--line)}
}
</style></head><body>

<header>
  <div class="logo">PSX TERMINAL</div>
  <div class="tag">technical engine</div>
  <div class="live" id="mkt"><div class="dot"></div><span id="clock">—</span></div>
  <div class="tag" id="installbtn" style="display:none;cursor:pointer;
    color:var(--grn);border-color:rgba(0,224,122,.4)"
    onclick="doInstall()">⤓ INSTALL</div>
  <div class="tag" id="autopill" style="cursor:pointer" onclick="toggleAuto()">AUTO OFF</div>
  <select id="mode" style="width:auto;padding:5px 8px;font-size:11px" onchange="setAuto(true)">
    <option value="watchlist" selected>my list</option>
    <option value="kse100">KSE-100</option></select>
  <select id="every" style="width:auto;padding:5px 8px;font-size:11px" onchange="setAuto(true)">
    <option value="5">5m</option><option value="15" selected>15m</option>
    <option value="30">30m</option><option value="60">60m</option></select>
</header>

<div class="layout">
<aside>
  <div style="display:flex;gap:6px;margin-bottom:8px">
    <select id="listsel" onchange="pickList(this.value)" style="flex:1"></select>
    <button onclick="newList()" title="new list">＋</button>
    <button onclick="delList()" title="delete list">🗑</button>
  </div>
  <div class="addbar">
    <input id="add" placeholder="add symbol…" onkeydown="if(event.key=='Enter')addSym()">
    <button class="p" onclick="addSym()">+</button>
  </div>
  <div style="display:flex;gap:6px;margin-bottom:6px">
    <button class="p" style="flex:1" onclick="scanSelected()">SCAN SELECTED</button>
    <button onclick="selAll()" title="select all">☰</button>
  </div>
  <div style="font-size:10px;color:var(--dim);margin-bottom:4px" id="selinfo">0 selected</div>
  <div id="wl"></div>
</aside>

<main>
  <div class="tabs" id="tabs"></div>
  <div id="view"></div>
</main>
</div>

<script>
let WATCH=[],SEL=new Set(),CUR='mylist',ACTIVE=null,poll=null;

function clock(){const d=new Date();
 document.getElementById('clock').textContent=d.toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'});}
setInterval(clock,1000);clock();

const TABS=[['live','◉ LIVE'],['mylist','MY LIST'],['v2','\u25C8 DEEP READ'],
            ['wyckoff','WYCKOFF'],['wscan','WYCKOFF SCAN'],['compare','COMPARE'],
            ['stock','STOCK'],['fund','FINANCIALS'],['book','BOOK RISK'],
            ['pf','PORTFOLIO'],['learn','TRACK RECORD'],
            ['browse','ALL PSX'],['market','MARKET SCAN'],['breadth','BREADTH'],
            ['raw','FULL REPORT'],['export','\u2913 EXPORT']];
function drawTabs(){document.getElementById('tabs').innerHTML=
 TABS.map(([k,l])=>`<div class="tab ${k==CUR?'on':''}" onclick="go('${k}')">${l}</div>`).join('');}
function go(k){CUR=k;drawTabs();render();}
const V=h=>document.getElementById('view').innerHTML=h;
const cls=v=>v>0?'pos':v<0?'neg':'';

// ---------- watchlist ----------
function loadLists(){
 fetch('/lists').then(r=>r.json()).then(d=>{
  const sel=document.getElementById('listsel');
  sel.innerHTML=Object.keys(d.lists).map(n=>
   `<option value="${n}" ${n==d.current?'selected':''}>${n} (${d.lists[n].length})</option>`).join('');});}
function pickList(n){fetch('/lists/select/'+encodeURIComponent(n),{method:'POST'})
 .then(()=>{SEL.clear();loadWL();loadLists();if(CUR=='mylist')myListView();});}
function newList(){
 const n=prompt('Name for the new list:');if(!n)return;
 const syms=prompt('Symbols, separated by spaces or commas (leave blank for empty):')||'';
 fetch('/lists/create',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({name:n,symbols:syms.split(/[\s,]+/).filter(Boolean)})})
 .then(()=>{loadWL();loadLists();});}
function delList(){
 const n=document.getElementById('listsel').value;
 if(!confirm('Delete list "'+n+'"?'))return;
 fetch('/lists/delete/'+encodeURIComponent(n),{method:'POST'})
  .then(()=>{loadWL();loadLists();});}

function loadWL(){
 fetch('/watchlist').then(r=>r.json()).then(d=>{WATCH=d.items;drawWL();});}
function drawWL(){
 const bysec={};WATCH.forEach(w=>{(bysec[w.sector]=bysec[w.sector]||[]).push(w);});
 let h='';
 Object.keys(bysec).sort().forEach(sec=>{
  h+=`<div class="sec">${sec}</div>`;
  bysec[sec].forEach(w=>{
   const c=w.chg==null?'':(w.chg>0?'pos':w.chg<0?'neg':'');
   h+=`<div class="wrow ${ACTIVE==w.sym?'sel':''}" onclick="pick('${w.sym}')">
    <div class="cb ${SEL.has(w.sym)?'on':''}" onclick="event.stopPropagation();tog('${w.sym}')">
      ${SEL.has(w.sym)?'✓':''}</div>
    <div class="wsym">${w.sym}</div>
    <div class="wpx">${w.price??'—'}</div>
    <div class="wch ${c}">${w.chg==null?'':(w.chg>0?'+':'')+w.chg+'%'}</div>
    <div class="x" onclick="event.stopPropagation();rm('${w.sym}')">×</div></div>`;});});
 document.getElementById('wl').innerHTML=h||'<div class="note">Watchlist empty. Add a symbol above.</div>';
 document.getElementById('selinfo').textContent=SEL.size+' selected';}
function tog(s){SEL.has(s)?SEL.delete(s):SEL.add(s);drawWL();}
function selAll(){SEL.size==WATCH.length?SEL.clear():WATCH.forEach(w=>SEL.add(w.sym));drawWL();}
function addSym(){const i=document.getElementById('add');const s=i.value.trim().toUpperCase();
 if(!s)return;i.value='';fetch('/watch/add/'+s,{method:'POST'}).then(()=>loadWL());}
function rm(s){fetch('/watch/remove/'+s,{method:'POST'}).then(()=>{SEL.delete(s);loadWL();});}
function pick(s){ACTIVE=s;CUR='stock';drawTabs();drawWL();render();}

// ---------- render ----------
function render(){
 if(CUR=='live')return liveView();
 if(CUR=='mylist')return myListView();
 if(CUR=='compare')return compareView();
 if(CUR=='stock')return stockView();
 if(CUR=='fund')return fundView();
 if(CUR=='browse')return browseView();
 if(CUR=='market')return marketView();
 if(CUR=='export')return exportView();
 if(CUR=='breadth')return breadthView();
 if(CUR=='raw')return rawView();
 if(CUR=='v2')return v2View();
 if(CUR=='wyckoff')return wyckView();
 if(CUR=='wscan')return wscanView();
 if(CUR=='book')return bookView();
 if(CUR=='pf')return pfView();
 if(CUR=='learn')return learnView();}

let livePoll=null;
function liveView(){
 V('<div class="spin">reading the live tape…</div>');
 loadLive();
 clearInterval(livePoll);
 livePoll=setInterval(()=>{if(CUR=='live')loadLive();},60000);}
function loadLive(){
 fetch('/live').then(r=>r.json()).then(d=>{
  if(!d.ok)return V('<div class="card"><h3>Live feed</h3><div class="note">'+
    d.error+'</div></div>');
  const A=d.alerts||[];
  const badge=d.market_open?'<span class="pill up">MARKET OPEN</span>':
              '<span class="pill dn">MARKET CLOSED</span>';
  let h=`<div class="card"><div class="vhead">
    <div class="vsym">LIVE TAPE</div>${badge}
    <div class="tag">session ${d.session}% elapsed</div>
    <div class="tag">${d.when}</div>
    <button class="p" style="margin-left:auto" onclick="loadLive()">REFRESH</button></div>
    <pre style="color:var(--txt);font-size:12.5px">${d.summary}</pre>
    <div class="note">Auto-refreshes every 60s while this tab is open.
     Live tape shows what is happening TODAY — cross-check against MY LIST
     before acting.</div></div>`;
  if(!A.length)return V(h);
  h+='<div class="card"><h3>Signals</h3>';
  A.forEach(a=>{
   const col=a.class=='accum'?'var(--grn)':a.class=='distrib'?'var(--red)':
             a.class=='block'?'var(--acc2)':'var(--amb)';
   h+=`<div style="border-left:3px solid ${col};padding:11px 13px;margin-bottom:9px;
        background:var(--panel2);border-radius:0 10px 10px 0;cursor:pointer"
        onclick="pick('${a.symbol}')">
     <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
      <b style="color:var(--acc);font-size:15px">${a.symbol}</b>
      <span style="color:${col};font-weight:700;font-size:12px">${a.kind}</span>
      <span>${a.price}</span>
      <span class="${a.chg>0?'pos':'neg'}">${a.chg>0?'+':''}${a.chg}%</span>
      <span style="margin-left:auto;font-size:11px;color:var(--dim)">
        Rs${a.value_m}M · ${a.trades} trades</span></div>
     <div style="font-size:11.5px;color:var(--dim);margin-top:6px">
      ${a.why.join(' · ')}</div></div>`;});
  V(h+'</div>');});}

let listPoll=null;
function WATCH_RANK_IDLE(){return listPoll==null;}
function myListView(){
 V('<div class="spin">loading…</div>');
 fetch('/ranklist').then(r=>r.json()).then(function(d){
  if(d.status=='idle'&&(!d.ranked||!d.ranked.length)){
   V('<div class="empty"><div class="big">◧</div><b>No ranking yet</b>'+
     '<div style="margin-top:8px;font-size:13px">Scanning your list takes a few '+
     'minutes the first time.</div><br>'+
     '<button class="p" onclick="rerank()">RUN SCAN NOW</button></div>');
   return;}
  handleList(d);});}
function handleList(d){
 if(d.status=='running'){
  const pct=d.total?Math.round(d.done/d.total*100):0;
  V('<div class="card"><h3>Ranking your list</h3>'+
    '<div class="spin" style="padding:14px">'+(d.progress||'')+'</div>'+
    '<div class="bar"><i style="width:'+pct+'%"></i></div>'+
    '<div class="note">First run downloads 3 years of history per stock, so it '+
    'can take several minutes on a cold server. This keeps running even if you '+
    'switch tabs — come back and it will be here.</div></div>');
  clearTimeout(listPoll);
  listPoll=setTimeout(function(){
    fetch('/ranklist').then(function(r){return r.json();}).then(handleList);},3000);
  return;}
 clearTimeout(listPoll);listPoll=null;
 if(d.status=='error'){V('<div class="card"><h3>Scan failed</h3><div class="note">'+
   d.progress+'</div></div>');return;}
 paintList(d);}
let PMODE='drop';
const PLABEL={drop:'BAR: LIVE-SAFE',prorate:'BAR: PRORATED',raw:'BAR: RAW'};
function cyclePartial(){
 PMODE=PMODE=='drop'?'prorate':PMODE=='prorate'?'raw':'drop';
 fetch('/auto',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({on:AUTOON,every:+document.getElementById('every').value,
   mode:document.getElementById('mode').value,partial:PMODE})})
  .then(()=>{const e=document.getElementById('pmode');if(e)e.textContent=PLABEL[PMODE];rerank();});}
function rerank(){
 fetch('/ranklist',{method:'POST'}).then(r=>r.json()).then(handleList);}
function paintList(d){
 if(!d.ok)return V('<div class="card">'+d.error+'</div>');
 let h=`<div class="card"><div class="vhead">
   <div class="vsym">MY LIST</div><div class="vpx">${(d.ranked||[]).length} stocks</div>
   <div class="tag">${d.when||''}</div>
   <div class="tag" id="pmode" style="cursor:pointer" onclick="cyclePartial()">BAR: LIVE-SAFE</div>
   <button class="p" style="margin-left:auto" onclick="rerank()">REFRESH NOW</button></div>
   <pre style="color:var(--txt);font-size:12.5px">${d.commentary||''}</pre>
   ${(d.ranked&&d.ranked[0]&&d.ranked[0].partial)?
     `<div class="note" style="border-color:var(--amb)">${d.ranked[0].partial}</div>`:''}</div>`;
 h+=`<div class="card"><h3>Ranked</h3><table><thead><tr>
  <th class="l">Sym</th><th>Price</th><th>Verdict</th><th>wVol</th><th>dVol</th>
  <th>Trend</th><th>Cloud</th><th>RSI</th><th>Trigger</th><th>Conf</th></tr></thead><tbody>`;
 (d.ranked||[]).forEach(r=>{h+=`<tr onclick="pick('${r.symbol}')">
  <td class="l sym">${r.symbol}${r.thin?' <span class="pill dn" style="font-size:8px">THIN</span>':''}</td><td>${r.price}</td>
  <td><span class="badge ${r.class}" style="font-size:10px;padding:4px 9px">${r.verdict}</span></td>
  <td class="${cls(r.state.wVol)}">${r.state.wVol>0?'+':''}${r.state.wVol}</td>
  <td class="${cls(r.state.dVol)}">${r.state.dVol>0?'+':''}${r.state.dVol}</td>
  <td><span class="pill ${r.state.dTrend=='UP'?'up':'dn'}">${r.state.dTrend}</span></td>
  <td>${r.state.cloud}</td><td>${r.state.rsi}</td>
  <td>${r.levels.trigger}</td><td><b>${r.confidence}</b></td></tr>`;});
 V(h+`</tbody></table><div class="note">Turn AUTO on in the header to re-rank
  this list automatically during market hours. Scanning your own list is far
  lighter than a full market scan.</div></div>`);}

function compareView(){
 if(!SEL.size)return V(`<div class="empty"><div class="big">◧</div>
  <b>Select stocks on the left</b><div style="margin-top:6px;font-size:13px">
  Tick the boxes, then press SCAN SELECTED to rank them against each other
  with full reasoning.</div></div>`);
 V('<div class="spin">analysing '+SEL.size+' stocks…</div>');
 fetch('/compare',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({symbols:[...SEL]})}).then(r=>r.json()).then(d=>{
  if(!d.ok)return V('<div class="card">'+d.error+'</div>');
  let h=`<div class="card"><h3>Verdict &amp; reasoning</h3>
    <pre style="color:var(--txt);font-size:12.5px">${d.commentary}</pre></div>`;
  h+=`<div class="card"><h3>Ranked</h3><table><thead><tr>
   <th class="l">Sym</th><th>Price</th><th>Verdict</th><th>wVol</th><th>dVol</th>
   <th>Trend</th><th>Cloud</th><th>RSI</th><th>Trigger</th><th>Conf</th></tr></thead><tbody>`;
  d.ranked.forEach(r=>{h+=`<tr onclick="pick('${r.symbol}')">
   <td class="l sym">${r.symbol}</td><td>${r.price}</td>
   <td><span class="badge ${r.class}" style="font-size:10px;padding:4px 9px">${r.verdict}</span></td>
   <td class="${cls(r.state.wVol)}">${r.state.wVol>0?'+':''}${r.state.wVol}</td>
   <td class="${cls(r.state.dVol)}">${r.state.dVol>0?'+':''}${r.state.dVol}</td>
   <td><span class="pill ${r.state.dTrend=='UP'?'up':'dn'}">${r.state.dTrend}</span></td>
   <td>${r.state.cloud}</td><td>${r.state.rsi}</td>
   <td>${r.levels.trigger}</td><td><b>${r.confidence}</b></td></tr>`;});
  V(h+'</tbody></table></div>');});}

function stockView(){
 if(!ACTIVE)return V('<div class="empty"><div class="big">◈</div><b>Pick a stock on the left</b></div>');
 V('<div class="spin">analysing '+ACTIVE+'…</div>');
 fetch('/analyse/'+ACTIVE).then(r=>r.json()).then(d=>{
  if(!d.ok)return V('<div class="card">Error: '+d.error+'</div>');
  window.LAST=d;
  const L=d.levels,S=d.state;
  let h=`<div class="card"><div class="vhead">
    <div class="vsym">${d.symbol}</div><div class="vpx">${d.price}</div>
    <div class="badge ${d.class}">${d.verdict}</div>
    <div class="conf"><div class="confn">${d.confidence}</div>
      <div class="confl">confidence</div></div></div>
   <div class="bar"><i style="width:${d.confidence}%"></i></div>
   <div class="grid">
    <div class="stat"><b>weekly flow</b><span class="${cls(S.wVol)}">${S.wVol>0?'+':''}${S.wVol}/6</span></div>
    <div class="stat"><b>daily flow</b><span class="${cls(S.dVol)}">${S.dVol>0?'+':''}${S.dVol}/6</span></div>
    <div class="stat"><b>daily trend</b><span class="${S.dTrend=='UP'?'pos':'neg'}">${S.dTrend}</span></div>
    <div class="stat"><b>weekly trend</b><span class="${S.wTrend=='UP'?'pos':'neg'}">${S.wTrend}</span></div>
    <div class="stat"><b>vs cloud</b><span>${S.cloud}</span></div>
    <div class="stat"><b>RSI</b><span>${S.rsi}</span></div>
    <div class="stat"><b>ADX</b><span>${S.adx}</span></div>
    <div class="stat"><b>volume</b><span>${S.volx}x</span></div>
   </div>${d.rs?`<div class="note">${d.rs}</div>`:''}</div>`;

  if(d.bull.length)h+=`<div class="card"><h3>Supporting the trade</h3>
    <ul class="rlist b4">${d.bull.map(x=>`<li>${x}</li>`).join('')}</ul></div>`;
  if(d.bear.length)h+=`<div class="card"><h3>Against the trade</h3>
    <ul class="rlist b5">${d.bear.map(x=>`<li>${x}</li>`).join('')}</ul></div>`;
  if(d.flags.length)h+=`<div class="card"><h3>Watch out</h3>
    <ul class="rlist b6">${d.flags.map(x=>`<li>${x}</li>`).join('')}</ul></div>`;

  h+=`<div class="card"><h3>Trade plan</h3><div class="plan">
    <div><b>trigger</b><span>${L.trigger}</span></div>
    <div><b>stop</b><span class="neg">${L.stop}</span></div>
    <div><b>target 1</b><span class="pos">${L.t1}</span></div>
    <div><b>target 2</b><span class="pos">${L.t2}</span></div>
    <div><b>target 3</b><span class="pos">${L.t3}</span></div>
    <div><b>risk</b><span>${L.risk_pct}%</span></div>
    <div><b>R:R</b><span>${L.rr}</span></div>
    <div><b>size</b><span>${L.size_pct}%</span></div></div>
    <div class="note">Size assumes 0.5% account risk. In weak market breadth,
     halve it and wait for the trigger rather than anticipating it.</div></div>`;
  V(h);});}

function rawView(){
 if(!window.LAST||!window.LAST.report)return V('<div class="empty"><div class="big">▤</div>'+
  '<b>Open a stock first</b><div style="margin-top:6px;font-size:13px">'+
  'The full indicator report appears here.</div></div>');
 V('<div class="card"><h3>'+window.LAST.symbol+' — full indicator report</h3><pre>'+
   window.LAST.report.replace(/</g,'&lt;')+'</pre></div>');}

function marketView(){
 V(`<div class="card"><div style="display:flex;gap:8px;flex-wrap:wrap">
  <select id="uni" style="width:auto"><option value="KSE100">KSE-100 (fast)</option>
   <option value="all">Whole market (slow)</option></select>
  <select id="mv" style="width:auto"><option value="0.1" selected>vol&gt;0.1M</option>
   <option value="0.05">vol&gt;50k</option><option value="0.2">vol&gt;0.2M</option>
   <option value="0.5">vol&gt;0.5M</option><option value="1">vol&gt;1M</option></select>
  <button class="p" id="sb" onclick="doScan()">RUN SCAN</button></div>
  <div class="note" id="st">Ranks every stock by the accumulation-recovery model.</div></div>
  <div class="card"><table><thead><tr><th class="l">Sym</th><th>Price</th><th>1m</th>
  <th>wVol</th><th>dVol</th><th>Trend</th><th>Cloud</th><th>Score</th></tr></thead>
  <tbody id="rows"></tbody></table></div>`);
 fetch('/status').then(r=>r.json()).then(d=>{if(d.rows&&d.rows.length)rowsHtml(d.rows);});}
function doScan(){fetch('/scan',{method:'POST',headers:{'Content-Type':'application/json'},
 body:JSON.stringify({universe:uni.value,min_vol:mv.value})}).then(r=>r.json())
 .then(d=>{if(d.ok){document.getElementById('sb').textContent='…';pollScan();}});}
function pollScan(){clearInterval(poll);poll=setInterval(()=>{
 fetch('/status').then(r=>r.json()).then(d=>{
  const st=document.getElementById('st');if(st)st.textContent=d.progress||d.status;
  if(d.status=='done'||d.status=='error'){clearInterval(poll);
   const b=document.getElementById('sb');if(b)b.textContent='RUN SCAN';rowsHtml(d.rows||[]);}});},1500);}
function rowsHtml(rows){const tb=document.getElementById('rows');if(!tb)return;tb.innerHTML='';
 rows.slice(0,60).forEach(r=>{const tr=document.createElement('tr');
  tr.onclick=()=>pick(r.sym);
  tr.innerHTML=`<td class="l sym">${r.sym}</td><td>${r.price}</td>
   <td class="${cls(r['1m%'])}">${r['1m%']}%</td>
   <td class="${cls(r.wVol)}">${r.wVol>0?'+':''}${r.wVol}</td>
   <td class="${cls(r.dVol)}">${r.dVol>0?'+':''}${r.dVol}</td>
   <td><span class="pill ${r.dTrend=='UP'?'up':'dn'}">${r.dTrend}</span></td>
   <td>${r.cloud}</td><td><b>${r.SCORE}</b></td>`;tb.appendChild(tr);});}

function exportView(){
 const sel=[...SEL];
 const n=sel.length, target=n?sel:WATCH.map(w=>w.sym);
 V(`<div class="card"><div class="vhead"><div class="vsym">EXPORT</div>
   <div class="tag">${target.length} stock${target.length==1?'':'s'}</div></div>
   <div class="note">${n? 'Exporting your <b>'+n+' ticked</b> stock'+(n==1?'':'s')+'.'
     : 'Nothing ticked — exporting your <b>whole list</b> ('+target.length+'). '+
       'Tick boxes on the left to export a subset.'}</div>
   <div class="row" style="margin-top:14px">
     <button class="p" style="flex:1" onclick="doExport('excel')">⤓ EXCEL (.xlsx)</button>
     <button class="p" style="flex:1" onclick="doExport('pdf')">⤓ PDF REPORT</button>
   </div>
   <div class="status" id="exs"></div></div>

   <div class="card"><h3>Export one stock</h3>
    <div class="row">
     <input id="exsym" placeholder="symbol e.g. GAL" style="flex:1"
      onkeydown="if(event.key=='Enter')doExportOne('excel')">
     <button onclick="doExportOne('excel')">EXCEL</button>
     <button onclick="doExportOne('pdf')">PDF</button></div></div>

   <div class="card"><h3>What's in each file</h3>
    <ul class="rlist b4">
     <li><b>Excel</b> — Summary sheet with the ranked table and commentary, then
        one sheet per stock: reasoning, trade plan and the full indicator report</li>
     <li><b>PDF</b> — cover page with ranked table and commentary, then a page per
        stock with verdict, evidence, levels and every indicator</li>
    </ul>
    <div class="note">Large lists take a few minutes to build — the download
     starts when it's ready. Cached stocks are much faster.</div></div>`);}

function doExport(kind){
 const sel=[...SEL];
 const q=sel.length?('?syms='+encodeURIComponent(sel.join(','))):'';
 const e=document.getElementById('exs');
 if(e)e.textContent='Building '+kind.toUpperCase()+'… this can take a few minutes for a long list.';
 window.location='/export/'+kind+q;
 setTimeout(()=>{if(e)e.textContent='If the download did not start, check the CMD window for an error.';},4000);}

function doExportOne(kind){
 const s=(document.getElementById('exsym').value||'').trim().toUpperCase();
 if(!s)return alert('Type a symbol first.');
 window.location='/export/'+kind+'?syms='+encodeURIComponent(s);}

function breadthView(){V('<div class="spin">reading breadth…</div>');
 fetch('/breadth').then(r=>r.json()).then(d=>
  V('<div class="card"><h3>Market regime</h3><pre style="color:var(--txt)">'+
    d.text+'</pre><div class="note">Run a market scan first if this looks empty. '+
    'Breadth sets your position size, not your stock selection.</div></div>'));}

function fundView(){
 if(!ACTIVE)return V('<div class="empty"><div class="big">▣</div><b>Pick a stock first</b>'+
  '<div style="margin-top:6px;font-size:13px">Financial reports are pulled live from PSX.</div></div>');
 V('<div class="spin">pulling '+ACTIVE+' financials from PSX…</div>');
 fetch('/fundamentals/'+ACTIVE).then(r=>r.json()).then(d=>{
  if(!d.ok)return V('<div class="card"><h3>'+ACTIVE+'</h3><div class="note">'+d.error+'</div></div>');
  let h='<div class="card"><div class="vhead"><div class="vsym">'+d.symbol+
        '</div><div class="tag">PSX filings</div></div>';
  const m=Object.keys(d.metrics||{});
  if(m.length){h+='<div class="grid">'+m.slice(0,12).map(k=>
    `<div class="stat"><b>${k}</b><span>${d.metrics[k]}</span></div>`).join('')+'</div>';}
  h+=(d.notes||[]).map(n=>`<div class="note">${n}</div>`).join('')+'</div>';
  (d.tables||[]).forEach(t=>{
   h+=`<div class="card"><h3>${t.name}</h3><div style="overflow-x:auto"><table><thead><tr>`+
      t.columns.map((c,i)=>`<th class="${i==0?'l':''}">${c}</th>`).join('')+
      '</tr></thead><tbody>'+
      t.rows.map(r=>'<tr>'+r.map((c,i)=>`<td class="${i==0?'l':''}">${c}</td>`).join('')+'</tr>').join('')+
      '</tbody></table></div></div>';});
  V(h);});}

function browseView(){
 V('<div class="spin">loading every PSX listing…</div>');
 fetch('/universe').then(r=>r.json()).then(d=>{
  const liveOK=d.source=='live';
  let h=`<div class="card"><div class="vhead"><div class="vsym">${d.count}</div>
   <div class="vpx">listed symbols</div>
   <div class="tag" style="${liveOK?'color:var(--grn);border-color:rgba(0,224,122,.4)':'color:var(--amb);border-color:rgba(255,176,32,.4)'}">
     ${liveOK?'LIVE FROM PSX':'FALLBACK LIST'}</div></div>`;
  if(!liveOK){h+=`<div class="note" style="border-color:var(--amb)">
    The live PSX ticker fetch failed, so this is the built-in list.<br>
    Reason: <b>${d.error||'unknown'}</b><br>
    Send me that message and I'll fix the parser.</div>`;}
  h+=`<div class="note">Click any symbol to analyse it. Use + add sector to
   put a whole group on your watchlist.</div></div>`;
  Object.keys(d.sectors).forEach(sec=>{
   const list=d.sectors[sec];
   h+=`<div class="card"><h3>${sec} &nbsp;·&nbsp; ${list.length}
     <button style="float:right;padding:4px 10px;font-size:11px"
      onclick='addMany(${JSON.stringify(list)})'>+ add sector</button></h3>
     <div style="display:flex;flex-wrap:wrap;gap:6px">`+
     list.map(s=>`<span class="pill" style="background:var(--panel2);color:var(--acc);
       cursor:pointer;padding:6px 11px;font-size:12px"
       onclick="pick('${s}')">${s}</span>`).join('')+'</div></div>';});
  V(h);});}
function addMany(list){fetch('/watch/addmany',{method:'POST',
 headers:{'Content-Type':'application/json'},body:JSON.stringify({symbols:list})})
 .then(r=>r.json()).then(d=>{loadWL();alert('Added '+d.added+' symbols. Watchlist now '+d.total+'.');});}

let AUTOON=false;
function setAuto(keep){
 const ev=+document.getElementById('every').value;
 const md=document.getElementById('mode').value;
 const on=keep?AUTOON:!AUTOON;
 fetch('/auto',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({on:on,every:ev,mode:md})}).then(r=>r.json()).then(paintAuto);}
function toggleAuto(){AUTOON=!AUTOON;setAuto(true);}
function paintAuto(a){
 AUTOON=a.on;
 const p=document.getElementById('autopill');
 p.textContent=a.on?('AUTO '+(a.in_sec!=null?fmt(a.in_sec):'')):'AUTO OFF';
 p.style.color=a.on?'var(--grn)':'var(--dim)';
 p.style.borderColor=a.on?'rgba(0,224,122,.4)':'var(--line)';
 const m=document.getElementById('mkt');
 m.style.color=a.market_open?'var(--grn)':'var(--dim)';
 m.title=a.market_open?'market open':'market closed';
 if(a.last)p.title='last auto-scan '+a.last+' · '+a.runs+' runs';}
function fmt(s){const m=Math.floor(s/60);return m>0?m+'m':s+'s';}
setInterval(()=>{fetch('/autostatus').then(r=>r.json()).then(a=>{
  paintAuto(a);
  if(a.on&&CUR=='market')fetch('/status').then(r=>r.json()).then(d=>rowsHtml(d.rows||[]));
  if(a.on&&CUR=='mylist'&&WATCH_RANK_IDLE())fetch('/ranklist').then(r=>r.json()).then(handleList);
 });},15000);
fetch('/autostatus').then(r=>r.json()).then(paintAuto);

if('serviceWorker' in navigator){
 navigator.serviceWorker.register('/sw.js').catch(()=>{});}
let deferredPrompt=null;
window.addEventListener('beforeinstallprompt',e=>{
 e.preventDefault();deferredPrompt=e;
 const b=document.getElementById('installbtn');if(b)b.style.display='inline-block';});
function doInstall(){
 if(!deferredPrompt){alert('Use the Chrome menu (three dots) and pick '+
  '"Install app" or "Add to Home screen".');return;}
 deferredPrompt.prompt();deferredPrompt.userChoice.then(()=>{deferredPrompt=null;
  const b=document.getElementById('installbtn');if(b)b.style.display='none';});}

// ================= v2.0 views =================
const esc=t=>String(t==null?'':t).replace(/&/g,'&amp;').replace(/</g,'&lt;');
const sgn=v=>(v>0?'+':'')+v;

function rangeBar(r,pos){
 if(!r)return '';
 const p=Math.max(-15,Math.min(115,pos));
 return `<div class="rngbar"><u style="width:100%"></u>
   <i style="left:${p}%"></i>
   <em style="left:4px">${r.support} floor</em>
   <em style="right:4px">${r.resistance} ceiling</em></div>
  <div class="note">Price sits ${pos}% of the way up the range.
   ${esc(r.boundary_note||'')}</div>`;}

function phaseStrip(ph){
 return '<div class="phase">'+['A','B','C','D','E'].map(x=>
  `<div class="ph ${x==ph?'on':''}">${x}</div>`).join('')+'</div>';}

function critList(c){
 return '<ul class="crit">'+(c||[]).map(([t,ok])=>
  `<li class="${ok?'y':''}">${esc(t)}</li>`).join('')+'</ul>';}

function eventTable(ev){
 if(!ev||!ev.length)return '<div class="note">No schematic events met their '+
  'criteria in this window. That is a real answer, not a gap.</div>';
 return `<table class="t2"><tr><th>Date</th><th>Event</th><th>Vol</th>
  <th>Spread</th><th>Close pos</th><th>What it means</th></tr>`+
  ev.map(e=>`<tr><td>${e.date}</td><td><b>${e.kind}</b></td>
   <td>${e.vol_x!=null?e.vol_x+'x':''}</td><td>${e.spread_x!=null?e.spread_x+'x':''}</td>
   <td>${e.close_pos!=null?e.close_pos+'%':''}</td>
   <td style="color:var(--dim)">${esc(e.note)}</td></tr>`).join('')+'</table>';}

function wyckCard(w,label){
 if(!w||!w.ok)return `<div class="card"><h3>${label}</h3><div class="note">${
   esc((w&&w.error)||'no read available')}</div></div>`;
 if(!w.range)return `<div class="card"><h3>${label}</h3>
   <div class="note">No trading range — trend context only.</div>
   <pre style="font-size:12px;color:var(--txt)">${esc(w.narrative)}</pre></div>`;
 const R=w.range,ce=(w.laws||{}).cause_effect||{};
 let h=`<div class="card"><div class="vhead">
   <div class="vsym">${label}</div>
   <div class="badge ${/ullish/.test(w.bias)?'buy':/earish/.test(w.bias)?'avoid':'wait'}">${w.bias}</div>
   <div class="tag">${w.structure}</div>
   <div class="tag">read confidence ${w.confidence}</div></div>
  ${phaseStrip(w.phase)}
  <div class="note">${esc(w.phase_note)}</div>
  ${rangeBar(R,w.position_in_range)}
  <div class="wyk">
   <div><b>support</b><span>${R.support}</span></div>
   <div><b>resistance</b><span>${R.resistance}</span></div>
   <div><b>range height</b><span>${R.height_pct}%</span></div>
   <div><b>cause (bars)</b><span>${R.bars}</span></div>
   <div><b>closes inside</b><span>${R.inside_pct}%</span></div>
   <div><b>target up</b><span class="pos">${ce.up_base||'—'}</span></div>
  </div></div>`;

 h+=`<div class="card"><h3>Events identified</h3>${eventTable(w.events)}`;
 if(w.not_labelled&&w.not_labelled.length)
  h+=`<h3 style="margin-top:14px">Deliberately not labelled</h3>
   <ul class="rlist b6">${w.not_labelled.map(e=>
    `<li><b>${e.date} ${e.kind}</b> — ${esc(e.note)}</li>`).join('')}</ul>`;
 h+='</div>';

 const graded=(w.springs||[]).concat(w.upthrusts||[]);
 if(graded.length){
  h+='<div class="card"><h3>Spring / Upthrust quality</h3>';
  graded.forEach(e=>{
   h+=`<div style="margin:0 0 16px;padding-left:10px;border-left:2px solid var(--line)">
     <div style="margin-bottom:6px"><b>${e.kind}</b> &nbsp;${e.date}&nbsp;
      <span class="prob ${e.probability}">${e.probability}</span>
      <span class="tag">${e.criteria_met}/${(e.criteria||[]).length} criteria</span></div>
     <div class="note" style="margin:0 0 6px">${esc(e.note)}</div>
     ${critList(e.criteria)}
     <div class="note">Follow-through: ${esc((e.test||e.sow||{}).note||
       'none yet — unconfirmed')}</div></div>`;});
  h+='</div>';}

 h+=`<div class="card"><h3>Strength vs weakness</h3>
   <ul class="rlist b4">${(w.strength||[]).map(x=>`<li>${esc(x)}</li>`).join('')
     ||'<li style="color:var(--dim)">nothing on the strength side</li>'}</ul>
   <ul class="rlist b5" style="margin-top:10px">${(w.weakness||[]).map(x=>
     `<li>${esc(x)}</li>`).join('')||
     '<li style="color:var(--dim)">nothing on the weakness side</li>'}</ul></div>`;

 const laws=w.laws||{};
 h+=`<div class="card"><h3>The three laws</h3>
   <div class="note"><b>Supply and demand.</b> ${esc((laws.supply_demand||{}).text)}</div>
   <div class="note"><b>Cause and effect.</b> ${esc(ce.text)}</div>
   <div class="note"><b>Effort vs result.</b><br>${
    ((laws.effort_result||{}).notes||[]).map(n=>'· '+esc(n)).join('<br>')}</div></div>`;

 h+=`<div class="card"><h3>PSX-specific risk</h3>
   <ul class="rlist b6">${(w.risks||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div>`;
 h+=`<div class="card"><h3>Full written read</h3>
   <pre style="font-size:12px;color:var(--txt);white-space:pre-wrap">${
    esc(w.narrative)}</pre></div>`;
 return h;}

function wyckView(){
 if(!ACTIVE)return V('<div class="empty"><div class="big">◈</div>'+
  '<b>Pick a stock on the left</b><div class="note">The Wyckoff tab reads structure '+
  '— range, phase, Springs and Upthrusts — from price and volume only.</div></div>');
 V('<div class="spin">reading the structure of '+ACTIVE+'…</div>');
 fetch('/wyckoff/'+ACTIVE).then(r=>r.json()).then(d=>{
  if(!d.ok)return V('<div class="card">Error: '+esc(d.error)+'</div>');
  let h=`<div class="card"><div class="vhead"><div class="vsym">${d.symbol}</div>
    <div class="tag">${d.sector}</div>
    <div class="tag">${d.agree?'daily and weekly agree':'daily and weekly disagree'}</div>
    <button class="p" style="margin-left:auto" onclick="wyckView()">REFRESH</button></div>
    <div class="note">${d.agree?
      'The daily and weekly structures tell the same story, which is the stronger case.':
      'The daily and weekly structures differ. The weekly is the dominant one — a '+
      'bullish daily inside a distributive weekly is a rally to sell, not a base.'}</div>
    </div>`;
  h+=wyckCard(d.weekly,'WEEKLY STRUCTURE (dominant)');
  h+=wyckCard(d.daily,'DAILY STRUCTURE');
  V(h);});}

function v2View(){
 if(!ACTIVE)return V('<div class="empty"><div class="big">◈</div>'+
  '<b>Pick a stock on the left</b><div class="note">Deep Read combines the chart, '+
  'the Wyckoff structure, market context and the capital rules into one verdict.'+
  '</div></div>');
 V('<div class="spin">deep read on '+ACTIVE+' — chart, structure, context, risk…</div>');
 fetch('/v2/analyse/'+ACTIVE).then(r=>r.json()).then(d=>{
  if(!d.ok)return V('<div class="card">Error: '+esc(d.error)+'</div>');
  window.LAST=d;
  const S=d.scores||{},C=d.context||{},RK=d.risk||{},W=d.wyckoff||{},
        rg=C.regime||{},rs=C.rs||{},sh=C.shariah||{},fu=C.fundamentals||{},
        sz=RK.sizing;
  const conflict=/conflict/.test(d.agreement||'');
  let h=`<div class="card"><div class="vhead">
    <div class="vsym">${d.symbol}</div><div class="vpx">${d.price}</div>
    <div class="badge ${d.class}">${d.verdict}</div>
    <div class="conf"><div class="confn">${d.confidence}</div>
     <div class="confl">confidence</div></div></div>
   <div class="bar"><i style="width:${d.confidence}%"></i></div>
   <div class="wyk">
    <div><b>composite</b><span>${d.composite}/100</span></div>
    <div><b>chart</b><span>${S.chart}</span></div>
    <div><b>structure</b><span>${S.structure==null?'—':S.structure}</span></div>
    <div><b>fundamentals</b><span>${S.fundamentals==null?'—':S.fundamentals}</span></div>
    <div><b>rel strength</b><span>${rs.rs_score==null?'—':rs.rs_score}</span></div>
    <div><b>risk level</b><span class="${RK.risk_level=='Low'?'pos':
      RK.risk_level=='High'?'neg':''}">${RK.risk_level||'—'}</span></div>
   </div>
   <div class="note" style="border-left:2px solid ${conflict?'var(--amb)':'var(--acc)'};
     padding-left:10px"><b>${conflict?'The two methods disagree.':
     'Chart and structure.'}</b> ${esc(d.agreement_note)}</div>
   ${d.verdict!=d.verdict_v1?`<div class="note" style="color:var(--amb)">
     <b>Verdict downgraded from ${d.verdict_v1} to ${d.verdict}.</b><br>${
     (d.downgrades||[]).map(x=>'· '+esc(x)).join('<br>')}</div>`:''}
   </div>`;

  if(sz)h+=`<div class="card"><h3>What to actually buy</h3><div class="plan">
    <div><b>shares</b><span>${sz.shares.toLocaleString()}</span></div>
    <div><b>position</b><span>Rs ${sz.position_pkr.toLocaleString()}</span></div>
    <div><b>% of capital</b><span>${sz.position_pct}%</span></div>
    <div><b>risk/share</b><span>${sz.risk_per_share}</span></div>
    <div><b>loss if stopped</b><span class="neg">Rs ${
      sz.max_loss_pkr.toLocaleString()}</span></div>
    <div><b>as % capital</b><span class="neg">${sz.max_loss_pct}%</span></div></div>
    <div class="note">Sized on Rs ${sz.capital.toLocaleString()} of capital. Set
     CAPITAL in the environment to change it.</div></div>`;

  h+=`<div class="card"><h3>Risk checks</h3>
    ${(RK.vetoes||[]).length?`<div class="note" style="color:var(--red)">
      <b>Blocking:</b> ${RK.vetoes.join(', ')}</div>`:
      '<div class="note" style="color:var(--grn)">No blocking risk conditions.</div>'}
    <ul class="rlist b6">${(RK.warnings||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ul>
    <div class="wyk"><div><b>avg value/day</b><span>Rs ${
     ((RK.metrics||{}).avg_value_pkr/1e6||0).toFixed(2)}M</span></div>
     <div><b>ATR</b><span>${(RK.metrics||{}).atr_pct}%</span></div>
     <div><b>headroom R:R</b><span>${(RK.metrics||{}).headroom_rr} / ${
      (RK.metrics||{}).rr_min} min</span></div>
     <div><b>above 20-EMA</b><span>${(RK.metrics||{}).extension_pct}%</span></div>
    </div></div>`;

  h+=`<div class="card"><h3>Market context</h3>
    <div class="note"><b>Regime.</b> ${esc(rg.note)}</div>
    ${rs.note?`<div class="note"><b>Relative strength.</b> ${esc(rs.note)}</div>`:''}
    <div class="note"><b>Shariah.</b> ${esc(sh.status)} — source ${esc(sh.source)}.
     ${(sh.notes||[]).map(esc).join(' ')}</div>
    <div class="note"><b>Fundamentals.</b> ${fu.score==null?
     'no data available; the technical read stands alone.':
     ('score '+fu.score+'/100'+(fu.peers_used?' against '+fu.peers_used+
      ' sector peers':' with no peer comparison')+'.')}<br>${
     (fu.notes||[]).map(x=>'· '+esc(x)).join('<br>')}</div></div>`;

  if(d.bull&&d.bull.length)h+=`<div class="card"><h3>Supporting the trade</h3>
    <ul class="rlist b4">${d.bull.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div>`;
  if(d.bear&&d.bear.length)h+=`<div class="card"><h3>Against the trade</h3>
    <ul class="rlist b5">${d.bear.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div>`;
  if(d.flags&&d.flags.length)h+=`<div class="card"><h3>Watch out</h3>
    <ul class="rlist b6">${d.flags.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div>`;

  if(W&&W.ok)h+=wyckCard(W,'WYCKOFF STRUCTURE (daily)');
  h+=`<div class="card"><h3>In one paragraph</h3>
    <div style="font-size:13.5px;line-height:1.75">${esc(d.summary)}</div>
    <div class="note">Confidence maths: ${(d.confidence_notes||[]).map(esc).join('; ')
      ||'model confidence unmodified'}.</div></div>`;
  V(h);});}

function wscanView(){
 let h=`<div class="card"><div class="vhead"><div class="vsym">WYCKOFF SCAN</div>
   <select id="wsk" style="max-width:150px">
    <option value="watchlist">my list</option><option value="kse100">KSE-100</option>
    <option value="all">whole market</option></select>
   <select id="wstf" style="max-width:120px">
    <option value="daily">daily</option><option value="weekly">weekly</option></select>
   <button class="p" onclick="startWscan()">SCAN</button></div>
   <div class="note">Ranks by structural readiness: Phase D and C first,
    accumulation over distribution, High-probability Springs and a held Last Point
    of Support pushing a name up, Upthrusts and Signs of Weakness pushing it down.</div>
   <div id="wsprog" class="note"></div></div><div id="wsout"></div>`;
 V(h);
 pollWscan();}

function startWscan(){
 fetch('/wyckoff/scan',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({kind:document.getElementById('wsk').value,
                       tf:document.getElementById('wstf').value})})
 .then(()=>pollWscan());}

function pollWscan(){
 fetch('/wyckoff/scanstatus').then(r=>r.json()).then(d=>{
  const p=document.getElementById('wsprog');if(!p)return;
  p.textContent=d.status=='running'?('scanning… '+(d.progress||'')):
    (d.when?('last scan '+d.when+' · '+d.progress):'not run yet');
  const o=document.getElementById('wsout');
  if(o&&d.rows&&d.rows.length){
   o.innerHTML=`<div class="card"><table class="t2">
    <tr><th>Sym</th><th>Price</th><th>Structure</th><th>Ph</th><th>Bias</th>
     <th>Support</th><th>Resist</th><th>In range</th><th>Spring</th>
     <th>Upthrust</th><th>SOS</th><th>SOW</th><th>Conf</th><th>Target</th></tr>`+
    d.rows.map(r=>`<tr onclick="ACTIVE='${r.symbol}';CUR='wyckoff';drawTabs();render()"
      style="cursor:pointer">
     <td><b>${r.symbol}</b></td><td>${r.price}</td>
     <td style="font-size:11.5px">${r.structure}</td>
     <td><b>${r.phase}</b></td>
     <td class="${/ullish/.test(r.bias)?'pos':/earish/.test(r.bias)?'neg':''}"
       style="font-size:11.5px">${r.bias}</td>
     <td>${r.support??'—'}</td><td>${r.resistance??'—'}</td>
     <td>${r.pos_pct==null?'—':r.pos_pct+'%'}</td>
     <td class="pos" style="font-size:11px">${r.spring||''}</td>
     <td class="neg" style="font-size:11px">${r.upthrust||''}</td>
     <td>${r.sos||''}</td><td>${r.sow||''}</td><td>${r.confidence}</td>
     <td class="pos">${r.target_up??'—'}</td></tr>`).join('')+
    `</table><div class="note">Click any row for the full structural read.
     A range is only reported where one genuinely exists — names in a clean trend
     are dropped rather than fitted with invented boundaries.</div></div>`;}
  if(d.status=='running'&&CUR=='wscan')setTimeout(pollWscan,3000);});}

function bookView(){
 V('<div class="spin">sizing the whole book…</div>');
 fetch('/v2/book',{method:'POST'}).then(r=>r.json()).then(d=>{
  if(!d.ok)return V(`<div class="card"><h3>Book risk</h3>
    <div class="note">${esc(d.error)}</div></div>`);
  const b=d.book;
  let h=`<div class="card"><div class="vhead"><div class="vsym">BOOK RISK</div>
    <div class="tag">capital Rs ${b.capital.toLocaleString()}</div></div>
   <div class="wyk">
    <div><b>positions</b><span>${b.positions}/${b.max_positions}</span></div>
    <div><b>total heat</b><span class="${b.heat_pct>b.max_heat_pct*0.8?'neg':'pos'}">${
      b.heat_pct}%</span></div>
    <div><b>heat ceiling</b><span>${b.max_heat_pct}%</span></div>
    <div><b>deployed</b><span>${b.deployed_pct}%</span></div>
    <div><b>cash</b><span>${b.cash_pct}%</span></div>
    <div><b>deferred</b><span>${b.deferred}</span></div></div>
   <div class="note">${esc(b.text)}</div>
   <div class="note">Heat is what you lose if every stop in the book fills on the
    same morning. Per-trade sizing cannot see that; this is the number that
    actually ends accounts.</div></div>`;

  if(d.admitted.length)h+=`<div class="card"><h3>Admitted</h3><table class="t2">
    <tr><th>Sym</th><th>Sector</th><th>Verdict</th><th>Price</th><th>Stop</th>
     <th>Shares</th><th>Value</th><th>Risk</th><th>Heat</th><th>Weight</th></tr>`+
    d.admitted.map(r=>`<tr><td><b>${r.symbol}</b></td><td>${r.sector}</td>
     <td>${r.verdict}</td><td>${r.price}</td><td class="neg">${r.stop}</td>
     <td>${r.shares.toLocaleString()}</td>
     <td>Rs ${r.value_pkr.toLocaleString()}</td>
     <td class="neg">Rs ${r.risk_pkr.toLocaleString()}</td>
     <td>${r.heat_pct}%</td><td>${r.weight_pct}%</td></tr>`).join('')+'</table></div>';

  if(d.deferred.length)h+=`<div class="card"><h3>Deferred by a cap</h3>
    <ul class="rlist b6">${d.deferred.map(r=>
     `<li><b>${r.symbol}</b> (${r.sector}) — ${esc(r.reason)}</li>`).join('')}</ul>
    <div class="note">Deferred, not rejected. These are good setups the book has
     no room for today.</div></div>`;
  if(d.unsizable.length)h+=`<div class="card"><h3>Unsizable</h3>
    <ul class="rlist b6">${d.unsizable.map(r=>
     `<li><b>${r.symbol}</b> — ${esc(r.reason)}</li>`).join('')}</ul></div>`;

  const sec=Object.entries(b.sectors||{});
  if(sec.length)h+=`<div class="card"><h3>Sector exposure</h3><table class="t2">
    <tr><th>Sector</th><th>Value</th><th>% capital</th><th>vs ${b.max_sector_pct}% cap</th></tr>`+
    sec.map(([k,v])=>`<tr><td>${k}</td><td>Rs ${v.value_pkr.toLocaleString()}</td>
     <td>${v.pct}%</td><td class="${v.pct>b.max_sector_pct*0.9?'neg':'pos'}">${
      (b.max_sector_pct-v.pct).toFixed(1)}% room</td></tr>`).join('')+'</table></div>';
  V(h);});}

function pfView(){
 V('<div class="spin">marking your book to market…</div>');
 fetch('/portfolio').then(r=>r.json()).then(d=>{
  const o=d.open,t=o.total,c=d.closed,cs=c.stats;
  let h=`<div class="card"><div class="vhead"><div class="vsym">PORTFOLIO</div>
    <div class="tag">${t.positions} open</div>
    ${t.unpriced?`<div class="tag">${t.unpriced} unpriced</div>`:''}</div>
   <div class="wyk">
    <div><b>cost</b><span>Rs ${(t.cost_pkr||0).toLocaleString()}</span></div>
    <div><b>value</b><span>Rs ${(t.value_pkr||0).toLocaleString()}</span></div>
    <div><b>open P/L</b><span class="${(t.pl_pkr||0)>=0?'pos':'neg'}">Rs ${
      (t.pl_pkr||0).toLocaleString()}</span></div>
    <div><b>open P/L %</b><span class="${(t.pl_pct||0)>=0?'pos':'neg'}">${
      t.pl_pct==null?'—':sgn(t.pl_pct)+'%'}</span></div></div></div>`;

  h+=`<div class="card"><h3>Add a position</h3>
   <div class="addbar"><input id="pfs" placeholder="SYMBOL" style="max-width:110px">
    <input id="pfq" placeholder="qty" style="max-width:90px">
    <input id="pfc" placeholder="avg cost" style="max-width:110px">
    <button class="p" onclick="pfAdd()">ADD</button></div>
   <div class="note">Stored locally in the SQLite file, never sent anywhere.</div></div>`;

  if(o.rows.length)h+=`<div class="card"><h3>Open positions</h3><table class="t2">
    <tr><th>Sym</th><th>Qty</th><th>Avg cost</th><th>Price</th><th>Cost</th>
     <th>Value</th><th>P/L</th><th>%</th><th></th></tr>`+
    o.rows.map(r=>`<tr><td><b>${r.symbol}</b></td><td>${r.qty}</td>
     <td>${r.avg_cost}</td><td>${r.price??'—'}</td>
     <td>Rs ${r.cost_pkr.toLocaleString()}</td>
     <td>${r.value_pkr==null?'—':'Rs '+r.value_pkr.toLocaleString()}</td>
     <td class="${(r.pl_pkr||0)>=0?'pos':'neg'}">${r.pl_pkr==null?'—':
      'Rs '+r.pl_pkr.toLocaleString()}</td>
     <td class="${(r.pl_pct||0)>=0?'pos':'neg'}">${r.pl_pct==null?'—':sgn(r.pl_pct)+'%'}</td>
     <td><button onclick="pfClose(${r.id},${r.price||0})">close</button>
      <button onclick="pfDel(${r.id})">×</button></td></tr>`).join('')+'</table></div>';
  else h+='<div class="card"><div class="note">No open positions recorded.</div></div>';

  if(cs.closed)h+=`<div class="card"><h3>Closed trades — your actual record</h3>
   <div class="wyk">
    <div><b>closed</b><span>${cs.closed}</span></div>
    <div><b>win rate</b><span>${cs.win_rate}%</span></div>
    <div><b>net</b><span class="${cs.net_pkr>=0?'pos':'neg'}">Rs ${
      cs.net_pkr.toLocaleString()}</span></div>
    <div><b>profit factor</b><span>${cs.profit_factor??'—'}</span></div>
    <div><b>expectancy</b><span class="${(cs.expectancy_pkr||0)>=0?'pos':'neg'}">Rs ${
      (cs.expectancy_pkr||0).toLocaleString()}</span></div>
    <div><b>avg win / loss</b><span>${(cs.avg_win_pkr||0).toLocaleString()} / ${
      (cs.avg_loss_pkr||0).toLocaleString()}</span></div></div>
   ${cs.caveat?`<div class="note" style="color:var(--amb)">${esc(cs.caveat)}</div>`:''}
   <table class="t2"><tr><th>Sym</th><th>Qty</th><th>In</th><th>Out</th>
    <th>P/L</th><th>%</th><th>Closed</th></tr>`+
   c.rows.map(r=>`<tr><td><b>${r.symbol}</b></td><td>${r.qty}</td>
    <td>${r.avg_cost}</td><td>${r.exit_px}</td>
    <td class="${r.pl_pkr>=0?'pos':'neg'}">Rs ${r.pl_pkr.toLocaleString()}</td>
    <td class="${r.pl_pct>=0?'pos':'neg'}">${sgn(r.pl_pct)}%</td>
    <td style="color:var(--dim)">${(r.closed||'').slice(0,10)}</td></tr>`).join('')+
   '</table></div>';
  V(h);});}

function pfAdd(){
 const s=document.getElementById('pfs').value.trim().toUpperCase(),
       q=parseFloat(document.getElementById('pfq').value),
       c=parseFloat(document.getElementById('pfc').value);
 if(!s||!q||!c)return alert('Symbol, quantity and average cost are all needed.');
 fetch('/portfolio/add',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({symbol:s,qty:q,avg_cost:c})}).then(()=>pfView());}
function pfClose(id,px){
 const v=prompt('Exit price:',px||'');if(!v)return;
 fetch('/portfolio/close/'+id,{method:'POST',
  headers:{'Content-Type':'application/json'},
  body:JSON.stringify({exit_px:parseFloat(v)})}).then(()=>pfView());}
function pfDel(id){if(!confirm('Delete this position record?'))return;
 fetch('/portfolio/del/'+id,{method:'POST'}).then(()=>pfView());}

function learnView(){
 V('<div class="spin">reading the journal…</div>');
 fetch('/learning'+(ACTIVE?'?sym='+ACTIVE:'')).then(r=>r.json()).then(d=>{
  let h=`<div class="card"><div class="vhead"><div class="vsym">TRACK RECORD</div>
    ${ACTIVE?`<div class="tag">${ACTIVE} only</div>`:'<div class="tag">all symbols</div>'}
    <button class="p" style="margin-left:auto" onclick="gradeNow()">GRADE PENDING</button>
    </div>
   <div class="note"><b>Confidence adjustment: ${sgn(d.adjustment)} points.</b>
    ${esc(d.note)}</div>
   <div class="note">Every Deep Read is journalled with the price and levels that
    were true at the time. Grading compares that to what price actually did.
    History moves CONFIDENCE only — the model's weights never change, so you can
    watch it be wrong instead of having it quietly rewrite itself.</div></div>`;

  if(d.accuracy.length)h+=`<div class="card"><h3>Verdict outcomes</h3><table class="t2">
    <tr><th>Verdict</th><th>Outcome</th><th>Count</th></tr>`+
    d.accuracy.map(r=>`<tr><td><b>${r.verdict}</b></td>
     <td class="${r.outcome=='worked'?'pos':r.outcome=='failed'?'neg':''}">${
      r.outcome}</td><td>${r.n}</td></tr>`).join('')+'</table></div>';

  if(d.features.length)h+=`<div class="card"><h3>Which signals are working</h3>
   <table class="t2"><tr><th>Feature</th><th>Seen</th><th>Hits</th><th>Misses</th>
    <th>Win rate</th><th>Reliable?</th></tr>`+
   d.features.map(f=>`<tr><td><b>${f.feature}</b></td><td>${f.n}</td>
    <td class="pos">${f.hits}</td><td class="neg">${f.misses}</td>
    <td>${f.win_rate==null?'—':f.win_rate+'%'}</td>
    <td class="${f.reliable?'pos':'neg'}">${f.reliable?'yes (20+)':'too few'}</td>
    </tr>`).join('')+`</table>
   <div class="note">Anything under 20 observations is noise. Do not act on a
    feature marked "too few" no matter how good its win rate looks.</div></div>`;

  if(d.history.length)h+=`<div class="card"><h3>Journal</h3><table class="t2">
   <tr><th>When</th><th>Sym</th><th>Verdict</th><th>Conf</th><th>Price</th>
    <th>Phase</th><th>+1d</th><th>+3d</th><th>+7d</th><th>+20d</th>
    <th>Outcome</th></tr>`+
   d.history.map(r=>`<tr><td style="color:var(--dim)">${(r.run_time||'').slice(0,16)}</td>
    <td><b>${r.symbol}</b></td><td>${r.verdict||''}</td><td>${r.confidence??''}</td>
    <td>${r.price??''}</td><td>${r.wyckoff_phase||''}</td>
    ${[r.d1,r.d3,r.d7,r.d20].map(v=>`<td class="${(v||0)>=0?'pos':'neg'}">${
      v==null?'':sgn(v)+'%'}</td>`).join('')}
    <td class="${r.outcome=='worked'?'pos':r.outcome=='failed'?'neg':''}">${
      r.outcome||'pending'}</td></tr>`).join('')+'</table></div>';
  else h+='<div class="card"><div class="note">Journal is empty. Open DEEP READ on '+
    'a few names and they will be recorded automatically.</div></div>';
  V(h);});}

function gradeNow(){
 fetch('/learning/grade',{method:'POST'}).then(r=>r.json()).then(d=>{
  alert(d.ok?`Graded ${d.graded}, skipped ${d.skipped} (not enough forward bars yet).`
           :('Error: '+d.error));
  learnView();});}

drawTabs();loadLists();loadWL();render();
setInterval(loadWL,120000);
</script></body></html>
"""


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close(); return ip
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    ip = local_ip()
    print("\n" + "=" * 58)
    print("  PSX RESEARCH TERMINAL 2.0")
    print("  chart + Wyckoff + context + risk + memory")
    print(f"  This PC:  http://localhost:5000")
    print(f"  Phone:    http://{ip}:5000   (same WiFi)")
    print("  Ctrl+C to stop.")
    print("=" * 58 + "\n")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)
