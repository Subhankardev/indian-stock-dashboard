"""Accumulation / distribution evidence (docs/DESIGN.md §8–9).

Every feature is computed only from data published by the row's date:

* Class A (confirmed disclosed transactions): bulk/block deals by clients classified as
  institutions (mutual fund, insurer, FPI, bank, pension). Deals are published the same
  evening, so they are usable from that date's close.
* Class B (ownership change): quarterly shareholding (effective from ``published_on``) and
  monthly MF holdings (effective from ``published_on``).
* Class C (market-wide): FII/DII cash flows. Never attached to a stock.
* Class D (possible accumulation/distribution, inferred from price, volume and delivery).
* Class E (unclassified large activity): bulk/block deals by non-institutional clients and
  volume spikes without direction. Shown, never scored.

    python -m intel.evidence --store --days 5      # write fired signals + evidence to `signals`
"""
from __future__ import annotations

import argparse
import json
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import func, select

from . import db
from .config import load
from .entities import classify

RULE_VERSION = "p2.0"

ACC_SUBS = {
    "d_deliv_trend": "Delivery quantity 20D avg ≥1.2× 50D avg while price holds",
    "d_hidel_updays": "≥3 of last 10 sessions: delivery % >1.5× its 50D median, close in upper half",
    "d_updown": "Up-day volume ≥1.3× down-day volume (20D)",
    "d_cmf": "Chaikin money flow (20) positive and rising",
    "d_failed_breakdown": "Broke the 20D low in the last 3 sessions, closed back above on ≥avg volume",
}
DIST_SUBS = {
    "x_deliv_trend": "Delivery quantity 20D avg ≥1.2× 50D avg while price falls",
    "x_hidel_downdays": "≥3 of last 10 sessions: delivery % >1.5× its 50D median, close in lower half",
    "x_updown": "Down-day volume ≥1.3× up-day volume (20D)",
    "x_cmf": "Chaikin money flow (20) negative and falling",
    "x_churn": "Near the 52W high: ≥2× volume with <0.5% price progress in the last 5 sessions",
}


# ----------------------------------------------------------------- class D --- #
def _per_symbol(g: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=g.index)
    close, rng = g["close"], (g["high"] - g["low"]).replace(0, np.nan)
    pos = (close - g["low"]) / rng
    holds, falls = close >= close.shift(20) * 0.98, close < close.shift(20) * 0.98
    trend = g["deliv_qty_avg20"] / g["deliv_qty_avg50"]
    hi_del = g["deliv_pct"] > 1.5 * g["deliv_pct"].rolling(50, min_periods=30).median()
    out["d_deliv_trend"] = (trend >= 1.2) & holds
    out["x_deliv_trend"] = (trend >= 1.2) & falls
    out["d_hidel_updays"] = (hi_del & (pos >= 0.5)).rolling(10, min_periods=10).sum() >= 3
    out["x_hidel_downdays"] = (hi_del & (pos < 0.5)).rolling(10, min_periods=10).sum() >= 3
    out["d_updown"] = g["updown_vol20"] >= 1.3
    out["x_updown"] = g["updown_vol20"] <= 1 / 1.3
    out["d_cmf"] = (g["cmf20"] > 0) & (g["cmf20"] > g["cmf20"].shift(5))
    out["x_cmf"] = (g["cmf20"] < 0) & (g["cmf20"] < g["cmf20"].shift(5))
    broke = g["low"] < g["dc_lower"]
    ref = g["dc_lower"].where(broke).ffill(limit=2)
    out["d_failed_breakdown"] = (broke.astype(float).rolling(3, min_periods=1).max() > 0) & (close > ref) \
        & ~broke & (g["rvol20"] >= 1.0)
    churn = (g["dist_52w_high_pct"] >= -5) & (g["rvol20"] >= 2) & (g["ret_1d"].abs() < 0.5)
    out["x_churn"] = churn.astype(float).rolling(5, min_periods=1).max() > 0
    out["e_volume_spike"] = (g["rvol20"] >= 3) & (g["ret_1d"].abs() < 1)
    return out.fillna(False).astype(bool)


