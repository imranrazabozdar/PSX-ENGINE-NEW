"""portfolio_advisor.py — Turns the engine's per-stock signals into a strategy
for YOUR actual book: existing holdings + ready cash → P/L and a per-position
action (hold / add / average down / trim / exit), plus how to deploy spare cash
into new Buys.

Inputs are plain dicts so this stays pure/testable and the dashboard just renders
it. Sizing reuses the risk rules in config.RISK (per-trade risk %, position cap).

Averaging-down policy = CONSERVATIVE (Fahad, 2026-06-15): only suggest averaging
down when the engine STILL rates the stock Buy/Strong Buy — which already implies
it is not extended and not breaking down (those downgrade the signal), the thesis
is intact, and the position is under the per-stock cap. Never average a falling
knife.
"""

import json
import logging

import config

log = logging.getLogger("portfolio")

_DEPLOY_ACTIONS = ("AVERAGE DOWN", "ADD", "NEW POSITION")
_SIG_RANK = {"Strong Buy": 0, "Buy": 1}


def load_portfolio(path=None):
    """Read portfolio.json → {'cash_pkr': float, 'holdings': [..]}. Tolerant of a
    missing/blank file (returns an empty book)."""
    path = path or config.PORTFOLIO_PATH
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"cash_pkr": 0.0, "holdings": []}
    holds = []
    for h in d.get("holdings", []):
        try:
            holds.append({"symbol": str(h["symbol"]).upper(),
                          "qty": float(h["qty"]),
                          "avg_cost": float(h["avg_cost"])})
        except (KeyError, TypeError, ValueError):
            continue
    return {"cash_pkr": float(d.get("cash_pkr") or 0), "holdings": holds}


def _size(price, stop, cash_left, equity, current_value):
    """Risk-based share count, bounded by per-trade risk, remaining position-cap
    room, and the cash still available. Returns (shares, value)."""
    if not price or price <= 0 or cash_left <= 0:
        return 0, 0.0
    cap_value = equity * config.RISK["max_position_pct"] / 100
    room_value = max(0.0, cap_value - current_value)
    by_cap = room_value / price
    by_cash = cash_left / price
    if stop and price > stop:
        max_loss = equity * config.RISK["max_risk_per_trade_pct"] / 100
        by_risk = max_loss / (price - stop)
    else:
        by_risk = by_cash            # no valid stop -> let cash/cap bind
    shares = int(max(0, min(by_risk, by_cap, by_cash)))
    return shares, round(shares * price, 0)


