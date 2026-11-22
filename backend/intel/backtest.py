"""Portfolio backtests, walk-forward and sensitivity (Phase 8, docs/DESIGN.md §11.4–5).

Two strategies, both survivorship-free (the universe on each date is every stock that traded
and passed the liquidity filters *then*) and decided only from data at the close of day t,
traded at the open of t+1:

* **ranked**: every ``rebalance`` sessions, hold the top-K liquid stocks by the score built
  from a chosen factor set (equal weight, confidence ≥ min). Costs on every rupee traded,
  with slippage by liquidity tier and a capacity cap (≤ 5% of 20-day traded value).
* **signal**: enter on a research signal with the Phase 7 setup (trigger, ATR/swing stop,
  targets, risk sizing) and conservative paper-trade fills; at most ``max_positions`` at once.

Reports: CAGR, volatility, Sharpe, Sortino, max drawdown, Calmar, hit rate, profit factor,
expectancy, turnover, exposure, beta and information ratio vs Nifty 50 / Nifty 500, and
calendar-year returns.

    python -m intel.backtest --factors relative_strength,momentum --top 20 --rebalance 5
    python -m intel.backtest --signal breakout_volume
    python -m intel.backtest --walk-forward
    python -m intel.backtest --sensitivity --factors relative_strength,momentum
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import insert, select

from . import db
from .config import load
from .panel import load_index
from .setups import costs as cost_model

TRADING_DAYS = 250


# ------------------------------------------------------------------ metrics --- #
def metrics(equity: pd.Series, rf_pct: float = 6.5, bench: Optional[dict] = None) -> dict:
    eq = equity.dropna()
    if len(eq) < 2:
        return {}
    r = eq.pct_change().dropna()
    years = max(len(r) / TRADING_DAYS, 1e-9)
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1
    vol = r.std() * math.sqrt(TRADING_DAYS)
    rf_d = rf_pct / 100 / TRADING_DAYS
    ex = r - rf_d
    downside = r[r < 0].std() * math.sqrt(TRADING_DAYS)
    dd = eq / eq.cummax() - 1
    out = {"start": str(eq.index[0].date()), "end": str(eq.index[-1].date()), "years": round(years, 2),
           "total_return_pct": float((eq.iloc[-1] / eq.iloc[0] - 1) * 100), "cagr_pct": float(cagr * 100),
           "volatility_pct": float(vol * 100),
           "sharpe": float(ex.mean() / r.std() * math.sqrt(TRADING_DAYS)) if r.std() > 0 else None,
           "sortino": float(ex.mean() * TRADING_DAYS / downside) if downside and downside > 0 else None,
           "max_drawdown_pct": float(dd.min() * 100),
           "calmar": float(cagr / abs(dd.min())) if dd.min() < 0 else None}
    yearly = eq.groupby(eq.index.year).agg(["first", "last"])
    prev_last = yearly["last"].shift(1).fillna(yearly["first"])
    out["by_year"] = {str(y): float((row["last"] / prev_last[y] - 1) * 100) for y, row in yearly.iterrows()}
    for name, b in (bench or {}).items():
        b = b.reindex(eq.index).ffill().dropna()
        if len(b) < 2:
            continue
        br = b.pct_change().reindex(r.index).fillna(0)
        bcagr = (b.iloc[-1] / b.iloc[0]) ** (1 / years) - 1
        cov = np.cov(r, br)[0, 1] if br.std() > 0 else np.nan
        active = r - br
        byear = b.groupby(b.index.year).agg(["first", "last"])
        bprev = byear["last"].shift(1).fillna(byear["first"])
        out[f"vs_{name}"] = {
            "bench_cagr_pct": float(bcagr * 100), "excess_cagr_pct": float((cagr - bcagr) * 100),
            "beta": float(cov / br.var()) if br.var() > 0 else None,
            "information_ratio": float(active.mean() / active.std() * math.sqrt(TRADING_DAYS)) if active.std() > 0 else None,
            "bench_max_drawdown_pct": float((b / b.cummax() - 1).min() * 100),
            "bench_by_year": {str(y): float((row["last"] / bprev[y] - 1) * 100) for y, row in byear.iterrows()}}
    return out


def _side_cost_pct(value: float, avg_value_cr: float, cfg: dict) -> float:
    """Charges per side as a fraction of value, using tiered slippage (design §11.4)."""
    c = dict(cfg["costs"])
    c["slippage_bps"] = c["slippage_bps_liquid"] if (avg_value_cr or 0) >= c["liquid_tier_cr"] else c["slippage_bps_mid"]
    rt = cost_model(value, value, {"costs": c})["total"]
    return rt / (2 * value) if value > 0 else 0.0


# ------------------------------------------------------------------- ranked --- #
def ranked(panel: pd.DataFrame, factors: list[str], top_k: int = 20, rebalance: int = 5,
           cfg: Optional[dict] = None, start=None, end=None, cost_mult: float = 1.0,
           capital: Optional[float] = None) -> dict:
    """Equal-weight top-K by the score from ``factors``; rebalance every ``rebalance`` sessions."""
    from .scoring import score
    cfg = cfg or load()
    capital = float(capital or cfg["trading"]["capital"])
    weights, min_conf = cfg["scoring"]["weights"], cfg["scoring"]["min_confidence"]
    cap_pct = cfg["backtest"]["capacity_pct_of_value"] / 100
    p = panel
    if start is not None:
        p = p[p["date"] >= pd.Timestamp(start)]
    if end is not None:
        p = p[p["date"] <= pd.Timestamp(end)]
    dates = sorted(p["date"].unique())
    if len(dates) < rebalance + 2:
        return {"equity": pd.Series(dtype=float), "trades": 0, "turnover": 0.0}
    opens = p.pivot_table(index="date", columns="symbol", values="open")
    closes = p.pivot_table(index="date", columns="symbol", values="close").ffill()  # suspended days keep last price
    avgval = p.pivot_table(index="date", columns="symbol", values="avg_value_cr")
    cash, holdings = capital, {}          # symbol -> shares
    equity = {}
    traded_value = 0.0
    n_trades = 0
    exposure = []
    reb_days = set(dates[i] for i in range(0, len(dates) - 1, rebalance))
    first_invested = None
    for i, d in enumerate(dates):
        # mark to market at the close
        px = closes.loc[d]
        val = cash + sum(sh * (px.get(s) if pd.notna(px.get(s)) else 0.0) for s, sh in holdings.items())
        equity[d] = val
        exposure.append((val - cash) / val if val > 0 else 0.0)
        if d not in reb_days or i + 1 >= len(dates):
            continue
        snap = p[p["date"] == d]
        snap = snap[snap["liquid"].fillna(False).astype(bool)]
        sc = score(snap, set(factors), weights, "bullish").join(snap[["symbol"]])
        sc = sc[sc["bullish_confidence"] >= min_conf].dropna(subset=["bullish_score"])
        target = list(sc.sort_values("bullish_score", ascending=False)["symbol"].head(top_k))
        nxt = dates[i + 1]
        o = opens.loc[nxt]
        # sell what is no longer wanted (or untradeable tomorrow keeps its position)
        for s in list(holdings):
            if s not in target and pd.notna(o.get(s)):
                v = holdings.pop(s) * o[s]
                cash += v - v * _side_cost_pct(v, avgval.loc[d].get(s, 0), cfg) * cost_mult
                traded_value += v
                n_trades += 1
        # equal-weight the targets at tomorrow's open
        port_val = cash + sum(sh * (o.get(s) if pd.notna(o.get(s)) else closes.loc[d].get(s, 0)) for s, sh in holdings.items())
        per = port_val / max(top_k, 1)
        for s in target:
            price = o.get(s)
            if pd.isna(price) or price <= 0:
                continue
            cap = cap_pct * float(avgval.loc[d].get(s, 0) or 0) * 1e7
            want = min(per, cap) if cap > 0 else per
            have = holdings.get(s, 0) * price
            delta = want - have
            if abs(delta) < 0.05 * per:          # skip tiny rebalances
                continue
            if delta > 0:
                delta = min(delta, cash / (1 + 0.01))
            if delta <= 0 and have == 0:
                continue
            c = abs(delta) * _side_cost_pct(abs(delta), avgval.loc[d].get(s, 0), cfg) * cost_mult
            holdings[s] = holdings.get(s, 0) + delta / price
            cash -= delta + c
            if first_invested is None:
                first_invested = nxt
            traded_value += abs(delta)
            n_trades += 1
            if holdings[s] <= 1e-9:
                holdings.pop(s)
    eq = pd.Series(equity).sort_index()
    if first_invested is not None:
        # start the record the session before the first position: indicator warm-up (no stock
        # qualifying yet) would otherwise show as idle cash and distort CAGR
        k = max(eq.index.get_loc(first_invested) - 1, 0)
        eq = eq.iloc[k:]
        exposure = exposure[k:]
    years = max(len(eq) / TRADING_DAYS, 1e-9)
    return {"equity": eq, "trades": n_trades, "turnover": float(traded_value / capital / years),
            "exposure_pct": float(np.mean(exposure) * 100) if exposure else 0.0}


# ------------------------------------------------------------------- signal --- #
def signal_portfolio(panel: pd.DataFrame, mask: pd.Series, direction: str = "bullish",
                     cfg: Optional[dict] = None, start=None, end=None) -> dict:
    """Trade every signal event with the Phase 7 setup and fill rules, max_positions at once."""
    from .paper import step
    from .setups import setup_for
    cfg = cfg or load()
    capital = float(cfg["trading"]["capital"])
    max_pos = cfg["backtest"]["max_positions"]
    need = [c for c in panel.columns if not c.startswith(("p_", "q_", "c_"))]
    p = panel[need].copy()   # de-fragmented copy; factor columns aren't needed here
    mask = mask.reindex(p.index).fillna(False).astype(bool)
    if start is not None:
        keep = p["date"] >= pd.Timestamp(start)
        p, mask = p[keep], mask[keep]
    if end is not None:
        keep = p["date"] <= pd.Timestamp(end)
        p, mask = p[keep], mask[keep]
    by_sym = {s: g.set_index("date") for s, g in p.groupby("symbol")}
    events = p[mask].sort_values("date")
    busy: list = []                               # exit dates of active trades
    held: dict = {}                               # symbol -> exit date
    closed = []
    for _, ev in events.iterrows():
        d = ev["date"]
        busy = [x for x in busy if x > d]
        if len(busy) >= max_pos or (ev["symbol"] in held and held[ev["symbol"]] > d):
            continue
        g = by_sym[ev["symbol"]]
        hist = g[g.index <= d].reset_index()
        s = setup_for(ev, hist, direction, cfg)
        if not s.get("valid"):
            continue
        fut = g[g.index > d][["open", "high", "low", "close"]]
        if fut.empty:
            continue
        idx = cfg["trading"].get("exit_target_index", 0)
        tr = {"direction": direction, "status": "pending", "trigger": s["trigger"], "stop": s["stop"],
              "qty": s["qty"], "opened": d, "_target": s["targets"][min(idx, len(s["targets"]) - 1)]}
        out = step(tr, fut, cfg)
        if out["status"] in ("expired", "pending"):   # never triggered (or data ended first)
            continue
        if out["status"] != "closed":            # still open at the end: mark at last close
            last = fut.iloc[-1]
            out.update(status="closed", closed=fut.index[-1], exit_price=float(last["close"]), exit_reason="end")
            sgn = 1 if direction == "bullish" else -1
            gross = sgn * (out["exit_price"] - out["entry"]) * out["qty"]
            c = cost_model(out["entry"] * out["qty"], out["exit_price"] * out["qty"], cfg)["total"]
            out.update(pnl=gross - c, costs=c)
        exit_d = pd.Timestamp(out["closed"])
        busy.append(exit_d)
        held[ev["symbol"]] = exit_d
        closed.append({"symbol": ev["symbol"], "signal_date": d, "filled": out.get("filled"), "closed": exit_d,
                       "entry": out["entry"], "exit": out["exit_price"], "qty": out["qty"], "pnl": out["pnl"],
                       "costs": out.get("costs"), "r": out.get("r_multiple"), "reason": out["exit_reason"]})
    dates = sorted(p["date"].unique())
    tl = pd.DataFrame(closed)
    pnl_by_day = tl.groupby("closed")["pnl"].sum() if len(tl) else pd.Series(dtype=float)
    eq = capital + pnl_by_day.reindex(pd.DatetimeIndex(dates)).fillna(0).cumsum()
    res = {"equity": eq, "trades": int(len(tl)), "trade_log": tl}
    if len(tl):
        wins, losses = tl.loc[tl["pnl"] > 0, "pnl"], tl.loc[tl["pnl"] <= 0, "pnl"]
        res.update(hit_rate=float((tl["pnl"] > 0).mean()), expectancy=float(tl["pnl"].mean()),
                   profit_factor=float(wins.sum() / -losses.sum()) if losses.sum() < 0 else None,
                   avg_r=float(tl["r"].mean()) if tl["r"].notna().any() else None,
                   exits=tl["reason"].value_counts().to_dict())
    return res


# ------------------------------------------------------------- walk-forward --- #
def walk_forward_ranked(panel: pd.DataFrame, variants: dict[str, list[str]], cfg: Optional[dict] = None,
                        train: Optional[int] = None, test: Optional[int] = None) -> dict:
    """Pick the variant with the best in-sample Sharpe on each ``train`` window, run it on the next
    ``test`` sessions, stitch the out-of-sample equity."""
    cfg = cfg or load()
    b = cfg["backtest"]
    train, test = train or b["train_sessions"], test or b["test_sessions"]
    dates = sorted(panel["date"].unique())
    windows, pieces = [], []
    level = float(cfg["trading"]["capital"])
    i = train
    while i < len(dates) - 1:
        tr_s, tr_e = dates[i - train], dates[i - 1]
        te_s, te_e = dates[i], dates[min(i + test, len(dates)) - 1]
        best, best_sharpe = None, None
        for name, facs in variants.items():
            eq = ranked(panel, facs, b["top_k"], b["rebalance_sessions"], cfg, tr_s, tr_e)["equity"]
            m = metrics(eq, b["risk_free_pct"])
            sh = m.get("sharpe")
            if sh is not None and (best_sharpe is None or sh > best_sharpe):
                best, best_sharpe = name, sh
        if best is None:
            break
        oos = ranked(panel, variants[best], b["top_k"], b["rebalance_sessions"], cfg, te_s, te_e, capital=level)["equity"]
        if len(oos):
            level = float(oos.iloc[-1])
            pieces.append(oos)
        windows.append({"train": f"{pd.Timestamp(tr_s).date()}..{pd.Timestamp(tr_e).date()}",
                        "test": f"{pd.Timestamp(te_s).date()}..{pd.Timestamp(te_e).date()}",
                        "chosen": best, "in_sample_sharpe": best_sharpe,
                        "oos_return_pct": float((oos.iloc[-1] / oos.iloc[0] - 1) * 100) if len(oos) > 1 else None})
        i += test
    eq = pd.concat(pieces) if pieces else pd.Series(dtype=float)
    eq = eq[~eq.index.duplicated(keep="first")]
    return {"equity": eq, "windows": windows}


# --------------------------------------------------------------- sensitivity --- #
def sensitivity(panel: pd.DataFrame, factors: list[str], cfg: Optional[dict] = None,
                top_ks=(10, 20, 30), rebalances=(5, 10, 21), cost_mults=(1.0, 2.0)) -> dict:
    cfg = cfg or load()
    rows = []
    for k in top_ks:
        for rb in rebalances:
            for cm in cost_mults:
                r = ranked(panel, factors, k, rb, cfg, cost_mult=cm)
                m = metrics(r["equity"], cfg["backtest"]["risk_free_pct"])
                rows.append({"top_k": k, "rebalance": rb, "cost_mult": cm, "cagr_pct": m.get("cagr_pct"),
                             "sharpe": m.get("sharpe"), "max_drawdown_pct": m.get("max_drawdown_pct"),
                             "turnover": r["turnover"]})
    df = pd.DataFrame(rows)
    cagr = df["cagr_pct"].dropna()
    fragile = bool(len(cagr) and (cagr > 0).any() and (cagr < 0).any())
    return {"grid": rows, "fragile": fragile,
            "verdict": ("results change sign across reasonable settings — treat as fragile" if fragile else
                        "sign of CAGR is stable across the grid" if len(cagr) else "no results")}


# ----------------------------------------------------------------- reports --- #
def benchmarks(eng, cfg: dict) -> dict:
    out = {}
    for name in cfg["backtest"]["benchmarks"]:
        b = load_index(eng, name)
        if len(b):
            b.index = pd.to_datetime(b.index)
            out[name] = b
    return out


def _series_json(eq: pd.Series, bench: dict) -> dict:
    if eq.empty:
        return {"dates": [], "equity": [], "benchmarks": {}}
    base = eq.iloc[0]
    out = {"dates": [str(d.date()) for d in eq.index], "equity": [float(v / base * 100) for v in eq],
           "drawdown": [float(v) for v in (eq / eq.cummax() - 1) * 100], "benchmarks": {}}
    for n, b in bench.items():
        bb = b.reindex(eq.index).ffill()
        if bb.notna().any():
            first = bb.dropna().iloc[0]
            out["benchmarks"][n] = [None if pd.isna(v) else float(v / first * 100) for v in bb]
    return out


def save(eng, kind: str, name: str, params: dict, mets: dict, series: dict) -> int:
    with eng.begin() as c:
        r = c.execute(insert(db.backtest_runs).values(
            run_at=datetime.now(timezone.utc).replace(tzinfo=None), kind=kind, name=name,
            params=json.dumps(params, default=str), metrics=json.dumps(mets, default=str),
            series=json.dumps(series, default=str)))
        return int(r.inserted_primary_key[0])


def run(eng, panel: pd.DataFrame, kind: str = "ranked", factors: Optional[list] = None, signal: Optional[str] = None,
        top_k: Optional[int] = None, rebalance: Optional[int] = None, cfg: Optional[dict] = None) -> dict:
    """Run one backtest of ``kind`` (ranked | signal | walk_forward | sensitivity), store and return it."""
    cfg = cfg or load()
    b = cfg["backtest"]
    bench = benchmarks(eng, cfg)
    rf = b["risk_free_pct"]
    top_k, rebalance = top_k or b["top_k"], rebalance or b["rebalance_sessions"]
    params = {"kind": kind, "factors": factors, "signal": signal, "top_k": top_k, "rebalance": rebalance,
              "costs": cfg["costs"], "capacity_pct_of_value": b["capacity_pct_of_value"],
              "universe": cfg["universe"]["name"], "liquidity": cfg["liquidity"],
              "bias_controls": ["decisions at close t, trades at open t+1", "survivorship-free daily universe",
                                "point-in-time fundamentals/ownership", "costs + tiered slippage + capacity cap"]}
    extra: dict = {}
    if kind == "ranked":
        if not factors:
            raise ValueError("ranked backtests need --factors")
        r = ranked(panel, factors, top_k, rebalance, cfg)
        eq, name = r["equity"], f"ranked: {'+'.join(factors)} top{top_k}/{rebalance}d"
        extra = {"trades": r["trades"], "turnover_x_per_year": r["turnover"], "exposure_pct": r["exposure_pct"]}
    elif kind == "signal":
        from .research.signals import SIGNALS
        if signal not in SIGNALS:
            raise ValueError(f"unknown signal {signal}")
        fn, direction, _ = SIGNALS[signal]
        r = signal_portfolio(panel, fn(panel), direction, cfg)
        eq, name = r["equity"], f"signal: {signal}"
        extra = {k: v for k, v in r.items() if k not in ("equity", "trade_log")}
        tl = r["trade_log"]
        extra["recent_trades"] = json.loads(tl.tail(50).to_json(orient="records", date_format="iso")) if len(tl) else []
    elif kind == "walk_forward":
        r = walk_forward_ranked(panel, b["variants"], cfg)
        eq, name = r["equity"], "walk-forward: " + ", ".join(b["variants"])
        extra = {"windows": r["windows"], "variants": b["variants"]}
    elif kind == "sensitivity":
        if not factors:
            raise ValueError("sensitivity needs --factors")
        r = sensitivity(panel, factors, cfg)
        eq, name = ranked(panel, factors, top_k, rebalance, cfg)["equity"], f"sensitivity: {'+'.join(factors)}"
        extra = r
    else:
        raise ValueError(f"unknown kind {kind}")
    mets = {**metrics(eq, rf, bench), **extra}
    series = _series_json(eq, bench)
    rid = save(eng, kind, name, params, mets, series)
    return {"id": rid, "name": name, "kind": kind, "params": params, "metrics": mets, "series": series}


def list_runs(eng, limit: int = 50) -> list[dict]:
    rows = db.read_sql(eng, select(db.backtest_runs.c.id, db.backtest_runs.c.run_at, db.backtest_runs.c.kind,
                                   db.backtest_runs.c.name, db.backtest_runs.c.metrics)
                       .order_by(db.backtest_runs.c.id.desc()).limit(limit))
    out = []
    for _, r in rows.iterrows():
        m = json.loads(r["metrics"])
        out.append({"id": int(r["id"]), "run_at": str(r["run_at"]), "kind": r["kind"], "name": r["name"],
                    "cagr_pct": m.get("cagr_pct"), "sharpe": m.get("sharpe"), "max_drawdown_pct": m.get("max_drawdown_pct"),
                    "excess_vs_nifty500": (m.get("vs_Nifty 500") or {}).get("excess_cagr_pct")})
    return out


def get_run(eng, rid: int) -> Optional[dict]:
    r = db.read_sql(eng, select(db.backtest_runs).where(db.backtest_runs.c.id == rid))
    if r.empty:
        return None
    x = r.iloc[0]
    return {"id": int(x["id"]), "run_at": str(x["run_at"]), "kind": x["kind"], "name": x["name"],
            "params": json.loads(x["params"]), "metrics": json.loads(x["metrics"]), "series": json.loads(x["series"])}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--factors", help="comma list of scoring factors (ranked / sensitivity)")
    ap.add_argument("--signal", help="research signal name (signal mode)")
    ap.add_argument("--top", type=int)
    ap.add_argument("--rebalance", type=int)
    ap.add_argument("--walk-forward", action="store_true")
    ap.add_argument("--sensitivity", action="store_true")
    a = ap.parse_args(argv)
    from .panel import build_panel
    from .scoring import enrich
    eng = db.engine()
    panel = enrich(eng, build_panel(eng))
    facs = [f.strip() for f in a.factors.split(",")] if a.factors else None
    kind = "walk_forward" if a.walk_forward else "sensitivity" if a.sensitivity else "signal" if a.signal else "ranked"
    res = run(eng, panel, kind, facs, a.signal, a.top, a.rebalance)
    m = res["metrics"]
    print(f"#{res['id']} {res['name']}")
    for k in ("start", "end", "cagr_pct", "volatility_pct", "sharpe", "sortino", "max_drawdown_pct", "calmar"):
        print(f"  {k:<18}{m.get(k)}")
    for b in load()["backtest"]["benchmarks"]:
        v = m.get(f"vs_{b}")
        if v:
            print(f"  vs {b:<14} excess CAGR {v['excess_cagr_pct']:.2f}% · beta {v['beta']} · IR {v['information_ratio']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
