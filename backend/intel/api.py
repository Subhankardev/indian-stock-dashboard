"""HTTP API for the Institutional Intelligence system: /api/intel/*.

Every response that shows market data carries ``as_of`` and the source(s) it came from,
and ``demo: true`` when the database holds synthetic demo data (``python -m intel.demo``).
"""
from __future__ import annotations

import json
import math
from datetime import date
from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import func, select

from . import db, evidence
from .config import load
from .panel import build_panel, latest_snapshot
from .research.signals import EVIDENCE_CLASS, FILTER_BASE, SIGNALS
from .sources import REGISTRY

router = APIRouter(prefix="/api/intel", tags=["intel"])

_cache: dict = {}

SCREEN_COLS = ["symbol", "industry", "close", "ret_1d", "rs_rank", "rs_1m", "rs_3m", "rs_6m", "rs_mom_20",
               "rsi14", "adx", "rvol20", "deliv_pct", "deliv_pct_avg20", "avg_value_cr", "atr_pct",
               "dist_52w_high_pct", "dma50", "dma200", "liquid", "d_score", "x_score",
               "accumulation_score", "accumulation_confidence", "distribution_score", "distribution_confidence",
               "pe", "pb", "mcap_cr", "roe_pct", "rev_growth_pct", "quality_score", "value_score"]


def _clean(v):
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, (pd.Timestamp, date)):
        return str(pd.Timestamp(v).date())
    if hasattr(v, "item"):
        return _clean(v.item())
    return v


def records(df: pd.DataFrame) -> list[dict]:
    return [{k: _clean(v) for k, v in row.items()} for row in df.to_dict("records")]


def _state_key(eng) -> tuple:
    with eng.connect() as c:
        n = c.execute(select(func.max(db.source_files.c.id), func.count(db.source_files.c.id))).one()
    return tuple(n) + (json.dumps(load(), sort_keys=True),)


def _is_demo(eng) -> bool:
    with eng.connect() as c:
        return bool(c.execute(select(func.count()).select_from(db.source_files)
                              .where(db.source_files.c.dataset == "demo")).scalar())


def panel_cached(eng) -> pd.DataFrame:
    """The full research panel, rebuilt only when new source files arrive or config changes."""
    key = (str(eng.url),) + _state_key(eng)
    if _cache.get("key") != key:
        _cache.clear()
        from .scoring import enrich
        _cache.update(key=key, panel=enrich(eng, build_panel(eng, load())))
    return _cache["panel"]


def _sources_for(eng, dataset_names: list[str]) -> list[dict]:
    fr = db.dataset_freshness(eng)
    fr = fr[fr["dataset"].isin(dataset_names)]
    return records(fr)


@router.get("/status")
def status():
    """Data freshness per dataset, the source registry and the active configuration."""
    eng = db.engine()
    fr = db.dataset_freshness(eng)
    with eng.connect() as c:
        errors = c.execute(select(db.source_files.c.dataset, db.source_files.c.as_of, db.source_files.c.error)
                           .where(db.source_files.c.status == "error")
                           .order_by(db.source_files.c.fetched_at.desc()).limit(10)).all()
    from .upstox_ingest import survivorship_warning
    return {"demo": _is_demo(eng), "datasets": records(fr), "recent_errors": [dict(r._mapping) for r in errors],
            "warning": survivorship_warning(eng), "registry": REGISTRY, "config": load(), "database": eng.url.render_as_string(hide_password=True)}