def advise(portfolio, latest):
    """portfolio: {'cash_pkr', 'holdings':[{symbol,qty,avg_cost}]}.
    latest: {SYMBOL: latest-run dict} (needs price, signal, stop_loss, final_score,
            buy_zone_low/high, risk_level).
    Returns {'holdings':[...], 'deploy':[...], 'totals':{...}}."""
    cash = float(portfolio.get("cash_pkr") or 0)
    holds = portfolio.get("holdings", [])
    held_syms = {h["symbol"] for h in holds}

    # --- value the book first (need total equity for position-cap %)
    rows, market_value, cost_value = [], 0.0, 0.0
    for h in holds:
        sym = h["symbol"]
        r = latest.get(sym)
        price = (r or {}).get("price")
        qty, avg = h["qty"], h["avg_cost"]
        cost = qty * avg
        cost_value += cost
        if price:
            mv = qty * price
            market_value += mv
        rows.append({"symbol": sym, "qty": qty, "avg_cost": avg, "price": price,
                     "cost": cost, "run": r})
    equity = market_value + cash

    # --- per-holding action
    holdings_out, deploy_ideas = [], []
    for row in rows:
        sym, r, price = row["symbol"], row["run"], row["price"]
        qty, avg, cost = row["qty"], row["avg_cost"], row["cost"]
        mv = qty * price if price else None
        pl = (mv - cost) if mv is not None else None
        pl_pct = (pl / cost * 100) if (pl is not None and cost) else None
        pos_pct = (mv / equity * 100) if (mv and equity) else 0.0
        signal = (r or {}).get("signal")
        stop = (r or {}).get("stop_loss")
        under_cap = pos_pct < config.RISK["max_position_pct"]

        if r is None:
            action, detail = "NO COVERAGE", "Not in the engine universe — tracked for P/L only."
        elif signal == "Exit":
            action, detail = "EXIT", "Engine flags Exit — close the position."
        elif price and stop and price <= stop:
            action, detail = "EXIT (stop hit)", f"Price ≤ stop {stop:.2f} — exit per plan."
        elif signal == "Avoid":
            action, detail = "TRIM / EXIT", "Thesis broken (Avoid) — reduce; do not add."
        elif signal in ("Buy", "Strong Buy"):
            if pl_pct is not None and pl_pct < 0:          # underwater
                if under_cap and cash > 0:
                    action = "AVERAGE DOWN"
                    detail = (f"Still {signal} (thesis intact, not extended) & under "
                              f"{config.RISK['max_position_pct']:.0f}% cap — averaging "
                              "lowers your cost.")
                else:
                    action = "HOLD"
                    detail = ("Underwater but at/over the position cap — hold, don't add.")
            else:                                          # in profit
                if under_cap and cash > 0:
                    action = "ADD"
                    detail = (f"Winner, still {signal} — room under cap to add "
                              "(prefer the buy-zone on a pullback).")
                else:
                    action = "HOLD (let it run)"
                    detail = "Winner but at the position cap — ride it, don't add."
        elif signal == "Watch":
            action = "HOLD"
            bz = _zone_txt(r)
            detail = f"Engine on Watch — hold." + (f" {bz}" if bz else "")
        else:
            action, detail = "HOLD", f"Engine: {signal or 'No data'} — hold."

        holdings_out.append({
            "symbol": sym, "qty": qty, "avg_cost": avg, "price": price,
            "value": mv, "pl": pl, "pl_pct": pl_pct, "pos_pct": pos_pct,
            "signal": signal, "action": action, "detail": detail})

        if action in ("AVERAGE DOWN", "ADD"):
            deploy_ideas.append({"symbol": sym, "signal": signal, "price": price,
                                 "stop": stop, "score": (r or {}).get("final_score") or 0,
                                 "kind": action, "current_value": mv or 0})

    # --- new-position candidates: Buy/Strong Buy not already held
    for sym, r in latest.items():
        if sym in held_syms:
            continue
        if r.get("signal") in ("Buy", "Strong Buy") and r.get("price"):
            deploy_ideas.append({"symbol": sym, "signal": r["signal"],
                                 "price": r["price"], "stop": r.get("stop_loss"),
                                 "score": r.get("final_score") or 0,
                                 "kind": "NEW POSITION", "current_value": 0})

    # --- allocate cash sequentially, best conviction first (Strong Buy, then score)
    deploy_ideas.sort(key=lambda d: (_SIG_RANK.get(d["signal"], 9), -d["score"]))
    cash_left, deploy_out = cash, []
    for d in deploy_ideas:
        shares, value = _size(d["price"], d["stop"], cash_left, equity, d["current_value"])
        if shares <= 0:
            continue
        cash_left -= value
        d2 = dict(d); d2["shares"] = shares; d2["value"] = value
        deploy_out.append(d2)

    totals = {
        "cash": cash, "invested_cost": round(cost_value, 0),
        "market_value": round(market_value, 0), "equity": round(equity, 0),
        "pl": round(market_value - cost_value, 0) if cost_value else 0.0,
        "pl_pct": round((market_value - cost_value) / cost_value * 100, 1) if cost_value else None,
        "deployed_pct": round(market_value / equity * 100, 1) if equity else 0.0,
        "cash_after_plan": round(cash_left, 0), "n_holdings": len(holds)}
    return {"holdings": holdings_out, "deploy": deploy_out, "totals": totals}


def _zone_txt(r):
    lo, hi = (r or {}).get("buy_zone_low"), (r or {}).get("buy_zone_high")
    return f"Buy-zone {lo:.2f}–{hi:.2f}." if lo and hi else ""
