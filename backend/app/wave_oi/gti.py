"""Ghost Trade Indicator (GTI) - trend / timing line.

There is no public standard definition of "GTI" (the TradingView scripts by
that name are closed source), so this is our own transparent version:

* an ATR trailing line on smoothed mid-price (SuperTrend-style) gives the
  trend direction and a natural trailing stop, and
* the "ghost" part - OI building while price stands still - lives in
  ``oi.ghost_activity`` and is combined with this line in ``strategy``.

A BUY flip happens when the close breaks above the line, SELL when it breaks
below.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import atr


def compute_gti(df: pd.DataFrame, atr_len: int = 10, mult: float = 2.5, smooth: int = 3) -> pd.DataFrame:
    mid = (df["high"] + df["low"]) / 2.0
    if smooth > 1:
        mid = mid.ewm(span=smooth, adjust=False).mean()
    a = atr(df, atr_len).to_numpy(float)
    src = mid.to_numpy(float)
    close = df["close"].to_numpy(float)
    upper = src + mult * a
    lower = src - mult * a

    n = len(df)
    line = np.full(n, np.nan)
    direction = np.zeros(n, dtype=int)
    if n == 0:
        return pd.DataFrame({"gti_line": line, "gti_dir": direction, "gti_flip": direction}, index=df.index)

    final_up, final_lo, d = upper[0], lower[0], 1
    for i in range(n):
        if i > 0:
            final_up = upper[i] if (upper[i] < final_up or close[i - 1] > final_up) else final_up
            final_lo = lower[i] if (lower[i] > final_lo or close[i - 1] < final_lo) else final_lo
            if d == 1 and close[i] < final_lo:
                d = -1
            elif d == -1 and close[i] > final_up:
                d = 1
        direction[i] = d
        line[i] = final_lo if d == 1 else final_up

    flip = np.zeros(n, dtype=int)
    flip[1:] = np.where(direction[1:] != direction[:-1], direction[1:], 0)
    return pd.DataFrame({"gti_line": line, "gti_dir": direction, "gti_flip": flip}, index=df.index)