def inferred(panel: pd.DataFrame) -> pd.DataFrame:
    """Class-D sub-signals for every panel row (panel sorted by symbol, date)."""
    need = {"deliv_qty_avg20", "deliv_qty_avg50", "deliv_pct", "updown_vol20", "cmf20", "dc_lower", "rvol20"}
    if panel.empty or not need <= set(panel.columns):
        return pd.DataFrame(index=panel.index)
    parts = [_per_symbol(g) for _, g in panel.groupby("symbol", sort=False)]
    out = pd.concat(parts).reindex(panel.index)
    out["d_score"] = out[list(ACC_SUBS)].sum(axis=1)
    out["x_score"] = out[list(DIST_SUBS)].sum(axis=1)
    return out


# ----------------------------------------------------------------- class A --- #
def deals_classified(eng, start=None, symbol: Optional[str] = None) -> pd.DataFrame:
    stmt = select(db.deals)
    if start is not None:
        stmt = stmt.where(db.deals.c.date >= start)
    if symbol:
        stmt = stmt.where(db.deals.c.symbol == symbol)
    d = db.read_sql(eng, stmt.order_by(db.deals.c.date.desc()))
    if d.empty:
        return d.assign(entity_class=[], method=[], institutional=[], value_cr=[])
    cl = classify(eng, d["client"]).set_index("name")
    d = d.join(cl, on="client")
    d["value_cr"] = d["qty"] * d["price"] / 1e7
    d["evidence_class"] = np.where(d["institutional"], "A", "E")
    return d


def deal_features(eng, panel: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=panel.index, data={"a_inst_net_cr": 0.0, "a_inst_buy": False, "a_inst_sell": False,
                                                "e_other_deal": False})
    d = deals_classified(eng)
    if d.empty or panel.empty:
        return out
    d["date"] = pd.to_datetime(d["date"])
    signed = np.where(d["side"] == "BUY", 1.0, -1.0) * d["value_cr"].fillna(0)
    inst = d[d["institutional"]].assign(net=signed[d["institutional"].to_numpy()])
    agg = inst.groupby(["symbol", "date"])["net"].sum()
    other = set(map(tuple, d.loc[~d["institutional"], ["symbol", "date"]].itertuples(index=False)))
    keys = pd.MultiIndex.from_frame(panel[["symbol", "date"]])
    net = pd.Series(agg.reindex(keys).to_numpy(), index=panel.index).fillna(0.0)
    out["a_inst_net_cr"] = net
    out["a_inst_buy"] = net > 0
    out["a_inst_sell"] = net < 0
    out["e_other_deal"] = [k in other for k in zip(panel["symbol"], panel["date"])]
    return out


# ----------------------------------------------------------------- class B --- #
def ownership_changes(eng, symbol: Optional[str] = None) -> pd.DataFrame:
    """Per symbol × quarter: institutional % (FPI + DII) and its change vs the prior quarter."""
    stmt = select(db.shareholding)
    if symbol:
        stmt = stmt.where(db.shareholding.c.symbol == symbol)
    sh = db.read_sql(eng, stmt)
    if sh.empty:
        return pd.DataFrame(columns=["symbol", "quarter_end", "published_on", "inst_pct", "fpi", "dii",
                                     "promoter", "inst_change_pp", "fpi_change_pp", "dii_change_pp"])
    w = sh.pivot_table(index=["symbol", "quarter_end"], columns="category", values="pct", aggfunc="sum").reset_index()
    pub = sh.groupby(["symbol", "quarter_end"])["published_on"].max().reset_index()
    w = w.merge(pub, on=["symbol", "quarter_end"])
    for c in ("fpi", "dii", "mutual_funds", "insurance", "promoter"):
        if c not in w:
            w[c] = np.nan
    w["dii"] = w["dii"].fillna(w[["mutual_funds", "insurance"]].sum(axis=1, min_count=1))
    w["inst_pct"] = w[["fpi", "dii"]].sum(axis=1, min_count=1)
    w = w.sort_values(["symbol", "quarter_end"])
    g = w.groupby("symbol")
    w["inst_change_pp"] = g["inst_pct"].diff()
    w["fpi_change_pp"] = g["fpi"].diff()
    w["dii_change_pp"] = g["dii"].diff()
    return w[["symbol", "quarter_end", "published_on", "inst_pct", "fpi", "dii", "promoter",
              "inst_change_pp", "fpi_change_pp", "dii_change_pp"]].reset_index(drop=True)


