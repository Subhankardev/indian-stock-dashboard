"""Relative strength vs benchmark indices and across the universe.

Relative strength (RS) compares a stock's performance with an index or with peers.
It is NOT RSI (a single-stock oscillator, see indicators.rsi).
"""
from __future__ import annotations

import pandas as pd


def rs_vs_benchmark(close: pd.Series, bench: pd.Series, windows: dict[str, int]) -> pd.DataFrame:
    """Per-date RS measures for one stock.

    - ``rs_ratio``: close / benchmark, rebased to 100 at the first common date
    - ``rs_<w>``: stock return minus benchmark return over w sessions (percentage points)
    - ``rs_mom_20``: 20-session change of the RS ratio (%) — is RS improving?
    """
    df = pd.concat({"c": close, "b": bench}, axis=1).dropna()
    out = pd.DataFrame(index=df.index)
    ratio = df["c"] / df["b"]
    out["rs_ratio"] = ratio / ratio.iloc[0] * 100 if len(ratio) else ratio
    for name, n in windows.items():
        out[f"rs_{name}"] = (df["c"].pct_change(n) - df["b"].pct_change(n)) * 100
    out["rs_mom_20"] = ratio.pct_change(20) * 100
    return out


def cross_sectional_rank(panel: pd.DataFrame, value_col: str, by: str = "date") -> pd.Series:
    """Percentile rank (0-100) of ``value_col`` among all symbols on each date."""
    return panel.groupby(by)[value_col].rank(pct=True) * 100


def quadrant(stock_rs: float, market_trend_up: bool) -> str:
    strong = stock_rs > 0
    if strong and market_trend_up:
        return "strong stock, strong market"
    if strong:
        return "strong stock, weak market"
    if market_trend_up:
        return "weak stock, strong market"
    return "weak stock, weak market"


def sector_relative(panel: pd.DataFrame, value_col: str, sector_col: str = "industry") -> pd.Series:
    """Stock value minus its sector's median on the same date (sector-relative strength)."""
    med = panel.groupby(["date", sector_col])[value_col].transform("median")
    return panel[value_col] - med
