"""Multi-factor scoring (docs/DESIGN.md §10, Phase 6).

For each stock and date every factor is a cross-sectional percentile p_f ∈ [0,100] among
liquid stocks. Bullish and bearish scores combine only **active** factors — those whose own
test (``factor_<name>``: the top quintile of p_f, as an event study) passed::

    score      = Σ_active w_f · p_f · c_f / Σ_active w_f · c_f
    confidence = Σ_active w_f · c_f / Σ_active w_f        (c_f = 1 with data, 0 missing,
                                                           0.5 for fundamentals older than 180 days)
    agreement  = share of active factors with data where p_f > 60

Missing data is never neutral. Rankings require confidence ≥ ``scoring.min_confidence``.
Conflicts (e.g. strong accumulation *and* distribution evidence) are flagged, not averaged.
The score is a rank, not a probability.

``preview=True`` treats every factor as active. Results are labelled UNVALIDATED.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import func, select

from . import db
from .config import load
from .market import attach_sector

FACTORS = {
    # name: (config weight key, description)
    "accumulation": ("accumulation", "Evidence of accumulation (A deals, B ownership, D inferred, F futures)"),
    "relative_strength": ("relative_strength", "6-month return vs Nifty 50, rank among liquid stocks"),
    "momentum": ("momentum", "20-day rate of change, 3-month RS and nearness to the 52-week high"),
    "volume_delivery": ("volume_delivery", "Relative volume, up/down volume, delivery trend"),
    "derivatives": ("derivatives", "Futures long build-up strength (F&O stocks only)"),
    "fundamentals": ("fundamentals", "Sector-relative quality (point-in-time)"),
    "valuation": ("valuation", "Sector-relative value: earnings and book yield"),
    "sector": ("sector", "Industry's median relative strength vs other industries"),
    "risk": ("risk", "Lower 20-day volatility and ATR% rank higher"),
}


def _pct(s: pd.Series, dates: pd.Series, higher: bool = True) -> pd.Series:
    v = s.astype(float) if higher else -s.astype(float)
    return v.groupby(dates).rank(pct=True) * 100


def _avg(*series: pd.Series) -> pd.Series:
    df = pd.concat(series, axis=1)
    return df.mean(axis=1, skipna=True).where(df.notna().any(axis=1))


def factor_frame(panel: pd.DataFrame) -> pd.DataFrame:
    """Bullish percentiles p_<f>, bearish percentiles q_<f>, and data-coverage c_<f>, per row."""
    if panel.empty:
        return pd.DataFrame(index=panel.index)
    liquid = panel["liquid"].fillna(False).astype(bool)
    p = panel
    d = panel["date"].where(liquid)  # NaN date → excluded from every cross-sectional rank
    out = pd.DataFrame(index=panel.index)
    col = lambda c: p[c].astype(float) if c in p else pd.Series(np.nan, index=p.index)  # noqa: E731

    # accumulation / distribution evidence (A, B, D, F), weighted as in evidence.COMPONENT_WEIGHTS
    a_net20 = col("a_inst_net_cr").fillna(0).groupby(p["symbol"]).transform(lambda s: s.rolling(20, min_periods=1).sum())
    a_rel = a_net20 / (col("avg_value_cr") * 20)
    have_deals = "a_inst_net_cr" in p and bool((p["a_inst_net_cr"].fillna(0) != 0).any())
    comps_acc = {"A": _pct(a_rel, d) if have_deals else None, "B": _pct(col("b_inst_change_pp_last"), d),
                 "D": col("d_score") / 5 * 100, "F": _pct(col("f_long_strength"), d)}
    comps_dist = {"A": _pct(-a_rel, d) if have_deals else None, "B": _pct(-col("b_inst_change_pp_last"), d),
                  "D": col("x_score") / 5 * 100, "F": _pct(col("f_short_strength"), d)}
    w = {"A": 0.30, "B": 0.25, "D": 0.30, "F": 0.15}

    def wavg(comps):
        num = pd.Series(0.0, index=p.index)
        den = pd.Series(0.0, index=p.index)
        for k, s in comps.items():
            if s is None:
                continue
            have = s.notna()
            num += np.where(have, w[k] * s.fillna(0), 0.0)
            den += np.where(have, w[k], 0.0)
        return (num / den.replace(0, np.nan))

    out["p_accumulation"] = _pct(wavg(comps_acc), d)
    out["q_accumulation"] = _pct(wavg(comps_dist), d)        # bearish counterpart = distribution
    out["p_relative_strength"] = col("rs_rank").where(liquid)
    out["q_relative_strength"] = 100 - out["p_relative_strength"]
    mom = _avg(_pct(col("roc20"), d), _pct(col("rs_3m"), d), _pct(col("dist_52w_high_pct"), d))
    out["p_momentum"] = mom
    out["q_momentum"] = 100 - mom
    deliv_trend = col("deliv_qty_avg20") / col("deliv_qty_avg50")
    vol = _avg(_pct(col("rvol20"), d), _pct(col("updown_vol20"), d), _pct(deliv_trend, d))
    out["p_volume_delivery"] = vol
    out["q_volume_delivery"] = _avg(_pct(col("rvol20"), d), _pct(-col("updown_vol20"), d), _pct(deliv_trend, d))
    out["p_derivatives"] = _pct(col("f_long_strength"), d)
    out["q_derivatives"] = _pct(col("f_short_strength"), d)
    out["p_fundamentals"] = col("quality_score").where(liquid)
    out["q_fundamentals"] = 100 - out["p_fundamentals"]
    out["p_valuation"] = col("value_score").where(liquid)
    out["q_valuation"] = 100 - out["p_valuation"]
    out["p_sector"] = col("sector_rank_pct").where(liquid)
    out["q_sector"] = 100 - out["p_sector"]
    risk = _avg(_pct(col("volatility20"), d, higher=False), _pct(col("atr_pct"), d, higher=False))
    out["p_risk"] = risk
    out["q_risk"] = risk
    # coverage: 1 with data, 0 missing; fundamentals older than 180 days count half
    for f in FACTORS:
        out[f"c_{f}"] = out[f"p_{f}"].notna().astype(float)
    if "fa_effective" in p:
        age = (p["date"] - pd.to_datetime(p["fa_effective"])).dt.days
        stale = age > 180
        for f in ("fundamentals", "valuation"):
            out.loc[stale & out[f"p_{f}"].notna(), f"c_{f}"] = 0.5
    return out


def enrich(eng, panel: pd.DataFrame) -> pd.DataFrame:
    """Panel + all evidence + sector strength + factor percentiles."""
    from .evidence import attach
    p = attach(eng, panel)
    if p.empty:
        return p
    p = pd.concat([p, attach_sector(p)], axis=1)
    return pd.concat([p, factor_frame(p)], axis=1)


def active_factors(eng) -> set[str]:
    latest = (select(db.signal_tests.c.signal_name, func.max(db.signal_tests.c.id).label("id"))
              .group_by(db.signal_tests.c.signal_name).subquery())
    with eng.connect() as c:
        rows = c.execute(select(db.signal_tests.c.signal_name, db.signal_tests.c.passed)
                         .join(latest, db.signal_tests.c.id == latest.c.id)).all()
    return {r.signal_name[len("factor_"):] for r in rows if r.signal_name.startswith("factor_") and r.passed}


def score(df: pd.DataFrame, active: set[str], weights: dict, side: str = "bullish") -> pd.DataFrame:
    """Score rows of an enriched panel (any subset, e.g. the latest snapshot)."""
    pre = "p_" if side == "bullish" else "q_"
    num = pd.Series(0.0, index=df.index)
    den = pd.Series(0.0, index=df.index)
    agree = pd.Series(0.0, index=df.index)
    avail = pd.Series(0.0, index=df.index)
    for f, (wkey, _) in FACTORS.items():
        if f not in active or f"{pre}{f}" not in df:
            continue
        wf = float(weights.get(wkey, 0))
        val, cov = df[f"{pre}{f}"].astype(float), df[f"c_{f}"].astype(float)
        num += wf * val.fillna(0) * cov
        den += wf * cov
        avail += val.notna()
        agree += (val > 60)
    total_w = sum(float(weights.get(FACTORS[f][0], 0)) for f in active if f in FACTORS)
    out = pd.DataFrame(index=df.index)
    out[f"{side}_score"] = (num / den.replace(0, np.nan))
    out[f"{side}_confidence"] = den / total_w if total_w else np.nan
    out[f"{side}_agreement"] = agree / avail.replace(0, np.nan)
    return out


def conflicts(row: pd.Series) -> list[str]:
    g = lambda k: row.get(k) if row.get(k) is not None and not pd.isna(row.get(k)) else None  # noqa: E731
    out = []
    if (g("p_accumulation") or 0) >= 70 and (g("q_accumulation") or 0) >= 70:
        out.append("Accumulation and distribution evidence are both strong")
    if (g("p_relative_strength") or 0) >= 80 and (g("x_score") or 0) >= 3:
        out.append("Strong relative strength but ≥3 distribution clues")
    if (g("p_accumulation") or 0) >= 70 and row.get("f_buildup") == "short_buildup":
        out.append("Accumulation evidence while futures show a short build-up")
    if (g("p_momentum") or 0) >= 80 and g("p_fundamentals") is not None and g("p_fundamentals") <= 20:
        out.append("Strong momentum with bottom-quintile quality")
    if row.get("a_inst_sell") and (g("p_accumulation") or 0) >= 70:
        out.append("Institutional selling in today's deals despite accumulation evidence")
    return out


def rankings(eng, panel: pd.DataFrame, preview: bool = False, limit: int = 30, cfg: Optional[dict] = None) -> dict:
    cfg = cfg or load()
    sc = cfg["scoring"]
    weights, min_conf = sc["weights"], sc["min_confidence"]
    active = set(FACTORS) if preview else active_factors(eng)
    last = panel["date"].max()
    snap = panel[(panel["date"] == last) & panel["liquid"].fillna(False).astype(bool)].copy()
    res = {"as_of": str(pd.Timestamp(last).date()), "active": sorted(active), "preview": preview,
           "factors": {f: {"description": d, "weight": weights.get(k, 0), "active": f in active}
                       for f, (k, d) in FACTORS.items()},
           "min_confidence": min_conf, "bullish": [], "bearish": [], "below_confidence": 0}
    if not active or snap.empty:
        res["message"] = ("No factor has passed its test yet, so nothing is ranked. Run the Research tests "
                          "(factor_* signals), or view the unvalidated preview.")
        return res
    for side in ("bullish", "bearish"):
        s = score(snap, active, weights, side)
        snap = snap.join(s)
    for side in ("bullish", "bearish"):
        ok = snap[f"{side}_confidence"] >= min_conf
        res["below_confidence"] += int((~ok & snap[f"{side}_score"].notna()).sum())
        top = snap[ok].sort_values(f"{side}_score", ascending=False).head(limit)
        rows = []
        pre = "p_" if side == "bullish" else "q_"
        for _, r in top.iterrows():
            rows.append({"symbol": r["symbol"], "industry": r.get("industry"), "close": float(r["close"]),
                         "score": float(r[f"{side}_score"]), "confidence": float(r[f"{side}_confidence"]),
                         "agreement": None if pd.isna(r[f"{side}_agreement"]) else float(r[f"{side}_agreement"]),
                         "factors": {f: (None if pd.isna(r.get(f"{pre}{f}")) else float(r[f"{pre}{f}"])) for f in FACTORS},
                         "conflicts": conflicts(r)})
        res[side] = rows
    res["special"] = special_situations(snap)
    return res


SPECIAL = {
    "accumulation_breakout": ("bullish", "Accumulation clues (≥3) on a volume breakout with top-quintile RS"),
    "institutional_buy_above_avwap": ("bullish", "Class-A institutional net buying while above the 52W-high AVWAP"),
    "ownership_up_quality": ("bullish", "Institutional ownership up ≥0.5 pp (published this session) with quality ≥70"),
    "distribution_near_high": ("bearish", "≥3 distribution clues within 5% of the 52-week high"),
    "short_buildup_breakdown": ("bearish", "Futures short build-up on a volume breakdown"),
}


def special_situations(snap: pd.DataFrame) -> list[dict]:
    """Combinations worth a look today. Each is listed with what triggered it; none is a
    recommendation and each still needs its own test before it can be scored."""
    def col(c, default=np.nan):
        return snap[c] if c in snap else pd.Series(default, index=snap.index)
    rules = {
        "accumulation_breakout": (col("d_score", 0) >= 3) & (col("close") > col("dc_upper")) & (col("rvol20", 0) >= 1.5)
                                 & (col("rs_rank", 0) >= 80),
        "institutional_buy_above_avwap": col("a_inst_buy", False).fillna(False).astype(bool) & (col("dist_avwap_hi52_pct") > 0),
        "ownership_up_quality": col("b_inst_up", False).fillna(False).astype(bool) & (col("quality_score").astype(float) >= 70),
        "distribution_near_high": (col("x_score", 0) >= 3) & (col("dist_52w_high_pct") >= -5),
        "short_buildup_breakdown": (col("f_buildup") == "short_buildup") & (col("close") < col("dc_lower")) & (col("rvol20", 0) >= 1.5),
    }
    out = []
    for k, m in rules.items():
        for _, r in snap[m.fillna(False).astype(bool)].iterrows():
            out.append({"kind": k, "direction": SPECIAL[k][0], "description": SPECIAL[k][1], "symbol": r["symbol"],
                        "close": float(r["close"]), "industry": r.get("industry")})
    return out