def mf_changes(eng, symbol: Optional[str] = None) -> pd.DataFrame:
    stmt = select(db.mf_holdings)
    if symbol:
        stmt = stmt.where(db.mf_holdings.c.symbol == symbol)
    m = db.read_sql(eng, stmt)
    if m.empty:
        return pd.DataFrame(columns=["symbol", "month_end", "published_on", "shares", "schemes", "mf_change_pct"])
    a = m.groupby(["symbol", "month_end"]).agg(shares=("shares", "sum"), schemes=("scheme", "nunique"),
                                               published_on=("published_on", "max")).reset_index()
    a = a.sort_values(["symbol", "month_end"])
    a["mf_change_pct"] = a.groupby("symbol")["shares"].pct_change() * 100
    return a.reset_index(drop=True)


def _event_on_publication(panel: pd.DataFrame, ev: pd.DataFrame, value_col: str) -> pd.Series:
    """Place each event on the first panel session on/after its publication date."""
    s = pd.Series(np.nan, index=panel.index)
    if ev.empty:
        return s
    ev = ev.dropna(subset=[value_col]).copy()
    ev["published_on"] = pd.to_datetime(ev["published_on"])
    for sym, g in panel.groupby("symbol", sort=False):
        e = ev[ev["symbol"] == sym]
        if e.empty:
            continue
        dates = g["date"].to_numpy()
        pos = np.searchsorted(dates, e["published_on"].to_numpy())
        for p, v in zip(pos, e[value_col].to_numpy()):
            if p < len(dates):
                s.at[g.index[p]] = v
    return s


def ownership_features(eng, panel: pd.DataFrame, min_change_pp: float = 0.5, min_mf_change_pct: float = 5.0) -> pd.DataFrame:
    out = pd.DataFrame(index=panel.index)
    oc = ownership_changes(eng)
    out["b_inst_change_pp"] = _event_on_publication(panel, oc, "inst_change_pp")
    out["b_inst_up"] = out["b_inst_change_pp"] >= min_change_pp
    out["b_inst_down"] = out["b_inst_change_pp"] <= -min_change_pp
    mc = mf_changes(eng)
    out["b_mf_change_pct"] = _event_on_publication(panel, mc, "mf_change_pct")
    out["b_mf_add"] = out["b_mf_change_pct"] >= min_mf_change_pct
    out["b_mf_cut"] = out["b_mf_change_pct"] <= -min_mf_change_pct
    # latest known value (carried forward) for scoring
    for col in ("b_inst_change_pp", "b_mf_change_pct"):
        out[col + "_last"] = out[col].groupby(panel["symbol"]).ffill(limit=130)
    return out


# ----------------------------------------------------------------- class C --- #
def market_flows(eng, days: int = 120) -> pd.DataFrame:
    f = db.read_sql(eng, select(db.fii_dii_daily).order_by(db.fii_dii_daily.c.date))
    if f.empty:
        return f
    w = f.pivot_table(index="date", columns="category", values="net_value").sort_index()
    for c in ("FII", "DII"):
        if c not in w:
            w[c] = np.nan
        w[f"{c}_5d"] = w[c].rolling(5, min_periods=1).sum()
        w[f"{c}_20d"] = w[c].rolling(20, min_periods=1).sum()
    return w.tail(days).reset_index()


