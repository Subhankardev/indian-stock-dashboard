"""Open-interest analytics.

* ``oi_regime`` - the classic price/OI quadrant over a lookback:

  ====== ====== ===================  ==========================
  Price  OI     Regime               Reading
  ====== ====== ===================  ==========================
  up     up     ``new_longs``        fresh buying, strong trend
  up     down   ``short_covering``   rally on exits, weaker
  down   up     ``new_shorts``       fresh selling, strong trend
  down   down   ``long_liquidation`` decline on exits, weaker
  ====== ====== ===================  ==========================

* ``ghost_activity`` - "ghost trades": bars where OI jumps abnormally (z-score)
  while price barely moves. Positions are being built quietly; the close
  location inside the bar's range hints at which side is absorbing.

* ``oi_divergence`` - price at an N-bar extreme while OI has shrunk over the
  same window (the move is running on exits, not new money).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import atr

REGIMES = ("new_longs", "short_covering", "new_shorts", "long_liquidation", "neutral")


def has_oi(df: pd.DataFrame) -> bool:
    return "open_interest" in df.columns and df["open_interest"].notna().sum() > 1


def oi_regime(df: pd.DataFrame, lookback: int = 5, min_oi_change: float = 0.01) -> pd.DataFrame:
    price_chg = df["close"].pct_change(lookback)
    oi_chg = df["open_interest"].pct_change(lookback, fill_method=None)
    up, down = price_chg > 0, price_chg < 0
    oi_up, oi_down = oi_chg >= min_oi_change, oi_chg <= -min_oi_change
    regime = np.select(
        [up & oi_up, up & oi_down, down & oi_up, down & oi_down],
        ["new_longs", "short_covering", "new_shorts", "long_liquidation"],
        default="neutral",
    )
    return pd.DataFrame(
        {"oi_regime": regime, "oi_change": oi_chg, "price_change": price_chg},
        index=df.index,
    )


def ghost_activity(
    df: pd.DataFrame,
    z_window: int = 50,
    z_threshold: float = 2.0,
    max_body_atr: float = 0.5,
    atr_len: int = 14,
) -> pd.DataFrame:
    oi_delta = df["open_interest"].pct_change(fill_method=None)
    mean = oi_delta.rolling(z_window, min_periods=max(10, z_window // 3)).mean()
    std = oi_delta.rolling(z_window, min_periods=max(10, z_window // 3)).std()
    z = (oi_delta - mean) / std.replace(0, np.nan)

    body = (df["close"] - df["open"]).abs() / atr(df, atr_len)
    ghost = (z.abs() >= z_threshold) & (body <= max_body_atr)

    rng = (df["high"] - df["low"]).replace(0, np.nan)
    clv = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / rng  # -1..1
    # Only OI *build-ups* say who is positioning; OI drops are just exits.
    bias = np.where(ghost & (z > 0), np.sign(clv.fillna(0)), 0).astype(int)
    return pd.DataFrame({"oi_z": z, "ghost": ghost.fillna(False), "ghost_bias": bias}, index=df.index)


def oi_divergence(df: pd.DataFrame, lookback: int = 20) -> pd.Series:
    """-1 bearish (new high on falling OI), +1 bullish (new low on falling OI)."""
    close = df["close"]
    oi_chg = df["open_interest"].pct_change(lookback, fill_method=None)
    at_high = close >= close.rolling(lookback).max()
    at_low = close <= close.rolling(lookback).min()
    out = np.where(at_high & (oi_chg < 0), -1, np.where(at_low & (oi_chg < 0), 1, 0))
    return pd.Series(out, index=df.index, name="oi_divergence")
