"""F&O analytics from the NSE UDiFF F&O bhavcopy and participant-wise OI (Phase 3).

What this measures, and what it does not:

* **Build-up classes** describe *positioning in that stock's futures* from price and open
  interest (OI): long build-up (price ↑, OI ↑), short build-up (price ↓, OI ↑), short covering
  (price ↑, OI ↓), long unwinding (price ↓, OI ↓). They do **not** say who traded, and an OI
  increase is not "institutions buying". Evidence class **F** (derivatives positioning).
* **Participant OI** (FII / DII / Pro / Client) is market-wide (class C) — never stock-specific.
* Total futures OI is summed across expiries so monthly rollover doesn't look like unwinding;
  build-ups are still blanked for a few sessions after an expiry, when OI legitimately drops.

    python -m intel.derivatives --rebuild      # recompute deriv_daily from stored fo_daily rows
"""
from __future__ import annotations

import argparse
import math
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import select

from . import db
from .config import load

BUILDUPS = ("long_buildup", "short_buildup", "short_covering", "long_unwinding")


# ------------------------------------------------------------- Black-Scholes --- #
def _ncdf(x):
    return 0.5 * (1 + np.vectorize(math.erf)(x / math.sqrt(2)))


def bs_price(spot, strike, t, r, vol, call: bool):
    vol = np.maximum(vol, 1e-6)
    d1 = (np.log(spot / strike) + (r + 0.5 * vol ** 2) * t) / (vol * np.sqrt(t))
    d2 = d1 - vol * np.sqrt(t)
    if call:
        return spot * _ncdf(d1) - strike * np.exp(-r * t) * _ncdf(d2)
    return strike * np.exp(-r * t) * _ncdf(-d2) - spot * _ncdf(-d1)


def implied_vol(price: float, spot: float, strike: float, t: float, r: float, call: bool) -> Optional[float]:
    """Bisection IV (annualised, as a fraction). None when the price is outside no-arbitrage bounds."""
    if not all(np.isfinite([price, spot, strike, t])) or price <= 0 or spot <= 0 or t <= 0:
        return None
    intrinsic = max(spot - strike * math.exp(-r * t), 0) if call else max(strike * math.exp(-r * t) - spot, 0)
    if price < intrinsic - 1e-9 or price >= (spot if call else strike):
        return None
    lo, hi = 1e-4, 5.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if bs_price(spot, strike, t, r, mid, call) > price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