# --------------------------------------------------------------- combined --- #
def attach(eng, panel: pd.DataFrame) -> pd.DataFrame:
    """Panel + all stock-level evidence columns (A, B, D, E)."""
    if panel.empty:
        return panel
    return pd.concat([panel, inferred(panel), deal_features(eng, panel), ownership_features(eng, panel)], axis=1)


COMPONENT_SIGNALS = {
    "accumulation": {"A": "inst_deal_buy", "B": "ownership_increase", "D": "possible_accumulation"},
    "distribution": {"A": "inst_deal_sell", "B": "ownership_decrease", "D": "possible_distribution"},
}
COMPONENT_WEIGHTS = {"A": 0.30, "B": 0.25, "D": 0.30, "F": 0.15}  # F = derivatives (Phase 3)


def passed_signals(eng) -> set[str]:
    latest = (select(db.signal_tests.c.signal_name, func.max(db.signal_tests.c.id).label("id"))
              .group_by(db.signal_tests.c.signal_name).subquery())
    with eng.connect() as c:
        rows = c.execute(select(db.signal_tests.c.signal_name, db.signal_tests.c.passed)
                         .join(latest, db.signal_tests.c.id == latest.c.id)).all()
    return {r.signal_name for r in rows if r.passed}


def _pct_rank(s: pd.Series) -> pd.Series:
    return s.rank(pct=True) * 100


def scores(snap: pd.DataFrame, panel: pd.DataFrame, passed: set[str], require_pass: bool = True) -> pd.DataFrame:
    """Accumulation and distribution scores for the latest snapshot.

    Components (0–100): A = 20-session net institutional deal value relative to traded value
    (cross-sectional percentile; 0 deals → 50 = no evidence either way, counted as data present
    only when deal files exist); B = latest institutional ownership change (percentile);
    D = share of class-D sub-signals firing. F (derivatives) arrives in Phase 3, so it is always
    missing and lowers confidence. A component is *weighted* only if its signal passed its test
    (unless require_pass is False); confidence = sum of weights of components that have data.
    """
    out = snap[["symbol"]].copy()
    last = panel["date"].max()
    recent = panel[panel["date"] > last - pd.Timedelta(days=30)]
    a_raw = recent.groupby("symbol")["a_inst_net_cr"].sum() if "a_inst_net_cr" in recent else pd.Series(dtype=float)
    value20 = snap.set_index("symbol")["avg_value_cr"] * 20
    a_rel = (a_raw.reindex(snap["symbol"]) / value20.reindex(snap["symbol"])).fillna(0)
    have_deals = "a_inst_net_cr" in panel and bool((panel["a_inst_net_cr"] != 0).any())
    sym = snap["symbol"].to_numpy()
    comp = {
        "accumulation": {
            "A": _pct_rank(a_rel).to_numpy() if have_deals else np.full(len(sym), np.nan),
            "B": _pct_rank(snap["b_inst_change_pp_last"]).to_numpy() if "b_inst_change_pp_last" in snap else np.full(len(sym), np.nan),
            "D": (snap["d_score"] / len(ACC_SUBS) * 100).to_numpy() if "d_score" in snap else np.full(len(sym), np.nan),
        },
        "distribution": {
            "A": _pct_rank(-a_rel).to_numpy() if have_deals else np.full(len(sym), np.nan),
            "B": _pct_rank(-snap["b_inst_change_pp_last"]).to_numpy() if "b_inst_change_pp_last" in snap else np.full(len(sym), np.nan),
            "D": (snap["x_score"] / len(DIST_SUBS) * 100).to_numpy() if "x_score" in snap else np.full(len(sym), np.nan),
        },
    }
    for side, parts in comp.items():
        num = np.zeros(len(sym))
        den = np.zeros(len(sym))
        conf = np.zeros(len(sym))
        for k, vals in parts.items():
            have = ~np.isnan(vals)
            w = COMPONENT_WEIGHTS[k]
            conf += np.where(have, w, 0.0)
            active = (COMPONENT_SIGNALS[side][k] in passed) or not require_pass
            out[f"{side}_{k}"] = vals
            if active:
                num += np.where(have, w * np.nan_to_num(vals), 0.0)
                den += np.where(have, w, 0.0)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[f"{side}_score"] = np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)
        out[f"{side}_confidence"] = conf
    return out


