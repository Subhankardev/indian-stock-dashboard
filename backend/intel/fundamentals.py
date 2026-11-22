"""Fundamentals: point-in-time growth, quality and valuation, sector-aware (Phase 5).

Data comes from a CSV import (``python -m intel.imports fundamentals file.csv``), long format::

    symbol,period_end,period_type,metric,value[,published_on]

``period_type`` is Q (quarter) or A (annual). Values are in ₹ crore except ``eps`` (₹),
``shares`` (crore shares) and percentages (``gnpa_pct``, ``nnpa_pct``, ``car_pct``).
Without ``published_on`` a quarter is assumed public 45 days after period end and a year
60 days after (SEBI LODR deadlines), so a number is never used before it could have been known.

Metrics used (others are stored and shown but not scored):
    revenue, ebitda, pat, eps, interest, total_equity, total_debt, cfo, shares,
    gnpa_pct, nnpa_pct, car_pct (banks / NBFCs)

**Sector-aware**: for financials (industry "Financial Services" or names with Bank/Finance)
debt/equity, EBITDA margin and CFO/PAT are not meaningful and are excluded; asset quality
(GNPA/NNPA) is used instead when present. Scores are percentiles *within the industry* when it
has ≥ ``min_peers`` stocks with data, else within the whole universe (flagged).
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import select

from . import db
from .config import load

METRICS = ["revenue", "ebitda", "pat", "eps", "interest", "total_equity", "total_debt", "cfo", "shares",
           "gnpa_pct", "nnpa_pct", "car_pct"]
FLOW = ["revenue", "ebitda", "pat", "eps", "interest", "cfo"]      # summed over 4 quarters for TTM
STOCK = ["total_equity", "total_debt", "shares", "gnpa_pct", "nnpa_pct", "car_pct"]  # latest value


def is_financial(industry: Optional[str]) -> bool:
    s = industry.lower() if isinstance(industry, str) else ""
    return "financial" in s or "bank" in s or "finance" in s or "insurance" in s


def load_long(eng, symbols=None) -> pd.DataFrame:
    stmt = select(db.fundamentals)
    if symbols is not None:
        stmt = stmt.where(db.fundamentals.c.symbol.in_(list(symbols)))
    return db.read_sql(eng, stmt)


def point_in_time(f: pd.DataFrame) -> pd.DataFrame:
    """One row per (symbol, effective date = a publication date) with metrics computed only from
    periods published on or before it."""
    if f.empty:
        return pd.DataFrame()
    f = f.copy()
    f["period_end"] = pd.to_datetime(f["period_end"])
    f["published_on"] = pd.to_datetime(f["published_on"])
    f["ptype"] = f["metric"].str.extract(r"^(Q|A):")[0].fillna("Q")
    f["metric"] = f["metric"].str.replace(r"^(Q|A):", "", regex=True)
    out = []
    for sym, g in f.groupby("symbol"):
        # pivot once per symbol; a period is known once its (latest) publication date has passed
        piv = {}
        for pt in ("Q", "A"):
            h = g[g["ptype"] == pt]
            wide = h.pivot_table(index="period_end", columns="metric", values="value", aggfunc="last").sort_index()
            pub = h.groupby("period_end")["published_on"].max().reindex(wide.index)
            piv[pt] = (wide, pub)
        for eff in sorted(g["published_on"].dropna().unique()):
            q = piv["Q"][0][(piv["Q"][1] <= eff).to_numpy()]
            a = piv["A"][0][(piv["A"][1] <= eff).to_numpy()]
            row = {"symbol": sym, "effective": eff}
            src = None
            if len(q) >= 4:
                last4 = q.tail(4)
                # require 4 consecutive quarters (≈ 1 year span)
                if (last4.index[-1] - last4.index[0]).days <= 300:
                    src = "ttm"
                    for m in FLOW:
                        if m in q and last4[m].notna().all():
                            row[f"ttm_{m}"] = float(last4[m].sum())
                    prev4 = q[q.index <= last4.index[-1] - pd.DateOffset(months=11)].tail(4)
                    if len(prev4) == 4 and (prev4.index[-1] - prev4.index[0]).days <= 300:
                        for m in ("revenue", "pat"):
                            if m in q and prev4[m].notna().all():
                                row[f"prev_ttm_{m}"] = float(prev4[m].sum())
                    row["period_end"] = last4.index[-1]
            if src is None and len(a):
                last = a.iloc[-1]
                src = "annual"
                for m in FLOW:
                    if m in a and pd.notna(last.get(m)):
                        row[f"ttm_{m}"] = float(last[m])
                if len(a) >= 2:
                    for m in ("revenue", "pat"):
                        if m in a and pd.notna(a.iloc[-2].get(m)):
                            row[f"prev_ttm_{m}"] = float(a.iloc[-2][m])
                row["period_end"] = a.index[-1]
            if src is None:
                continue
            row["basis"] = src
            both = pd.concat([q, a]).sort_index()
            for m in STOCK:
                if m in both and both[m].notna().any():
                    row[m] = float(both[m].dropna().iloc[-1])
            # average equity over the last year for ROE
            if "total_equity" in both and both["total_equity"].notna().sum() >= 2:
                eq = both["total_equity"].dropna()
                row["avg_equity"] = float(eq[eq.index >= eq.index[-1] - pd.DateOffset(months=12)].mean())
            out.append(row)
    pit = pd.DataFrame(out)
    if pit.empty:
        return pit
    def col(c):
        return pit[c] if c in pit else pd.Series(np.nan, index=pit.index)
    eq = col("avg_equity").fillna(col("total_equity"))
    pit["roe_pct"] = col("ttm_pat") / eq.where(eq > 0) * 100
    cap_employed = col("total_equity") + col("total_debt").fillna(0)
    pit["roce_pct"] = (col("ttm_pat") + col("ttm_interest").fillna(0)) / cap_employed.where(cap_employed > 0) * 100
    pit["de_ratio"] = col("total_debt") / col("total_equity").where(col("total_equity") > 0)
    pit["ebitda_margin_pct"] = col("ttm_ebitda") / col("ttm_revenue").where(col("ttm_revenue") > 0) * 100
    pit["pat_margin_pct"] = col("ttm_pat") / col("ttm_revenue").where(col("ttm_revenue") > 0) * 100
    pit["cfo_to_pat"] = col("ttm_cfo") / col("ttm_pat").where(col("ttm_pat") > 0)
    pit["rev_growth_pct"] = (col("ttm_revenue") / col("prev_ttm_revenue").where(col("prev_ttm_revenue") > 0) - 1) * 100
    pg = col("prev_ttm_pat")
    pit["pat_growth_pct"] = np.where(pg > 0, (col("ttm_pat") / pg.where(pg > 0) - 1) * 100, np.nan)
    eps = col("ttm_eps")
    shares = col("shares")
    pit["eps_used"] = eps.fillna(col("ttm_pat") / shares.where(shares > 0))
    pit["bvps"] = col("total_equity") / shares.where(shares > 0)
    return pit


QUALITY_INPUTS = {  # name: (higher is better?, used for financials?)
    "roe_pct": (True, True), "roce_pct": (True, False), "rev_growth_pct": (True, True),
    "pat_growth_pct": (True, True), "cfo_to_pat": (True, False), "de_ratio": (False, False),
    "ebitda_margin_pct": (True, False), "gnpa_pct": (False, True),
}
VALUE_INPUTS = {"earnings_yield_pct": True, "book_yield_pct": True}


def _sector_pct(df: pd.DataFrame, col: str, higher: bool, group: pd.Series, min_peers: int) -> tuple[pd.Series, pd.Series]:
    """Percentile within (date, industry) when ≥min_peers have data, else within date."""
    v = df[col] if higher else -df[col]
    n_in_group = v.notna().groupby([df["date"], group]).transform("sum")
    in_sector = v.groupby([df["date"], group]).rank(pct=True) * 100
    universe = v.groupby(df["date"]).rank(pct=True) * 100
    use_sector = n_in_group >= min_peers
    return in_sector.where(use_sector, universe), use_sector


def features(eng, panel: pd.DataFrame, cfg: Optional[dict] = None) -> pd.DataFrame:
    """Point-in-time fundamentals aligned to panel rows, plus sector-relative scores."""
    cfg = (cfg or load()).get("fundamentals", {})
    min_peers = cfg.get("min_peers", 5)
    cols = ["fa_effective", "fa_basis", "pe", "pb", "mcap_cr", "earnings_yield_pct", "book_yield_pct", "roe_pct",
            "roce_pct", "de_ratio", "ebitda_margin_pct", "pat_margin_pct", "cfo_to_pat", "rev_growth_pct",
            "pat_growth_pct", "gnpa_pct", "quality_score", "value_score", "fa_sector_relative", "fa_financial"]
    out = pd.DataFrame(index=panel.index, columns=cols)
    if panel.empty:
        return out
    pit = point_in_time(load_long(eng, panel["symbol"].unique()))
    if pit.empty:
        return out
    keyed = panel[["symbol", "date", "close", "industry"]].reset_index()
    pit = pit.sort_values("effective")
    merged = pd.merge_asof(keyed.sort_values("date"), pit.rename(columns={"effective": "fa_effective"}),
                           left_on="date", right_on="fa_effective", by="symbol", direction="backward")
    max_age = pd.Timedelta(days=cfg.get("max_age_days", 270))
    stale = (merged["date"] - merged["fa_effective"]) > max_age
    fa_cols = [c for c in pit.columns if c not in ("symbol", "effective")]
    merged.loc[stale, fa_cols] = np.nan
    merged.loc[stale, "fa_effective"] = pd.NaT
    m = merged
    m["fa_financial"] = m["industry"].map(is_financial).astype(bool)
    m["fa_basis"] = m["basis"] if "basis" in m else None
    eps, bvps = m.get("eps_used"), m.get("bvps")
    m["pe"] = np.where(eps > 0, m["close"] / eps, np.nan)
    m["pb"] = np.where(bvps > 0, m["close"] / bvps, np.nan)
    m["earnings_yield_pct"] = eps / m["close"] * 100
    m["book_yield_pct"] = bvps / m["close"] * 100
    m["mcap_cr"] = m["close"] * m.get("shares", np.nan) if "shares" in m else np.nan  # shares in crore → ₹ cr
    for c in QUALITY_INPUTS:
        if c not in m:
            m[c] = np.nan
    fin = m["fa_financial"]
    for c, (_, for_fin) in QUALITY_INPUTS.items():
        if not for_fin:
            m.loc[fin, c] = np.nan
    grp = m["industry"].fillna("Unknown")
    q_parts, sector_used = [], []
    for c, (higher, _) in QUALITY_INPUTS.items():
        pr, used = _sector_pct(m, c, higher, grp, min_peers)
        q_parts.append(pr)
        sector_used.append(used & m[c].notna())
    q = pd.concat(q_parts, axis=1)
    m["quality_score"] = q.mean(axis=1, skipna=True).where(q.notna().sum(axis=1) >= 3)
    v_parts = [_sector_pct(m, c, True, grp, min_peers)[0] for c in VALUE_INPUTS]
    v = pd.concat(v_parts, axis=1)
    m["value_score"] = v.mean(axis=1, skipna=True).where(v.notna().sum(axis=1) >= 1)
    m["fa_sector_relative"] = pd.concat(sector_used, axis=1).any(axis=1)
    m = m.set_index("index").reindex(panel.index)
    for c in cols:
        out[c] = m[c] if c in m else np.nan
    return out