# ------------------------------------------------------------ daily summary --- #
def summarize_day(fo: pd.DataFrame, r: Optional[float] = None) -> pd.DataFrame:
    """One row per underlying from one day's parsed F&O bhavcopy (``parsers.fo_udiff``)."""
    if fo.empty:
        return pd.DataFrame()
    r = load()["derivatives"]["risk_free_rate"] if r is None else r
    rows = []
    for (day, sym), g in fo.groupby(["date", "symbol"]):
        fut = g[g["instrument"].isin(["STF", "IDF"])].sort_values("expiry")
        opt = g[g["instrument"].isin(["STO", "IDO"])]
        kind = "index" if g["instrument"].isin(["IDF", "IDO"]).any() else "stock"
        under = g["underlying"].dropna()
        spot = float(under.iloc[0]) if len(under) else np.nan
        row = {"date": day, "symbol": sym, "kind": kind, "underlying": spot,
               "lot_size": float(g["lot_size"].dropna().iloc[0]) if g["lot_size"].notna().any() else None}
        if len(fut):
            near = fut.iloc[0]
            total_oi = fut["oi"].sum()
            row.update(near_expiry=near["expiry"], fut_close=near["close"], fut_oi=total_oi,
                       fut_oi_change=fut["oi_change"].sum(), fut_volume=fut["volume"].sum(),
                       basis_pct=(near["close"] / spot - 1) * 100 if spot and np.isfinite(spot) else None,
                       rollover_pct=(fut["oi"].iloc[1:].sum() / total_oi * 100) if total_oi else None)
        if len(opt):
            near_exp = opt["expiry"].min()
            o = opt[opt["expiry"] == near_exp]
            ce, pe = o[o["option_type"] == "CE"], o[o["option_type"] == "PE"]
            ce_oi, pe_oi = ce["oi"].sum(), pe["oi"].sum()
            row.update(ce_oi=ce_oi, pe_oi=pe_oi, pcr_oi=pe_oi / ce_oi if ce_oi else None,
                       ce_volume=ce["volume"].sum(), pe_volume=pe["volume"].sum(),
                       pcr_volume=pe["volume"].sum() / ce["volume"].sum() if ce["volume"].sum() else None,
                       max_ce_oi_strike=float(ce.loc[ce["oi"].idxmax(), "strike"]) if len(ce) and ce["oi"].max() > 0 else None,
                       max_pe_oi_strike=float(pe.loc[pe["oi"].idxmax(), "strike"]) if len(pe) and pe["oi"].max() > 0 else None)
            row.setdefault("near_expiry", near_exp)
            if np.isfinite(spot) and len(o):
                strikes = np.sort(o["strike"].unique())
                atm = float(strikes[np.abs(strikes - spot).argmin()])
                t = max((pd.Timestamp(near_exp) - pd.Timestamp(day)).days, 0.5) / 365.0
                ivs = []
                for typ, call in (("CE", True), ("PE", False)):
                    leg = o[(o["strike"] == atm) & (o["option_type"] == typ)]
                    if len(leg):
                        px = leg["settle"].iloc[0] if pd.notna(leg["settle"].iloc[0]) and leg["settle"].iloc[0] > 0 else leg["close"].iloc[0]
                        iv = implied_vol(float(px), spot, atm, t, r, call)
                        if iv is not None:
                            ivs.append(iv)
                row.update(atm_strike=atm, atm_iv=float(np.mean(ivs) * 100) if ivs else None)
        if row.get("near_expiry") is not None:
            row["days_to_expiry"] = (pd.Timestamp(row["near_expiry"]) - pd.Timestamp(day)).days
        rows.append(row)
    cols = [c.name for c in db.deriv_daily.columns if c.name != "source_id"]
    return pd.DataFrame(rows).reindex(columns=cols)


def store_day(eng, fo: pd.DataFrame, source_id: Optional[int]) -> int:
    s = summarize_day(fo)
    if s.empty:
        return 0
    day = s["date"].iloc[0]
    return db.replace_rows(eng, db.deriv_daily, s.assign(source_id=source_id), [db.deriv_daily.c.date == day])


def rebuild(eng) -> int:
    days = db.read_sql(eng, select(db.fo_daily.c.date).distinct())["date"]
    n = 0
    for d in days:
        fo = db.read_sql(eng, select(db.fo_daily).where(db.fo_daily.c.date == d))
        n += store_day(eng, fo, fo["source_id"].iloc[0] if len(fo) else None)
    return n


# --------------------------------------------------------- per-stock features --- #
def load_daily(eng, symbols=None, start=None) -> pd.DataFrame:
    stmt = select(db.deriv_daily)
    if symbols is not None:
        stmt = stmt.where(db.deriv_daily.c.symbol.in_(list(symbols)))
    if start is not None:
        stmt = stmt.where(db.deriv_daily.c.date >= start)
    d = db.read_sql(eng, stmt.order_by(db.deriv_daily.c.symbol, db.deriv_daily.c.date))
    if len(d):
        d["date"] = pd.to_datetime(d["date"])
    return d


def classify_buildup(ret_pct: pd.Series, oi_chg_pct: pd.Series, min_oi: float) -> pd.Series:
    out = pd.Series("neutral", index=ret_pct.index, dtype=object)
    out[(ret_pct > 0) & (oi_chg_pct >= min_oi)] = "long_buildup"
    out[(ret_pct < 0) & (oi_chg_pct >= min_oi)] = "short_buildup"
    out[(ret_pct > 0) & (oi_chg_pct <= -min_oi)] = "short_covering"
    out[(ret_pct < 0) & (oi_chg_pct <= -min_oi)] = "long_unwinding"
    out[ret_pct.isna() | oi_chg_pct.isna()] = None
    return out