@router.get("/screener")
async def screener(liquid_only: bool = True, limit: int = Query(200, ge=1, le=3000),
                   sort: str = "rs_rank", industry: Optional[str] = None):
    """Latest-session snapshot: relative strength, volume, delivery and trend for each stock."""
    eng = db.engine()
    panel = await run_in_threadpool(panel_cached, eng)
    if panel.empty:
        return {"as_of": None, "rows": [], "demo": _is_demo(eng), "sources": [],
                "message": "No price data yet. Run: python -m intel.ingest --lists --from 2023-01-01"}
    snap = latest_snapshot(panel)
    sc = evidence.scores(snap, panel, evidence.passed_signals(eng), load()["scoring"].get("require_pass", True))
    snap = snap.merge(sc[["symbol", "accumulation_score", "accumulation_confidence", "distribution_score",
                          "distribution_confidence"]], on="symbol", how="left")
    total = len(snap)
    if liquid_only:
        snap = snap[snap["liquid"].fillna(False).astype(bool)]
    if industry:
        snap = snap[snap["industry"] == industry]
    if sort not in snap.columns:
        raise HTTPException(422, f"unknown sort column {sort}")
    snap = snap.sort_values(sort, ascending=False, na_position="last")
    cols = [c for c in SCREEN_COLS if c in snap.columns]
    return {"as_of": _clean(snap["date"].iloc[0]) if len(snap) else _clean(panel["date"].max()),
            "demo": _is_demo(eng), "universe": load()["universe"]["name"], "total": total,
            "shown": min(limit, len(snap)), "rows": records(snap[cols].head(limit)),
            "industries": sorted(x for x in panel["industry"].dropna().unique()),
            "sources": _sources_for(eng, ["cm", "indices", "upstox_cm", "upstox_index_Nifty 50", "demo"])}


@router.get("/stock/{symbol}")
async def stock(symbol: str, days: int = Query(250, ge=20, le=2000)):
    """Daily series for one stock with indicators, RS and delivery, plus provenance."""
    eng = db.engine()
    panel = await run_in_threadpool(panel_cached, eng)
    sym = symbol.upper()
    g = panel[panel["symbol"] == sym] if not panel.empty else panel
    if g.empty:
        raise HTTPException(404, f"No data for {sym}")
    g = g.tail(days)
    last = g.iloc[-1]
    with eng.connect() as c:
        src = c.execute(select(db.source_files).where(db.source_files.c.id == db.prices_daily.c.source_id)
                        .where(db.prices_daily.c.symbol == sym).order_by(db.prices_daily.c.date.desc())
                        .limit(1)).first()
    cols = ["date", "open", "high", "low", "close", "volume", "deliv_pct", "deliv_qty", "ema20", "dma50", "dma200",
            "rs_ratio", "rs_rank", "rsi14", "rvol20", "atr14", "obv", "cmf20", "bb_upper", "bb_lower"]
    snapshot_cols = [c for c in SCREEN_COLS if c in g.columns] + ["supertrend_dir", "macd_hist", "mfi14",
                                                                  "updown_vol20", "volatility20", "history"]
    return {"symbol": sym, "as_of": _clean(last["date"]), "demo": _is_demo(eng),
            "latest": {k: _clean(last[k]) for k in snapshot_cols if k in g.columns},
            "series": records(g[[c for c in cols if c in g.columns]]),
            "source": _clean_row(src._mapping) if src else None}


def _clean_row(m) -> dict:
    return {k: _clean(v) if not hasattr(v, "isoformat") else v.isoformat() for k, v in dict(m).items()}


@router.get("/research")
def research():
    """Latest event-study result for each signal (see python -m intel.research)."""
    eng = db.engine()
    latest = (select(db.signal_tests.c.signal_name, func.max(db.signal_tests.c.id).label("id"))
              .group_by(db.signal_tests.c.signal_name).subquery())
    with eng.connect() as c:
        rows = c.execute(select(db.signal_tests).join(latest, db.signal_tests.c.id == latest.c.id)).all()
    out = []
    for r in rows:
        m = dict(r._mapping)
        m["stats"] = json.loads(m["stats"])
        m["run_at"] = m["run_at"].isoformat() if m["run_at"] else None
        out.append(m)
    from .upstox_ingest import survivorship_warning
    known = {n: {"direction": d, "description": desc, "evidence_class": EVIDENCE_CLASS.get(n)}
             for n, (_, d, desc) in SIGNALS.items()}
    return {"demo": _is_demo(eng), "signals": known, "results": out, "filters": FILTER_BASE,
            "warning": survivorship_warning(eng),
            "rules": load()["research"]}


