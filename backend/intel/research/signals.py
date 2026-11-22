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


def _col(p: pd.DataFrame, name: str) -> pd.Series:
    """Evidence columns exist only after ``intel.evidence.attach``; missing → never fires."""
    return p[name].fillna(False).astype(bool) if name in p else pd.Series(False, index=p.index)


# ---- Phase 2: accumulation / distribution evidence ---------------------------------- #
def possible_accumulation(p: pd.DataFrame) -> pd.Series:
    """Class D: ≥3 of 5 inferred-accumulation sub-signals, price above its 50-DMA."""
    if "d_score" not in p:
        return pd.Series(False, index=p.index)
    return p["liquid"].fillna(False) & (p["d_score"] >= 3) & (p["close"] > p["dma50"])


def possible_distribution(p: pd.DataFrame) -> pd.Series:
    if "x_score" not in p:
        return pd.Series(False, index=p.index)
    return p["liquid"].fillna(False) & (p["x_score"] >= 3) & (p["close"] < p["dma50"])


def delivery_spike_up(p: pd.DataFrame) -> pd.Series:
    """Class D: delivery qty ≥2.5× its 20D average on an up day of ≥2% with ≥1.5× volume."""
    if "deliv_qty_avg20" not in p:
        return pd.Series(False, index=p.index)
    prev_avg = p.groupby("symbol")["deliv_qty_avg20"].shift(1)
    return (p["liquid"].fillna(False) & (p["deliv_qty"] >= 2.5 * prev_avg) & (p["ret_1d"] >= 2)
            & (p["rvol20"] >= 1.5))


def inst_deal_buy(p: pd.DataFrame) -> pd.Series:
    """Class A: net buying by classified institutions in that day's bulk/block deals."""
    return _col(p, "a_inst_buy")


def inst_deal_sell(p: pd.DataFrame) -> pd.Series:
    return _col(p, "a_inst_sell")


def ownership_increase(p: pd.DataFrame) -> pd.Series:
    """Class B: FPI+DII holding up ≥0.5 pp QoQ, on the session the filing became public."""
    return _col(p, "b_inst_up")


def ownership_decrease(p: pd.DataFrame) -> pd.Series:
    return _col(p, "b_inst_down")


def mf_adds(p: pd.DataFrame) -> pd.Series:
    """Class B: mutual funds' total shares up ≥5% month on month, from the publication date."""
    return _col(p, "b_mf_add")


# ---- Phase 3: derivatives positioning (class F) --------------------------------------- #
def long_buildup(p: pd.DataFrame) -> pd.Series:
    """Futures long build-up: 5-session price up and total futures OI up ≥5% (outside expiry week)."""
    return p["liquid"].fillna(False) & (p["f_buildup"] == "long_buildup") if "f_buildup" in p else pd.Series(False, index=p.index)


def short_buildup(p: pd.DataFrame) -> pd.Series:
    return p["liquid"].fillna(False) & (p["f_buildup"] == "short_buildup") if "f_buildup" in p else pd.Series(False, index=p.index)


def short_covering(p: pd.DataFrame) -> pd.Series:
    """Price up while futures OI falls ≥5% — shorts closing; often short-lived."""
    return p["liquid"].fillna(False) & (p["f_buildup"] == "short_covering") if "f_buildup" in p else pd.Series(False, index=p.index)


SIGNALS: dict[str, tuple[Signal, str, str]] = {
    # name: (function, direction, description)
    "rs_leaders": (rs_leaders, "bullish", "Top-10% 6-month relative strength, RS rising"),
    "rs_laggards": (rs_laggards, "bearish", "Bottom-10% 6-month relative strength, RS falling"),
    "breakout_volume": (breakout_volume, "bullish", "20-day high breakout on 1.5× volume, above 200-DMA"),
    "breakdown_volume": (breakdown_volume, "bearish", "20-day low breakdown on 1.5× volume, below 200-DMA"),
    "near_52w_high_strong": (near_52w_high_strong, "bullish", "Within 5% of 52-week high and top-20% RS"),
    "possible_accumulation": (possible_accumulation, "bullish", "Class D: ≥3 of 5 inferred-accumulation clues, above 50-DMA"),
    "possible_distribution": (possible_distribution, "bearish", "Class D: ≥3 of 5 inferred-distribution clues, below 50-DMA"),
    "delivery_spike_up": (delivery_spike_up, "bullish", "Class D: delivery ≥2.5× average on a ≥2% up day with 1.5× volume"),
    "inst_deal_buy": (inst_deal_buy, "bullish", "Class A: net institutional buying in bulk/block deals"),
    "inst_deal_sell": (inst_deal_sell, "bearish", "Class A: net institutional selling in bulk/block deals"),
    "ownership_increase": (ownership_increase, "bullish", "Class B: FPI+DII holding up ≥0.5 pp QoQ (from filing date)"),
    "ownership_decrease": (ownership_decrease, "bearish", "Class B: FPI+DII holding down ≥0.5 pp QoQ (from filing date)"),
    "mf_adds": (mf_adds, "bullish", "Class B: mutual-fund shares up ≥5% MoM (from publication date)"),
    "long_buildup": (long_buildup, "bullish", "Class F: futures long build-up (price ↑, OI ↑ ≥5% over 5 sessions)"),
    "short_buildup": (short_buildup, "bearish", "Class F: futures short build-up (price ↓, OI ↑ ≥5% over 5 sessions)"),
    "short_covering": (short_covering, "bullish", "Class F: short covering (price ↑, OI ↓ ≥5% over 5 sessions)"),
}

# Evidence class of each signal (docs/DESIGN.md §8); price/RS signals are technical (T).
EVIDENCE_CLASS = {"rs_leaders": "T", "rs_laggards": "T", "breakout_volume": "T", "breakdown_volume": "T",
                  "near_52w_high_strong": "T", "possible_accumulation": "D", "possible_distribution": "D",
                  "delivery_spike_up": "D", "inst_deal_buy": "A", "inst_deal_sell": "A",
                  "ownership_increase": "B", "ownership_decrease": "B", "mf_adds": "B",
                  "long_buildup": "F", "short_buildup": "F", "short_covering": "F"}
