"""Market regime, breadth and sector strength (Phase 6). All market-wide (class C)."""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from .panel import load_index


def breadth(panel: pd.DataFrame, days: int = 250) -> pd.DataFrame:
    """Per date, over liquid stocks: % above 50/200-DMA, advancers/decliners, new 52W highs/lows."""
    if panel.empty:
        return pd.DataFrame()
    p = panel[panel["liquid"].fillna(False).astype(bool)]
    if p.empty:
        return pd.DataFrame()
    g = p.groupby("date")
    hi = p["dist_52w_high_pct"] >= -0.01 if "dist_52w_high_pct" in p else pd.Series(False, index=p.index)
    low52 = p.groupby("symbol")["low"].transform(lambda s: s.rolling(252, min_periods=200).min())
    out = pd.DataFrame({
        "stocks": g.size(),
        "pct_above_50dma": g.apply(lambda x: (x["close"] > x["dma50"]).mean() * 100, include_groups=False),
        "pct_above_200dma": g.apply(lambda x: (x["close"] > x["dma200"]).mean() * 100, include_groups=False),
        "advancers": g["ret_1d"].apply(lambda s: int((s > 0).sum())),
        "decliners": g["ret_1d"].apply(lambda s: int((s < 0).sum())),
        "new_highs": hi.groupby(p["date"]).sum(),
        "new_lows": (p["low"] <= low52).groupby(p["date"]).sum(),
    })
    out["ad_ratio"] = out["advancers"] / out["decliners"].replace(0, np.nan)
    return out.tail(days).reset_index()


def regime(eng, panel: pd.DataFrame, index_name: str = "Nifty 50") -> dict:
    """Trend of the index + breadth → uptrend / downtrend / mixed, with the reasons."""
    idx = load_index(eng, index_name)
    br = breadth(panel)
    if idx.empty:
        return {"label": "unknown", "reasons": [f"No {index_name} data"], "index": index_name}
    c = idx
    dma50, dma200 = c.rolling(50).mean(), c.rolling(200).mean()
    last = c.index[-1]
    above200 = bool(c.iloc[-1] > dma200.iloc[-1]) if pd.notna(dma200.iloc[-1]) else None
    slope50 = float(dma50.iloc[-1] / dma50.iloc[-21] - 1) * 100 if len(dma50.dropna()) > 21 else None
    b200 = float(br["pct_above_200dma"].iloc[-1]) if len(br) else None
    reasons, score = [], 0
    if above200 is not None:
        reasons.append(f"{index_name} {'above' if above200 else 'below'} its 200-DMA")
        score += 1 if above200 else -1
    if slope50 is not None:
        reasons.append(f"50-DMA {'rising' if slope50 > 0 else 'falling'} ({slope50:+.1f}% over 20 sessions)")
        score += 1 if slope50 > 0 else -1
    if b200 is not None:
        reasons.append(f"{b200:.0f}% of liquid stocks above their 200-DMA")
        score += 1 if b200 > 55 else -1 if b200 < 45 else 0
    label = "uptrend" if score >= 2 else "downtrend" if score <= -2 else "mixed"
    return {"label": label, "score": score, "reasons": reasons, "index": index_name, "as_of": str(last.date()),
            "index_close": float(c.iloc[-1]), "dma200": None if pd.isna(dma200.iloc[-1]) else float(dma200.iloc[-1]),
            "pct_above_200dma": b200}


def sector_strength(panel: pd.DataFrame) -> pd.DataFrame:
    """Per date × industry: median RS (3M/6M), % above 200-DMA, count. Adds a sector percentile."""
    p = panel[panel["liquid"].fillna(False).astype(bool) & panel["industry"].notna()]
    if p.empty or "rs_6m" not in p:
        return pd.DataFrame(columns=["date", "industry", "sector_rs_6m", "sector_rs_3m", "sector_pct_above_200dma",
                                     "sector_stocks", "sector_rank_pct"])
    g = p.groupby(["date", "industry"])
    s = pd.DataFrame({
        "sector_rs_6m": g["rs_6m"].median(), "sector_rs_3m": g["rs_3m"].median() if "rs_3m" in p else np.nan,
        "sector_pct_above_200dma": g.apply(lambda x: (x["close"] > x["dma200"]).mean() * 100, include_groups=False),
        "sector_stocks": g.size()}).reset_index()
    s["sector_rank_pct"] = s.groupby("date")["sector_rs_6m"].rank(pct=True) * 100
    return s


def sector_table(panel: pd.DataFrame) -> pd.DataFrame:
    s = sector_strength(panel)
    if s.empty:
        return s
    last = s[s["date"] == s["date"].max()].copy()
    prev_date = sorted(s["date"].unique())[-21] if s["date"].nunique() > 21 else None
    if prev_date is not None:
        prev = s[s["date"] == prev_date].set_index("industry")["sector_rank_pct"]
        last["rank_change_20d"] = last["sector_rank_pct"] - last["industry"].map(prev)
    return last.sort_values("sector_rs_6m", ascending=False).reset_index(drop=True)


def attach_sector(panel: pd.DataFrame, sectors: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    s = sector_strength(panel) if sectors is None else sectors
    cols = ["sector_rs_6m", "sector_rank_pct", "sector_pct_above_200dma"]
    if s.empty:
        return pd.DataFrame(index=panel.index, columns=cols)
    m = panel[["date", "industry"]].reset_index().merge(s[["date", "industry"] + cols], on=["date", "industry"],
                                                        how="left").set_index("index").reindex(panel.index)
    return m[cols]