@router.post("/research/run")
async def research_run(signal: Optional[list[str]] = Query(None)):
    """Re-run the event studies on stored history (may take a minute on full NSE data)."""
    from .research.__main__ import test_all
    unknown = [s for s in signal or [] if s not in SIGNALS]
    if unknown:
        raise HTTPException(422, f"unknown signal(s): {unknown}")
    eng = db.engine()
    try:
        await run_in_threadpool(test_all, eng, load(), signal)
    except SystemExit as exc:
        raise HTTPException(409, str(exc))
    return research()


# ------------------------------------------------------------- Phase 2 --- #
@router.get("/institutional")
async def institutional(days: int = Query(30, ge=1, le=365), limit: int = Query(30, ge=1, le=200)):
    """Market-wide FII/DII flows (class C), recent bulk/block deals with entity classes
    (A when the client is a classified institution, else E), and stocks with the most
    accumulation / distribution evidence on the latest session."""
    eng = db.engine()
    panel = await run_in_threadpool(panel_cached, eng)
    flows = evidence.market_flows(eng)
    out = {"demo": _is_demo(eng), "flows": records(flows),
           "flows_note": "Class C — market-wide cash-market flows (₹ crore, provisional). They say nothing about "
                         "any individual stock.", "deals": [], "accumulation": [], "distribution": [],
           "sources": _sources_for(eng, ["fii_dii", "fii_dii_import", "deals_bulk", "deals_block",
                                         "deals_bulk_import", "deals_block_import", "shareholding_import",
                                         "mf_import", "demo"])}
    if not panel.empty:
        last = panel["date"].max()
        d = evidence.deals_classified(eng, start=(last - pd.Timedelta(days=days)).date())
        out["deals"] = records(d[["date", "kind", "symbol", "client", "side", "qty", "price", "value_cr",
                                  "entity_class", "method", "evidence_class"]].head(500)) if len(d) else []
        snap = latest_snapshot(panel)
        passed = evidence.passed_signals(eng)
        req = load()["scoring"].get("require_pass", True)
        sc = evidence.scores(snap, panel, passed, req).merge(
            snap[["symbol", "industry", "close", "ret_1d", "rs_rank", "d_score", "x_score", "liquid"]], on="symbol")
        sc = sc[sc["liquid"].fillna(False).astype(bool)]
        key_a = "accumulation_score" if sc["accumulation_score"].notna().any() else "d_score"
        key_d = "distribution_score" if sc["distribution_score"].notna().any() else "x_score"
        out["accumulation"] = records(sc.sort_values(key_a, ascending=False).head(limit))
        out["distribution"] = records(sc.sort_values(key_d, ascending=False).head(limit))
        out["ranked_by"] = {"accumulation": key_a, "distribution": key_d}
        out["passed_components"] = sorted(passed & {s for v in evidence.COMPONENT_SIGNALS.values() for s in v.values()})
        out["as_of"] = _clean(last)
    return out


@router.get("/stock/{symbol}/evidence")
async def stock_evidence(symbol: str):
    """All accumulation / distribution evidence for one stock, each item with its class."""
    eng = db.engine()
    panel = await run_in_threadpool(panel_cached, eng)
    sym = symbol.upper()
    g = panel[panel["symbol"] == sym] if not panel.empty else panel
    if g.empty:
        raise HTTPException(404, f"No data for {sym}")
    snap = latest_snapshot(panel)
    passed = evidence.passed_signals(eng)
    sc = evidence.scores(snap, panel, passed, load()["scoring"].get("require_pass", True))
    row = g.iloc[-1]
    deals = evidence.deals_classified(eng, symbol=sym)
    own = evidence.ownership_changes(eng, sym)
    mf = evidence.mf_changes(eng, sym)
    hist_cols = ["date", "d_score", "x_score"] + list(evidence.ACC_SUBS) + list(evidence.DIST_SUBS)
    return {
        "symbol": sym, "as_of": _clean(row["date"]), "demo": _is_demo(eng),
        "items": evidence.stock_evidence(eng, row),
        "subsignals": {"accumulation": {k: {"on": bool(row.get(k)), "label": v} for k, v in evidence.ACC_SUBS.items()},
                       "distribution": {k: {"on": bool(row.get(k)), "label": v} for k, v in evidence.DIST_SUBS.items()}},
        "scores": records(sc[sc["symbol"] == sym])[0] if (sc["symbol"] == sym).any() else None,
        "passed_components": sorted(passed),
        "component_signals": evidence.COMPONENT_SIGNALS, "weights": evidence.COMPONENT_WEIGHTS,
        "history": records(g[[c for c in hist_cols if c in g]].tail(120)),
        "deals": records(deals[["date", "kind", "client", "side", "qty", "price", "value_cr", "entity_class",
                                "method", "evidence_class"]].head(100)) if len(deals) else [],
        "ownership": records(own), "mf": records(mf.tail(24)),
    }


