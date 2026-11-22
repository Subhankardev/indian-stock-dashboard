"""Paper trading (Phase 7). Simulated only — this module never talks to a broker.

Life of a paper trade:
    pending → open (filled) → closed (target / stop / time / manual), or pending → expired

Fill rules (conservative, daily bars):
* entry "breakout": fills on the first of the next ``pending_expiry_sessions`` sessions that
  trades through the trigger, at max(open, trigger) for longs (a gap above fills at the open);
  entry "close": fills at the next session's open.
* exits: a gap through the stop exits at the open; if one bar touches both stop and target,
  the **stop** is assumed first; otherwise stop, then target ``exit_target_index``; after
  ``time_exit_sessions`` sessions the trade exits at the close.
* P&L is after the full cost model in ``intel.setups.costs``.

    python -m intel.paper --update      # process new bars for pending/open trades
    python -m intel.paper --auto        # open trades for the top ranked (tested factors only)
    python -m intel.paper --report
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import insert, select, update

from . import db
from .config import load
from .setups import check_limits, costs, setup_for


def trades(eng, status: Optional[list] = None) -> pd.DataFrame:
    stmt = select(db.paper_trades)
    if status:
        stmt = stmt.where(db.paper_trades.c.status.in_(status))
    return db.read_sql(eng, stmt.order_by(db.paper_trades.c.id))


def open_trade(eng, setup: dict, notes: str = "", enforce_limits: bool = True) -> dict:
    if not setup.get("valid"):
        return {"ok": False, "reasons": [setup.get("reason") or "invalid setup"]}
    if enforce_limits:
        problems = check_limits(setup, trades(eng, ["pending", "open"]))
        if problems:
            return {"ok": False, "reasons": problems}
    t = setup["targets"] + [None] * (3 - len(setup["targets"]))
    with eng.begin() as c:
        res = c.execute(insert(db.paper_trades).values(
            symbol=setup["symbol"], direction=setup["direction"], opened=pd.Timestamp(setup["as_of"]).date(),
            trigger=setup["trigger"], entry=None, stop=setup["stop"], target1=t[0], target2=t[1], target3=t[2],
            qty=setup["qty"], status="pending", industry=setup.get("industry"),
            notes=json.dumps({"note": notes, "setup": setup}, default=str),
            created=datetime.now(timezone.utc).replace(tzinfo=None)))
        tid = int(res.inserted_primary_key[0])
    _event(eng, "paper_open", setup["symbol"], setup["direction"], pd.Timestamp(setup["as_of"]).date(),
           f"Paper {('buy' if setup['direction'] == 'bullish' else 'short')} set: {setup['symbol']}",
           f"trigger {setup['trigger']} · stop {setup['stop']} · targets {setup['targets']} · qty {setup['qty']}",
           {"trade_id": tid})
    return {"ok": True, "id": tid}


def _event(eng, kind, symbol, direction, day, title, detail, data=None):
    from .alerts import add
    add(eng, kind=kind, symbol=symbol, direction=direction, day=day, title=title, detail=detail, data=data or {})


def _exit_target(tr: pd.Series, cfg: dict) -> float:
    idx = cfg["trading"].get("exit_target_index", 0)
    vals = [tr["target1"], tr["target2"], tr["target3"]]
    vals = [v for v in vals if v is not None and not pd.isna(v)]
    return float(vals[min(idx, len(vals) - 1)])


def step(tr: dict, bars: pd.DataFrame, cfg: dict) -> dict:
    """Advance one trade through ``bars`` (dates after its last processed date). Pure function."""
    t = cfg["trading"]
    long_ = tr["direction"] == "bullish"
    sgn = 1 if long_ else -1
    tr = dict(tr)
    for d, b in bars.iterrows():
        day = pd.Timestamp(d).date()
        if tr["status"] == "pending":
            if t["entry"] == "close":
                fill = float(b["open"])
            else:
                hit = b["high"] >= tr["trigger"] if long_ else b["low"] <= tr["trigger"]
                if not hit:
                    tr["_waited"] = tr.get("_waited", 0) + 1
                    if tr["_waited"] >= t["pending_expiry_sessions"]:
                        tr.update(status="expired", closed=day, exit_reason="not triggered")
                        return tr
                    continue
                fill = max(float(b["open"]), tr["trigger"]) if long_ else min(float(b["open"]), tr["trigger"])
            tr.update(status="open", entry=fill, filled=day, _held=0, _events=tr.get("_events", []) + [("fill", day)])
            # the fill bar itself can still hit the stop (assumed after the fill)
        if tr["status"] == "open":
            tr["_held"] = tr.get("_held", 0) + (0 if tr.get("filled") == day else 1)
            stop, target = tr["stop"], tr["_target"]
            o, h, low, c = float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"])
            exit_px = reason = None
            gapped = (o <= stop) if long_ else (o >= stop)
            if gapped and tr.get("filled") != day:
                exit_px, reason = o, "stop (gap)"
            elif (low <= stop) if long_ else (h >= stop):
                exit_px, reason = stop, "stop"
            elif (h >= target) if long_ else (low <= target):
                exit_px, reason = (max(o, target) if long_ else min(o, target)) if tr.get("filled") != day else target, "target"
            elif tr["_held"] >= t["time_exit_sessions"]:
                exit_px, reason = c, "time"
            if exit_px is not None:
                buy, sell = (tr["entry"], exit_px) if long_ else (exit_px, tr["entry"])
                cst = costs(buy * tr["qty"], sell * tr["qty"], cfg)["total"]
                gross = sgn * (exit_px - tr["entry"]) * tr["qty"]
                risk = abs(tr["entry"] - tr["stop"]) * tr["qty"]
                tr.update(status="closed", closed=day, exit_price=float(exit_px), exit_reason=reason,
                          costs=float(cst), pnl=float(gross - cst), r_multiple=float((gross - cst) / risk) if risk else None)
                return tr
    return tr


def update_all(eng, cfg: Optional[dict] = None) -> dict:
    """Process all pending/open trades against stored daily bars."""
    cfg = cfg or load()
    live = trades(eng, ["pending", "open"])
    counts = {"filled": 0, "closed": 0, "expired": 0, "unchanged": 0}
    for _, row in live.iterrows():
        tr = row.to_dict()
        since = tr["filled"] if tr["status"] == "open" and tr.get("filled") else tr["opened"]
        bars = db.read_sql(eng, select(db.prices_daily.c.date, db.prices_daily.c.open, db.prices_daily.c.high,
                                       db.prices_daily.c.low, db.prices_daily.c.close)
                           .where(db.prices_daily.c.symbol == tr["symbol"], db.prices_daily.c.date > tr["opened"])
                           .order_by(db.prices_daily.c.date))
        if bars.empty:
            counts["unchanged"] += 1
            continue
        bars = bars.drop_duplicates("date").set_index("date")
        tr["_target"] = _exit_target(row, cfg)
        if tr["status"] == "open":
            # replay every bar since the fill: the state is deterministic, so this is idempotent
            held = bars[bars.index > since]
            tr["_held"] = 0
            new = step(tr, held, cfg) if len(held) else tr
        else:
            new = step(tr, bars, cfg)
        if new["status"] == row["status"] and new.get("entry") == row.get("entry"):
            counts["unchanged"] += 1
            continue
        vals = {k: new.get(k) for k in ("status", "entry", "filled", "closed", "exit_price", "exit_reason", "costs",
                                         "pnl", "r_multiple")}
        vals = {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in vals.items()}
        with eng.begin() as c:
            c.execute(update(db.paper_trades).where(db.paper_trades.c.id == int(row["id"])).values(**vals))
        if row["status"] == "pending" and new.get("entry") is not None:
            counts["filled"] += 1
            _event(eng, "paper_fill", tr["symbol"], tr["direction"], new["filled"],
                   f"Paper fill: {tr['symbol']} @ {new['entry']:.2f}", f"stop {tr['stop']} · target {tr['_target']:.2f}",
                   {"trade_id": int(row["id"])})
        if new["status"] == "closed":
            counts["closed"] += 1
            _event(eng, "paper_exit", tr["symbol"], tr["direction"], new["closed"],
                   f"Paper exit ({new['exit_reason']}): {tr['symbol']} @ {new['exit_price']:.2f}",
                   f"P&L ₹{new['pnl']:,.0f} after ₹{new['costs']:,.0f} costs · {new['r_multiple']:+.2f}R",
                   {"trade_id": int(row["id"])})
        elif new["status"] == "expired":
            counts["expired"] += 1
    return counts


def close_manual(eng, trade_id: int, price: float, day) -> dict:
    cfg = load()
    row = trades(eng)
    row = row[row["id"] == trade_id]
    if row.empty:
        raise KeyError(trade_id)
    tr = row.iloc[0].to_dict()
    if tr["status"] == "pending":
        vals = {"status": "cancelled", "closed": day, "exit_reason": "cancelled"}
    elif tr["status"] == "open":
        long_ = tr["direction"] == "bullish"
        buy, sell = (tr["entry"], price) if long_ else (price, tr["entry"])
        cst = costs(buy * tr["qty"], sell * tr["qty"], cfg)["total"]
        gross = (1 if long_ else -1) * (price - tr["entry"]) * tr["qty"]
        risk = abs(tr["entry"] - tr["stop"]) * tr["qty"]
        vals = {"status": "closed", "closed": day, "exit_price": price, "exit_reason": "manual", "costs": cst,
                "pnl": gross - cst, "r_multiple": (gross - cst) / risk if risk else None}
    else:
        return {"ok": False, "reason": f"trade is {tr['status']}"}
    with eng.begin() as c:
        c.execute(update(db.paper_trades).where(db.paper_trades.c.id == trade_id).values(**vals))
    return {"ok": True, **vals}


def report(eng) -> dict:
    t = trades(eng)
    capital = float(load()["trading"]["capital"])
    closed = t[t["status"] == "closed"].sort_values("closed") if len(t) else t
    out = {"capital": capital, "counts": t["status"].value_counts().to_dict() if len(t) else {}}
    if len(closed):
        pnl = closed["pnl"].astype(float)
        out.update(trades=len(closed), win_rate=float((pnl > 0).mean()), total_pnl=float(pnl.sum()),
                   total_costs=float(closed["costs"].sum()), avg_r=float(closed["r_multiple"].mean()),
                   expectancy=float(pnl.mean()), best=float(pnl.max()), worst=float(pnl.min()),
                   by_reason=closed["exit_reason"].value_counts().to_dict())
        eq = capital + pnl.cumsum()
        out["equity"] = [{"date": str(d), "equity": float(v)} for d, v in zip(closed["closed"], eq)]
        peak = eq.cummax()
        out["max_drawdown_pct"] = float(((eq - peak) / peak).min() * 100)
    live = t[t["status"] == "open"] if len(t) else t
    out["open_exposure"] = float((live["qty"] * live["entry"]).sum()) if len(live) else 0.0
    return out


def auto(eng, panel: pd.DataFrame, cfg: Optional[dict] = None) -> dict:
    """Open paper trades for the top-N bullish names — only when factors have passed their tests."""
    from .scoring import rankings
    cfg = cfg or load()
    r = rankings(eng, panel, preview=False, limit=cfg["trading"]["auto_top_n"], cfg=cfg)
    if not r["bullish"]:
        return {"opened": [], "skipped": [], "message": r.get("message") or "nothing ranked"}
    opened, skipped = [], []
    for item in r["bullish"]:
        g = panel[panel["symbol"] == item["symbol"]]
        s = setup_for(g.iloc[-1], g, "bullish", cfg)
        res = open_trade(eng, s, notes=f"auto: rank score {item['score']:.0f}, confidence {item['confidence']:.2f}")
        (opened if res["ok"] else skipped).append({"symbol": item["symbol"], **res})
    return {"opened": opened, "skipped": skipped}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    eng = db.engine()
    if a.update:
        print("Update:", update_all(eng))
    if a.auto:
        from .panel import build_panel
        from .scoring import enrich
        print("Auto:", auto(eng, enrich(eng, build_panel(eng))))
    if a.report or not (a.update or a.auto):
        print(json.dumps(report(eng), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
