"""Small, causal building blocks shared by the wave / OI / GTI modules."""
from __future__ import annotations

import pandas as pd


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """Wilder's Average True Range (RMA of true range). Uses only past data."""
    return true_range(df).ewm(alpha=1.0 / length, adjust=False).mean()