@router.get("/entities")
def entities_list(q: Optional[str] = None, limit: int = Query(200, ge=1, le=2000)):
    """Every deal client with its class and how it was classified."""
    from .entities import classify
    eng = db.engine()
    names = db.read_sql(eng, select(db.deals.c.client, func.count().label("deals"))
                        .group_by(db.deals.c.client).order_by(func.count().desc()))
    if q:
        names = names[names["client"].str.contains(q, case=False, regex=False)]
    names = names.head(limit)
    cl = classify(eng, names["client"]).rename(columns={"name": "client"})
    return {"entities": records(names.merge(cl, on="client", how="left"))}


class _EntityIn(BaseModel):
    name: str
    entity_class: str
    notes: str = ""


@router.put("/entities")
def entities_set(body: _EntityIn):
    """Manual classification; always overrides the name rules."""
    from .entities import INSTITUTIONAL, set_manual
    allowed = INSTITUTIONAL | {"broker_prop", "corporate", "individual", "promoter", "unknown"}
    if body.entity_class not in allowed:
        raise HTTPException(422, f"entity_class must be one of {sorted(allowed)}")
    set_manual(db.engine(), body.name, body.entity_class, body.notes)
    _cache.clear()
    return {"ok": True}


# ------------------------------------------------------------- Phase 3 --- #
@router.get("/derivatives")
async def derivatives_view(limit: int = Query(20, ge=1, le=200)):
    """Market-wide participant positioning (class C), index option/futures data, and
    stock futures build-ups on the latest session (class F)."""
    from . import derivatives as dv
    eng = db.engine()
    panel = await run_in_threadpool(panel_cached, eng)
    part = dv.participant_summary(eng)
    idx = dv.index_summary(eng)
    out = {"demo": _is_demo(eng), "participants": records(part), "indices": records(idx),
           "note": "Build-ups describe futures positioning from price and open interest. They do not show who "
                   "traded; an OI increase is not institutional buying. Participant OI is market-wide (class C).",
           "buildups": {}, "counts": {},
           "sources": _sources_for(eng, ["fo", "participant_oi", "demo"])}
    if not panel.empty and "f_buildup" in panel:
        snap = latest_snapshot(panel)
        fo = snap[snap["f_has_fo"].fillna(False).astype(bool)]
        cols = ["symbol", "industry", "close", "f_ret_pct", "f_oi_chg_pct", "f_basis_pct", "f_rollover_pct",
                "f_pcr_oi", "f_atm_iv", "rs_rank"]
        for b in dv.BUILDUPS:
            sub = fo[fo["f_buildup"] == b].sort_values("f_oi_chg_pct", ascending=b in ("short_covering", "long_unwinding"))
            out["buildups"][b] = records(sub[cols].head(limit))
        out["counts"] = {str(k): int(v) for k, v in fo["f_buildup"].fillna("no data").value_counts().items()}
        out["as_of"] = _clean(snap["date"].iloc[0])
    return out


@router.get("/stock/{symbol}/derivatives")
def stock_derivatives(symbol: str, days: int = Query(120, ge=5, le=1000)):
    from . import derivatives as dv
    eng = db.engine()
    d = dv.load_daily(eng, [symbol.upper()])
    if d.empty:
        return {"symbol": symbol.upper(), "has_fo": False, "series": []}
    return {"symbol": symbol.upper(), "has_fo": True, "series": records(d.tail(days).drop(columns=["source_id"]))}