def stock_evidence(eng, row: pd.Series) -> list[dict]:
    """Human-readable evidence items for one snapshot row (latest date)."""
    items = []
    for k, desc in ACC_SUBS.items():
        if row.get(k):
            items.append({"class": "D", "direction": "bullish", "label": "Possible accumulation", "detail": desc})
    for k, desc in DIST_SUBS.items():
        if row.get(k):
            items.append({"class": "D", "direction": "bearish", "label": "Possible distribution", "detail": desc})
    if row.get("e_volume_spike"):
        items.append({"class": "E", "direction": "neutral", "label": "Unclassified volume spike",
                      "detail": "Volume ≥3× average with <1% price change — direction unknown"})
    v = row.get("b_inst_change_pp_last")
    if v is not None and not pd.isna(v):
        items.append({"class": "B", "direction": "bullish" if v > 0 else "bearish" if v < 0 else "neutral",
                      "label": "Institutional ownership change", "detail": f"FPI+DII {v:+.2f} pp vs prior quarter (latest published)"})
    v = row.get("b_mf_change_pct_last")
    if v is not None and not pd.isna(v):
        items.append({"class": "B", "direction": "bullish" if v > 0 else "bearish" if v < 0 else "neutral",
                      "label": "Mutual-fund holdings change", "detail": f"MF shares {v:+.1f}% vs prior month (latest published)"})
    return items


def store(eng, panel: pd.DataFrame, days: int = 5) -> int:
    """Write every fired signal (with its evidence) for the last ``days`` sessions to `signals`."""
    from .research.signals import SIGNALS, EVIDENCE_CLASS
    dates = sorted(panel["date"].unique())[-days:]
    sub = panel[panel["date"].isin(dates)]
    rows = []
    for name, (fn, direction, _) in SIGNALS.items():
        fired = sub[fn(panel).reindex(sub.index).fillna(False).astype(bool)]
        cls = EVIDENCE_CLASS.get(name, "D")
        for _, r in fired.iterrows():
            ev = {k: (None if pd.isna(r[k]) else (bool(r[k]) if isinstance(r[k], (bool, np.bool_)) else float(r[k])))
                  for k in list(ACC_SUBS) + list(DIST_SUBS) + ["rs_rank", "rvol20", "deliv_pct", "a_inst_net_cr",
                                                               "b_inst_change_pp_last", "b_mf_change_pct_last"]
                  if k in r.index}
            rows.append({"date": pd.Timestamp(r["date"]).date(), "symbol": r["symbol"], "module": "research",
                         "name": name, "direction": direction, "strength": None, "evidence_class": cls,
                         "evidence": json.dumps(ev), "rule_version": RULE_VERSION})
    df = pd.DataFrame(rows)
    db.replace_rows(eng, db.signals, df, [db.signals.c.date.in_([pd.Timestamp(d).date() for d in dates]),
                                          db.signals.c.rule_version == RULE_VERSION])
    return len(df)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", action="store_true")
    ap.add_argument("--days", type=int, default=5)
    a = ap.parse_args(argv)
    from .panel import build_panel
    eng = db.engine()
    panel = attach(eng, build_panel(eng, load()))
    if panel.empty:
        print("No price data.")
        return 1
    if a.store:
        print("Signals stored:", store(eng, panel, a.days))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
