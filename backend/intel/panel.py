"""Builds the analysis panel: every symbol × date with indicators, RS, liquidity flags.

    panel = build_panel(eng, cfg)            # all stored history
    latest = latest_snapshot(panel)          # one row per symbol, most recent date

Liquidity filters are evaluated per date using only past data, so research on the
panel is survivorship-bias-free (the universe on day t is whatever was liquid on day t).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import select

from . import db
from .config import load
from .indicators import compute_all
from .relative_strength import cross_sectional_rank, rs_vs_benchmark


def load_prices(eng, start: Optional[date] = None, symbols: Optional[list] = None,
                series: Optional[list] = None) -> pd.DataFrame:
    t = db.prices_daily
    stmt = select(t)
    if start:
        stmt = stmt.where(t.c.date >= start)
    if symbols:
        stmt = stmt.where(t.c.symbol.in_(symbols))
    if series:
        stmt = stmt.where(t.c.series.in_(series))
    df = db.read_sql(eng, stmt, parse_dates=["date"])
    if df.empty:
        return df
    # a symbol can move between EQ and BE/BZ; keep one row per symbol/date (prefer EQ)
    df["_pref"] = (df["series"] != "EQ").astype(int)
    df = df.sort_values(["symbol", "date", "_pref"]).drop_duplicates(["symbol", "date"]).drop(columns="_pref")
    return df


def load_index(eng, name: str, start: Optional[date] = None) -> pd.Series:
    t = db.index_daily
    stmt = select(t.c.date, t.c.close).where(t.c.index_name == name)
    if start:
        stmt = stmt.where(t.c.date >= start)
    df = db.read_sql(eng, stmt, parse_dates=["date"])
    return df.set_index("date")["close"].sort_index() if len(df) else pd.Series(dtype=float, name="close")


def index_names(eng) -> list[str]:
    with eng.connect() as c:
        return [r[0] for r in c.execute(select(db.index_daily.c.index_name).distinct())]


def members(eng, index_key: str) -> list[str]:
    t = db.index_members
    with eng.connect() as c:
        latest = c.execute(select(t.c.as_of).where(t.c.index_name == index_key)
                           .order_by(t.c.as_of.desc()).limit(1)).scalar()
        if latest is None:
            return []
        return [r[0] for r in c.execute(select(t.c.symbol).where(t.c.index_name == index_key, t.c.as_of == latest))]


def industries(eng) -> dict[str, str]:
    t = db.index_members
    df = db.read_sql(eng, select(t.c.symbol, t.c.industry, t.c.as_of))
    if df.empty:
        return {}
    return df.sort_values("as_of").drop_duplicates("symbol", keep="last").set_index("symbol")["industry"].to_dict()


def universe_symbols(eng, cfg: dict) -> Optional[list]:
    """None = every symbol in the bhavcopy; else the configured list."""
    name = cfg["universe"]["name"]
    if name == "all":
        return None
    if name == "custom":
        return [s.upper() for s in cfg["universe"]["custom"]]
    if name == "fno":
        t = db.fo_daily
        with eng.connect() as c:
            last = c.execute(select(t.c.date).order_by(t.c.date.desc()).limit(1)).scalar()
            if last is not None:
                return [r[0] for r in c.execute(select(t.c.symbol).distinct().where(t.c.date == last, t.c.instrument == "STF"))]
            d = db.deriv_daily                      # Upstox-loaded F&O summaries
            last = c.execute(select(d.c.date).where(d.c.kind == "stock").order_by(d.c.date.desc()).limit(1)).scalar()
            if last is None:
                return []
            return [r[0] for r in c.execute(select(d.c.symbol).distinct().where(d.c.date == last, d.c.kind == "stock"))]
    return members(eng, name)


def liquidity_flags(g: pd.DataFrame, liq: dict) -> pd.DataFrame:
    """Per-date liquidity tests for one symbol (past ``lookback`` sessions only)."""
    n = liq["lookback_sessions"]
    out = pd.DataFrame(index=g.index)
    out["avg_value_cr"] = (g["turnover_lacs"] / 100).rolling(n, min_periods=n).mean()
    out["avg_volume"] = g["volume"].rolling(n, min_periods=n).mean()
    out["history"] = np.arange(1, len(g) + 1)
    out["volatility20"] = g["close"].pct_change().rolling(20, min_periods=20).std() * 100
    out["liquid"] = ((out["avg_value_cr"] >= liq["min_avg_traded_value_cr"])
                     & (out["avg_volume"] >= liq["min_avg_volume"])
                     & (out["history"] >= liq["min_history_sessions"])
                     & (g["close"] >= liq["min_price"])
                     & (out["volatility20"] <= liq["max_daily_volatility_pct"]))
    return out


def build_panel(eng, cfg: Optional[dict] = None, start: Optional[date] = None,
                symbols: Optional[list] = None) -> pd.DataFrame:
    cfg = cfg or load()
    syms = symbols or universe_symbols(eng, cfg)
    warm = (start - timedelta(days=420)) if start else None  # indicator warm-up
    prices = load_prices(eng, warm, syms, cfg["universe"]["series"])
    if prices.empty:
        return prices
    rs_cfg = cfg["relative_strength"]
    bench = load_index(eng, rs_cfg["benchmark"], warm)
    frames = []
    for sym, g in prices.groupby("symbol", sort=False):
        g = g.set_index("date").sort_index()
        ind = compute_all(g)
        liq = liquidity_flags(g, cfg["liquidity"])
        part = pd.concat([g[["open", "high", "low", "close", "volume", "turnover_lacs", "deliv_qty"]], ind,
                          liq.drop(columns=["volatility20"])], axis=1)
        if len(bench):
            part = part.join(rs_vs_benchmark(g["close"], bench, rs_cfg["windows"]))
        part["symbol"] = sym
        frames.append(part.reset_index())
    panel = pd.concat(frames, ignore_index=True)
    rank_col = f"rs_{rs_cfg['rank_window']}"
    if rank_col in panel:
        liquid = panel["liquid"].fillna(False)
        panel["rs_rank"] = np.nan
        panel.loc[liquid, "rs_rank"] = cross_sectional_rank(panel[liquid], rank_col)
    ind_map = industries(eng)
    panel["industry"] = panel["symbol"].map(ind_map)
    if start:
        panel = panel[panel["date"] >= pd.Timestamp(start)]
    return panel.reset_index(drop=True)


def latest_snapshot(panel: pd.DataFrame) -> pd.DataFrame:
    if panel.empty:
        return panel
    last = panel["date"].max()
    snap = panel[panel["date"] == last].copy()
    return snap.sort_values("rs_rank", ascending=False, na_position="last").reset_index(drop=True)
