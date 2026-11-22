"""Signal definitions. Each returns a boolean Series aligned to the panel, using only data
available at the close of each row's date. Phases add their signals here; every one is
tested with ``python -m intel.research`` before the scoring engine may use it.
"""
from __future__ import annotations

from typing import Callable

import pandas as pd

Signal = Callable[[pd.DataFrame], pd.Series]


def rs_leaders(p: pd.DataFrame) -> pd.Series:
    """Top-decile 6-month RS in the liquid universe, with RS still improving."""
    return p["liquid"].fillna(False) & (p["rs_rank"] >= 90) & (p["rs_mom_20"] > 0)


def rs_laggards(p: pd.DataFrame) -> pd.Series:
    return p["liquid"].fillna(False) & (p["rs_rank"] <= 10) & (p["rs_mom_20"] < 0)


def breakout_volume(p: pd.DataFrame) -> pd.Series:
    """Close above the prior 20-session high on ≥1.5× average volume, above the 200-DMA."""
    return (p["liquid"].fillna(False) & (p["close"] > p["dc_upper"]) & (p["rvol20"] >= 1.5)
            & (p["close"] > p["dma200"]))


def breakdown_volume(p: pd.DataFrame) -> pd.Series:
    return (p["liquid"].fillna(False) & (p["close"] < p["dc_lower"]) & (p["rvol20"] >= 1.5)
            & (p["close"] < p["dma200"]))


def near_52w_high_strong(p: pd.DataFrame) -> pd.Series:
    """Within 5% of the 52-week high with top-quintile RS (a classic momentum screen)."""
    return p["liquid"].fillna(False) & (p["dist_52w_high_pct"] >= -5) & (p["rs_rank"] >= 80)


SIGNALS: dict[str, tuple[Signal, str, str]] = {
    # name: (function, direction, description)
    "rs_leaders": (rs_leaders, "bullish", "Top-10% 6-month relative strength, RS rising"),
    "rs_laggards": (rs_laggards, "bearish", "Bottom-10% 6-month relative strength, RS falling"),
    "breakout_volume": (breakout_volume, "bullish", "20-day high breakout on 1.5× volume, above 200-DMA"),
    "breakdown_volume": (breakdown_volume, "bearish", "20-day low breakdown on 1.5× volume, below 200-DMA"),
    "near_52w_high_strong": (near_52w_high_strong, "bullish", "Within 5% of 52-week high and top-20% RS"),
}