# ------------------------------------------------------------- Phase 4 --- #
@router.get("/stock/{symbol}/vwap")
async def stock_vwap(symbol: str, days: int = Query(250, ge=20, le=2000)):
    """Daily rolling and anchored VWAPs (exact: NSE average traded price × volume)."""
    from .vwap import ANCHORS
    eng = db.engine()
    panel = await run_in_threadpool(panel_cached, eng)
    g = panel[panel["symbol"] == symbol.upper()] if not panel.empty else panel
    if g.empty:
        raise HTTPException(404, f"No data for {symbol.upper()}")
    cols = ["date", "close", "vwap20", "vwap50"] + [f"avwap_{k}" for k in ANCHORS]
    last = g.iloc[-1]
    latest = {k: _clean(last[k]) for k in cols[1:] + [f"dist_avwap_{a}_pct" for a in ANCHORS] +
              ["dist_vwap20_pct", "absorption_up", "absorption_down", "avwap_reclaim", "avwap_loss"] if k in g}
    return {"symbol": symbol.upper(), "as_of": _clean(last["date"]), "anchors": ANCHORS, "latest": latest,
            "series": records(g[cols].tail(days))}


@router.get("/stock/{symbol}/intraday")
async def stock_intraday(symbol: str, day: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
    """Intraday VWAP bands and volume profile from 1-minute candles (Upstox). Uses labelled
    synthetic bars when the database is in demo mode."""
    from . import intraday
    eng = db.engine()
    d = date.fromisoformat(day) if day else None
    if _is_demo(eng):
        panel = await run_in_threadpool(panel_cached, eng)
        g = panel[panel["symbol"] == symbol.upper()] if not panel.empty else panel
        base = float(g["close"].iloc[-1]) if len(g) else 1000.0
        m = intraday.synthetic_session(d or date.today(), base=base, seed=sum(map(ord, symbol.upper())))
        source = "synthetic"
    else:
        try:
            m = await run_in_threadpool(intraday.fetch_minutes, symbol.upper(), d)
        except Exception as exc:
            raise HTTPException(502, f"1-minute candles unavailable (needs a valid UPSTOX_ACCESS_TOKEN): {exc}")
        source = "upstox"
        if m.empty:
            raise HTTPException(404, "No 1-minute candles for that day (holiday or before the open)")
    out = intraday.summary(m)
    out.update(symbol=symbol.upper(), source=source, demo=source == "synthetic",
               note="Approximation from 1-minute bars: each bar's volume is placed at its typical price. "
                    "Not order flow — see /api/intel/orderflow.")
    return out


@router.get("/orderflow")
def orderflow_status():
    """Tick/order-book features and why each is off (needs a licensed feed)."""
    from .orderflow import status
    return status()


# ------------------------------------------------------------- Phase 5 --- #
@router.get("/stock/{symbol}/fundamentals")
async def stock_fundamentals(symbol: str):
    """Point-in-time fundamentals, valuation and sector-relative scores, with industry peers."""
    from . import fundamentals as fa
    eng = db.engine()
    panel = await run_in_threadpool(panel_cached, eng)
    sym = symbol.upper()
    g = panel[panel["symbol"] == sym] if not panel.empty else panel
    if g.empty:
        raise HTTPException(404, f"No data for {sym}")
    fa_cols = ["fa_effective", "fa_basis", "pe", "pb", "mcap_cr", "earnings_yield_pct", "roe_pct", "roce_pct",
               "de_ratio", "ebitda_margin_pct", "pat_margin_pct", "cfo_to_pat", "rev_growth_pct", "pat_growth_pct",
               "gnpa_pct", "quality_score", "value_score", "fa_sector_relative", "fa_financial"]
    last = g.iloc[-1]
    snap = latest_snapshot(panel)
    peers = snap[(snap["industry"] == last["industry"])]
    pit = fa.point_in_time(fa.load_long(eng, [sym]))
    hist_cols = ["effective", "period_end", "basis", "ttm_revenue", "ttm_pat", "ttm_eps", "roe_pct", "de_ratio",
                 "rev_growth_pct", "pat_growth_pct", "pat_margin_pct", "gnpa_pct"]
    return {"symbol": sym, "as_of": _clean(last["date"]), "industry": last.get("industry"), "demo": _is_demo(eng),
            "latest": {k: _clean(last[k]) for k in fa_cols if k in g},
            "history": records(pit[[c for c in hist_cols if c in pit]].tail(12)) if len(pit) else [],
            "peers": records(peers[["symbol", "close", "pe", "pb", "roe_pct", "rev_growth_pct", "quality_score",
                                    "value_score"]].sort_values("quality_score", ascending=False, na_position="last")
                             .head(15)) if "pe" in peers else [],
            "sources": _sources_for(eng, ["fundamentals_import", "demo"]),
            "note": "Point-in-time: each number is used only from its publication date. Financials skip leverage, "
                    "EBITDA margin and cash conversion; asset quality (GNPA) is used when present."}


# ------------------------------------------------------------- Phase 6 --- #
@router.get("/rankings")
async def rankings_view(preview: bool = False, limit: int = Query(30, ge=1, le=200)):
    """Bullish and bearish rankings from active (tested) factors, with confidence, agreement,
    conflicts and special situations. ``preview=true`` uses every factor — UNVALIDATED."""
    from . import scoring
    eng = db.engine()
    panel = await run_in_threadpool(panel_cached, eng)
    if panel.empty:
        return {"demo": _is_demo(eng), "bullish": [], "bearish": [], "message": "No price data."}
    res = scoring.rankings(eng, panel, preview=preview, limit=limit)
    res["demo"] = _is_demo(eng)
    return res


@router.get("/market")
async def market_view():
    """Market regime, breadth, sector strength and flows (all market-wide, class C)."""
    from . import market
    eng = db.engine()
    panel = await run_in_threadpool(panel_cached, eng)
    if panel.empty:
        return {"demo": _is_demo(eng), "regime": None, "breadth": [], "sectors": []}
    br = market.breadth(panel, days=250)
    last = panel["date"].max()
    today = panel[panel["date"] == last]
    counts = {k: int(today[k].fillna(False).astype(bool).sum()) for k in
              ("a_inst_buy", "a_inst_sell", "b_inst_up", "b_inst_down") if k in today}
    if "d_score" in today:
        counts["possible_accumulation"] = int((today["d_score"] >= 3).sum())
        counts["possible_distribution"] = int((today["x_score"] >= 3).sum())
    flows = evidence.market_flows(eng, days=1)
    return {"demo": _is_demo(eng), "regime": market.regime(eng, panel), "breadth": records(br),
            "sectors": records(market.sector_table(panel)), "signal_counts": counts,
            "flows": records(flows), "as_of": _clean(last)}


@router.get("/stock/{symbol}/score")
async def stock_score(symbol: str, preview: bool = False):
    """Score breakdown for one stock: each factor's percentile, weight, coverage and status."""
    from . import scoring
    eng = db.engine()
    panel = await run_in_threadpool(panel_cached, eng)
    sym = symbol.upper()
    g = panel[panel["symbol"] == sym] if not panel.empty else panel
    if g.empty:
        raise HTTPException(404, f"No data for {sym}")
    cfg = load()
    active = set(scoring.FACTORS) if preview else scoring.active_factors(eng)
    row = g.iloc[[-1]]
    weights = cfg["scoring"]["weights"]
    sides = {side: {k: _clean(v) for k, v in scoring.score(row, active, weights, side).iloc[0].items()}
             for side in ("bullish", "bearish")}
    r = row.iloc[0]
    factors = [{"factor": f, "description": d, "weight": weights.get(k, 0), "active": f in active,
                "bullish_pct": _clean(r.get(f"p_{f}")), "bearish_pct": _clean(r.get(f"q_{f}")),
                "coverage": _clean(r.get(f"c_{f}"))} for f, (k, d) in scoring.FACTORS.items()]
    history = scoring.score(g.tail(250), active, weights, "bullish").join(g.tail(250)[["date"]])
    return {"symbol": sym, "as_of": _clean(r["date"]), "preview": preview, "active": sorted(active),
            "liquid": bool(r.get("liquid")), "min_confidence": cfg["scoring"]["min_confidence"],
            "scores": sides, "factors": factors, "conflicts": scoring.conflicts(r),
            "history": records(history[["date", "bullish_score", "bullish_confidence"]])}


# ------------------------------------------------------------- Phase 7 --- #
@router.get("/stock/{symbol}/setup")
async def stock_setup(symbol: str, direction: str = Query("bullish", pattern="^(bullish|bearish)$")):
    """Trade setup (trigger, ATR/swing stop, targets, size, costs) for paper trading."""
    from .setups import check_limits, setup_for
    from .paper import trades
    eng = db.engine()
    panel = await run_in_threadpool(panel_cached, eng)
    g = panel[panel["symbol"] == symbol.upper()] if not panel.empty else panel
    if g.empty:
        raise HTTPException(404, f"No data for {symbol.upper()}")
    s = setup_for(g.iloc[-1], g, direction)
    s["limit_problems"] = check_limits(s, trades(eng, ["pending", "open"])) if s.get("valid") else []
    s["config"] = {k: load()["trading"][k] for k in ("capital", "risk_per_trade_pct", "atr_mult", "entry",
                                                      "targets_r", "exit_target_index", "time_exit_sessions")}
    return s


class _PaperIn(BaseModel):
    symbol: str
    direction: str = "bullish"
    notes: str = ""


@router.get("/paper")
def paper_list():
    """All paper trades and performance. Simulated — no orders are ever placed."""
    from .paper import report, trades
    eng = db.engine()
    t = trades(eng).sort_values("id", ascending=False) if len(trades(eng)) else trades(eng)
    return {"trades": records(t.drop(columns=["notes"], errors="ignore")), "report": report(eng),
            "demo": _is_demo(eng)}


@router.post("/paper")
async def paper_open(body: _PaperIn):
    from .paper import open_trade
    from .setups import setup_for
    if body.direction not in ("bullish", "bearish"):
        raise HTTPException(422, "direction must be bullish or bearish")
    eng = db.engine()
    panel = await run_in_threadpool(panel_cached, eng)
    g = panel[panel["symbol"] == body.symbol.upper()] if not panel.empty else panel
    if g.empty:
        raise HTTPException(404, f"No data for {body.symbol.upper()}")
    res = open_trade(eng, setup_for(g.iloc[-1], g, body.direction), notes=body.notes)
    if not res["ok"]:
        raise HTTPException(409, "; ".join(res["reasons"]))
    return res


@router.post("/paper/update")
def paper_update():
    from .paper import update_all
    return update_all(db.engine())


@router.post("/paper/auto")
async def paper_auto():
    from .paper import auto
    eng = db.engine()
    panel = await run_in_threadpool(panel_cached, eng)
    return await run_in_threadpool(auto, eng, panel)


@router.post("/paper/{trade_id}/close")
async def paper_close(trade_id: int):
    from .paper import close_manual, trades
    eng = db.engine()
    t = trades(eng)
    row = t[t["id"] == trade_id]
    if row.empty:
        raise HTTPException(404, "no such paper trade")
    panel = await run_in_threadpool(panel_cached, eng)
    g = panel[panel["symbol"] == row.iloc[0]["symbol"]]
    if g.empty:
        raise HTTPException(409, "no price to close at")
    res = close_manual(eng, trade_id, float(g.iloc[-1]["close"]), pd.Timestamp(g.iloc[-1]["date"]).date())
    if not res["ok"]:
        raise HTTPException(409, res["reason"])
    return {k: _clean(v) for k, v in res.items()}


@router.get("/alerts")
def alerts_list(limit: int = Query(200, ge=1, le=2000)):
    from app.alerts.telegram import TelegramNotifier
    from .alerts import recent
    eng = db.engine()
    return {"alerts": records(recent(eng, limit)), "telegram": TelegramNotifier.from_env().info(),
            "kinds": load()["alerts"]["kinds"], "demo": _is_demo(eng)}


@router.post("/alerts/run")
async def alerts_run(send: bool = True):
    from .alerts import generate, send_pending
    eng = db.engine()
    panel = await run_in_threadpool(panel_cached, eng)
    made = await run_in_threadpool(generate, eng, panel)
    sent = await run_in_threadpool(send_pending, eng) if send else None
    return {"generated": made, "telegram": sent}


@router.post("/alerts/test")
async def alerts_test():
    from datetime import datetime
    from .alerts import add, send_pending
    eng = db.engine()
    add(eng, "test", None, None, date.today(), f"Test alert {datetime.now():%H:%M:%S}", "Telegram alerts work.")
    res = await run_in_threadpool(send_pending, eng)
    if res.get("skipped"):
        raise HTTPException(400, res["skipped"] + " — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in backend/.env")
    return res


# ------------------------------------------------------------- Phase 8 --- #
class _BacktestIn(BaseModel):
    kind: str = "ranked"                 # ranked | signal | walk_forward | sensitivity
    factors: Optional[list[str]] = None
    signal: Optional[str] = None
    top_k: Optional[int] = None
    rebalance: Optional[int] = None


@router.get("/backtests")
def backtests_list():
    from .backtest import list_runs
    from .scoring import FACTORS
    eng = db.engine()
    b = load()["backtest"]
    from .upstox_ingest import survivorship_warning
    return {"runs": list_runs(eng), "warning": survivorship_warning(eng), "factors": list(FACTORS), "signals": list(SIGNALS), "variants": b["variants"],
            "defaults": {k: b[k] for k in ("top_k", "rebalance_sessions", "train_sessions", "test_sessions")},
            "demo": _is_demo(eng)}


@router.get("/backtests/{run_id}")
def backtests_get(run_id: int):
    from .backtest import get_run
    r = get_run(db.engine(), run_id)
    if r is None:
        raise HTTPException(404, "no such backtest")
    return r


@router.post("/backtests")
async def backtests_run(body: _BacktestIn):
    """Run and store a portfolio backtest (may take a minute or more on full NSE history)."""
    from .backtest import run
    from .scoring import FACTORS
    if body.kind not in ("ranked", "signal", "walk_forward", "sensitivity"):
        raise HTTPException(422, "kind must be ranked, signal, walk_forward or sensitivity")
    bad = [f for f in body.factors or [] if f not in FACTORS]
    if bad:
        raise HTTPException(422, f"unknown factors {bad}")
    eng = db.engine()
    panel = await run_in_threadpool(panel_cached, eng)
    if panel.empty:
        raise HTTPException(409, "No price data.")
    try:
        res = await run_in_threadpool(run, eng, panel, body.kind, body.factors, body.signal, body.top_k, body.rebalance)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return res


# ------------------------------------------------------------- Phase 9 --- #
class _AskIn(BaseModel):
    question: str
    history: list[dict] = []
    effort: str = "high"


@router.get("/assistant/status")
def assistant_status():
    from .assistant import MODEL, TOOLS, configured
    return {"configured": configured(), "model": MODEL, "tools": [t["name"] for t in TOOLS]}


@router.post("/assistant")
async def assistant_ask(body: _AskIn):
    """Ask the research assistant. Answers are grounded in this database through read-only tools."""
    import anthropic
    from .assistant import Tools, ask, configured
    if not configured():
        raise HTTPException(400, "Set ANTHROPIC_API_KEY in backend/.env and restart the backend.")
    if body.effort not in ("low", "medium", "high", "xhigh", "max"):
        raise HTTPException(422, "effort must be low, medium, high, xhigh or max")
    q = body.question.strip()
    if not q or len(q) > 4000:
        raise HTTPException(422, "question must be 1-4000 characters")
    eng = db.engine()
    tools = Tools(eng, lambda: panel_cached(eng))
    try:
        return await run_in_threadpool(ask, q, body.history[-20:], tools, None, body.effort)
    except anthropic.AuthenticationError:
        raise HTTPException(401, "ANTHROPIC_API_KEY was rejected — check the key in backend/.env")
    except anthropic.RateLimitError:
        raise HTTPException(429, "Claude API rate limit reached — try again shortly")
    except anthropic.APIStatusError as exc:
        raise HTTPException(502, f"Claude API error {exc.status_code}: {exc.message}")
    except anthropic.APIConnectionError:
        raise HTTPException(502, "Could not reach the Claude API (network)")
