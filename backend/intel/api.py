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
from sqlalchemy import func, select

from . import db
from .config import load
from .panel import build_panel, latest_snapshot
from .research.signals import SIGNALS
from .sources import REGISTRY

router = APIRouter(prefix="/api/intel", tags=["intel"])

_cache: dict = {}

SCREEN_COLS = ["symbol", "industry", "close", "ret_1d", "rs_rank", "rs_1m", "rs_3m", "rs_6m", "rs_mom_20",
               "rsi14", "adx", "rvol20", "deliv_pct", "deliv_pct_avg20", "avg_value_cr", "atr_pct",
               "dist_52w_high_pct", "dma50", "dma200", "liquid"]


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
        _cache.update(key=key, panel=build_panel(eng, load()))
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
    return {"demo": _is_demo(eng), "datasets": records(fr), "recent_errors": [dict(r._mapping) for r in errors],
            "registry": REGISTRY, "config": load(), "database": eng.url.render_as_string(hide_password=True)}


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
            "sources": _sources_for(eng, ["cm", "indices", "demo"])}


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
    known = {n: {"direction": d, "description": desc} for n, (_, d, desc) in SIGNALS.items()}
    return {"demo": _is_demo(eng), "signals": known, "results": out,
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
