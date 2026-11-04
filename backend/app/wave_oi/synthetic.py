"""Deterministic synthetic daily bars with Elliott-like legs and matching OI.

Used for tests, demos and offline development (NSE blocks many networks).
Impulse legs (1, 3, 5) build open interest; corrective legs (2, 4, A, B, C)
unwind it - the textbook behaviour the strategy looks for.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# (relative size, trend-direction leg?) for one 5-3 cycle
_CYCLE = [
    (1.0, True), (-0.6, False), (1.65, True), (-0.38, False), (1.0, True),   # 1-5
    (-0.9, False), (0.45, False), (-0.85, False),                            # A-B-C
]


def make_synthetic(bars: int = 500, seed: int = 7, start_price: float = 1000.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes, oi = [], []
    price, open_int = start_price, 1_000_000.0
    while len(closes) < bars:
        trend = 1 if rng.random() < 0.6 else -1
        unit = rng.uniform(0.06, 0.12)          # wave-1 size as a fraction of price
        for size, impulse in _CYCLE:
            length = int(rng.integers(6, 16))
            leg_ret = trend * size * unit
            steps = np.full(length, leg_ret / length) + rng.normal(0, 0.006, length)
            oi_step = (0.012 if impulse else -0.006) + rng.normal(0, 0.004, length)
            for s, d_oi in zip(steps, oi_step):
                price *= float(np.exp(s))
                open_int *= float(np.exp(d_oi))
                closes.append(price)
                oi.append(open_int)
    close = np.array(closes[:bars])
    open_ = np.concatenate([[start_price], close[:-1]]) * np.exp(rng.normal(0, 0.002, bars))
    wick = np.abs(rng.normal(0, 0.006, bars)) * close
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.006, bars)) * close
    idx = pd.bdate_range("2022-01-03", periods=bars, name="date")
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.integers(100_000, 1_000_000, bars).astype(float),
            "open_interest": np.array(oi[:bars]).round(),
        },
        index=idx,
    )