def features(eng, panel: pd.DataFrame, cfg: Optional[dict] = None) -> pd.DataFrame:
    """Derivatives columns aligned to panel rows. NaN/None for stocks without F&O."""
    cfg = (cfg or load())["derivatives"]
    cols = ["f_has_fo", "f_oi_chg_pct", "f_ret_pct", "f_buildup", "f_basis_pct", "f_rollover_pct", "f_pcr_oi",
            "f_atm_iv", "f_long_strength", "f_short_strength", "f_expiry_window"]
    out = pd.DataFrame(index=panel.index, columns=cols)
    out["f_has_fo"] = False
    if panel.empty:
        return out
    d = load_daily(eng, panel["symbol"].unique())
    d = d[d["kind"] == "stock"] if len(d) else d
    if d.empty:
        return out
    w, mn, black = cfg["oi_change_window"], cfg["min_oi_change_pct"], cfg["expiry_blackout_sessions"]
    keyed = panel[["symbol", "date", "close"]].reset_index()
    m = keyed.merge(d, on=["symbol", "date"], how="left").sort_values(["symbol", "date"])
    g = m.groupby("symbol", sort=False)
    m["oi_chg"] = g["fut_oi"].pct_change(w, fill_method=None) * 100
    m["ret"] = g["close"].pct_change(w, fill_method=None) * 100
    exp_changed = g["near_expiry"].transform(lambda s: (s != s.shift(1)) & s.shift(1).notna())
    m["exp_window"] = exp_changed.astype(float).groupby(m["symbol"]).transform(
        lambda s: s.rolling(black + 1, min_periods=1).max()) > 0
    m["buildup"] = classify_buildup(m["ret"], m["oi_chg"], mn)
    m.loc[m["exp_window"], "buildup"] = "expiry"
    m = m.set_index("index").reindex(panel.index)
    out["f_has_fo"] = m["fut_oi"].notna()
    out["f_oi_chg_pct"], out["f_ret_pct"], out["f_buildup"] = m["oi_chg"], m["ret"], m["buildup"]
    out["f_basis_pct"], out["f_rollover_pct"], out["f_pcr_oi"], out["f_atm_iv"] = (
        m["basis_pct"], m["rollover_pct"], m["pcr_oi"], m["atm_iv"])
    out["f_expiry_window"] = m["exp_window"].fillna(False).astype(bool)
    long_ = (m["buildup"] == "long_buildup")
    short_ = (m["buildup"] == "short_buildup")
    out["f_long_strength"] = np.where(out["f_has_fo"], np.where(long_, m["oi_chg"], 0.0), np.nan)
    out["f_short_strength"] = np.where(out["f_has_fo"], np.where(short_, m["oi_chg"], 0.0), np.nan)
    return out


# ----------------------------------------------------------- market-wide (C) --- #
def participant_summary(eng, days: int = 120) -> pd.DataFrame:
    """Index-futures positioning per participant: long %, net contracts and their changes."""
    p = db.read_sql(eng, select(db.participant_oi).order_by(db.participant_oi.c.date))
    if p.empty:
        return p
    p = p[p["client_type"].isin(["FII", "DII", "Pro", "Client"])].copy()
    tot = p["fut_idx_long"] + p["fut_idx_short"]
    p["idx_fut_long_pct"] = np.where(tot > 0, p["fut_idx_long"] / tot * 100, np.nan)
    p["idx_fut_net"] = p["fut_idx_long"] - p["fut_idx_short"]
    p["idx_opt_net_calls"] = p["opt_idx_call_long"] - p["opt_idx_call_short"]
    p["idx_opt_net_puts"] = p["opt_idx_put_long"] - p["opt_idx_put_short"]
    p = p.sort_values(["client_type", "date"])
    p["idx_fut_net_chg"] = p.groupby("client_type")["idx_fut_net"].diff()
    last_dates = sorted(p["date"].unique())[-days:]
    return p[p["date"].isin(last_dates)].reset_index(drop=True)


def index_summary(eng, names=("NIFTY", "BANKNIFTY"), days: int = 120) -> pd.DataFrame:
    d = db.read_sql(eng, select(db.deriv_daily).where(db.deriv_daily.c.symbol.in_(list(names)))
                    .order_by(db.deriv_daily.c.date))
    if d.empty:
        return d
    last = sorted(d["date"].unique())[-days:]
    return d[d["date"].isin(last)].reset_index(drop=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rebuild", action="store_true", help="recompute deriv_daily from fo_daily")
    a = ap.parse_args(argv)
    if a.rebuild:
        print("deriv_daily rows:", rebuild(db.engine()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
