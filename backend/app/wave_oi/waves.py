"""Elliott-style wave detection.

Pipeline:
1. ``zigzag`` turns OHLC bars into alternating swing pivots. A pivot is only
   *confirmed* once price has reversed from it by ``atr_mult * ATR``; the bar
   index where that happens is stored as ``confirmed_at`` so nothing downstream
   can peek into the future.
2. ``setup_from_pivots`` looks at the most recent confirmed pivots and checks
   them against the Elliott impulse rules:

   * wave 2 never retraces more than 100% of wave 1
   * wave 3 is never the shortest of waves 1, 3 and 5
   * wave 4 does not overlap wave 1's price territory

   and reports the actionable setup (riding wave 3, riding wave 5, or the
   A-B-C correction after a finished impulse) with a Fibonacci target and the
   price that invalidates the count.
3. ``wave_context`` replays that bar by bar, giving a causal per-bar label.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
import pandas as pd

from .indicators import atr


@dataclass(frozen=True)
class Pivot:
    idx: int            # bar where the swing extreme happened
    price: float
    kind: str           # "H" (swing high) or "L" (swing low)
    confirmed_at: int   # first bar where the swing was known to be complete


@dataclass(frozen=True)
class WaveConfig:
    w2_min: float = 0.236        # wave 2 retracement of wave 1
    w2_max: float = 0.886
    w4_min: float = 0.146        # wave 4 retracement of wave 3
    w4_max: float = 0.618
    w3_min_ratio: float = 0.618  # wave 3 length vs wave 1 (filters weak counts)
    w3_target: float = 1.618     # wave 3 projection off the wave 2 low
    abc_retrace: float = 0.382   # expected correction of the whole impulse


@dataclass(frozen=True)
class WaveSetup:
    label: str          # "W3", "W5" or "ABC"
    direction: int      # +1 expect up, -1 expect down
    target: float
    invalidation: float  # price that proves the wave count wrong
    stop: float          # protective stop: just beyond the latest pivot
    score: float         # 0..1, how textbook the Fibonacci ratios are
    pivot_idx: tuple


# --------------------------------------------------------------------------- #
# Swing pivots
# --------------------------------------------------------------------------- #
def zigzag(
    df: pd.DataFrame,
    atr_len: int = 14,
    atr_mult: float = 2.5,
    min_pct: float = 0.0,
) -> list[Pivot]:
    """Alternating swing highs/lows with a volatility-scaled reversal threshold."""
    n = len(df)
    if n < 2:
        return []
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    threshold = np.maximum(atr(df, atr_len).to_numpy(float) * atr_mult, close * min_pct)

    pivots: list[Pivot] = []
    direction = 0
    hi, hi_i = high[0], 0
    lo, lo_i = low[0], 0
    for i in range(1, n):
        th = threshold[i]
        if direction == 0:
            if high[i] > hi:
                hi, hi_i = high[i], i
            if low[i] < lo:
                lo, lo_i = low[i], i
            if hi_i == i and lo_i < i and hi - lo >= th:
                pivots.append(Pivot(lo_i, float(lo), "L", i))
                direction = 1
            elif lo_i == i and hi_i < i and hi - lo >= th:
                pivots.append(Pivot(hi_i, float(hi), "H", i))
                direction = -1
        elif direction == 1:
            if high[i] > hi:
                hi, hi_i = high[i], i
            elif hi - low[i] >= th:
                pivots.append(Pivot(hi_i, float(hi), "H", i))
                direction = -1
                lo, lo_i = low[i], i
        else:
            if low[i] < lo:
                lo, lo_i = low[i], i
            elif high[i] - lo >= th:
                pivots.append(Pivot(lo_i, float(lo), "L", i))
                direction = 1
                hi, hi_i = high[i], i
    return pivots


# --------------------------------------------------------------------------- #
# Elliott rules
# --------------------------------------------------------------------------- #
def _normalise(pivots: Sequence[Pivot]) -> tuple[int, list[float]]:
    """Flip a down-sequence so every rule can be written for an up-impulse."""
    d = 1 if pivots[0].kind == "L" else -1
    return d, [d * p.price for p in pivots]


def _closeness(value: float, target: float, tol: float = 0.35) -> float:
    return math.exp(-abs(value - target) / (tol * target))


def _waves_1_2(x: list[float], cfg: WaveConfig) -> Optional[float]:
    w1 = x[1] - x[0]
    if w1 <= 0:
        return None
    r2 = (x[1] - x[2]) / w1
    if not (cfg.w2_min <= r2 <= cfg.w2_max):
        return None
    return r2


def _waves_3_4(x: list[float], cfg: WaveConfig) -> Optional[tuple[float, float]]:
    w1 = x[1] - x[0]
    w3 = x[3] - x[2]
    if w3 <= 0 or x[3] <= x[1] or w3 < cfg.w3_min_ratio * w1:
        return None
    if x[4] <= x[1]:  # wave 4 overlaps wave 1
        return None
    r4 = (x[3] - x[4]) / w3
    if not (cfg.w4_min <= r4 <= cfg.w4_max):
        return None
    return w3 / w1, r4


def _impulse(x: list[float], cfg: WaveConfig) -> Optional[float]:
    """Score of a complete 5-wave impulse over 6 normalised pivots, or None."""
    r2 = _waves_1_2(x, cfg)
    if r2 is None:
        return None
    w34 = _waves_3_4(x, cfg)
    if w34 is None:
        return None
    w1, w3, w5 = x[1] - x[0], x[3] - x[2], x[5] - x[4]
    if w5 <= 0 or (w3 < w1 and w3 < w5):  # wave 3 may not be the shortest
        return None
    r3, r4 = w34
    return float(np.mean([
        _closeness(r2, 0.618),
        _closeness(r3, 1.618),
        _closeness(r4, 0.382),
        _closeness(w5 / w1, 1.0),
    ]))


def setup_from_pivots(pivots: Sequence[Pivot], cfg: WaveConfig = WaveConfig()) -> Optional[WaveSetup]:
    """The most specific valid setup ending at the latest confirmed pivot."""
    if len(pivots) >= 6:
        last = pivots[-6:]
        d, x = _normalise(last)
        score = _impulse(x, cfg)
        if score is not None:
            size = x[5] - x[0]
            return WaveSetup(
                label="ABC",
                direction=-d,
                target=d * (x[5] - cfg.abc_retrace * size),
                invalidation=d * x[5],
                stop=d * x[5],
                score=score,
                pivot_idx=tuple(p.idx for p in last),
            )

    if len(pivots) >= 5:
        last = pivots[-5:]
        d, x = _normalise(last)
        r2 = _waves_1_2(x, cfg)
        w34 = _waves_3_4(x, cfg) if r2 is not None else None
        if w34 is not None:
            w1, w3 = x[1] - x[0], x[3] - x[2]
            r3, r4 = w34
            # wave 5 ~ wave 1, but capped at wave 3 so wave 3 isn't the shortest
            return WaveSetup(
                label="W5",
                direction=d,
                target=d * (x[4] + min(w1, w3)),
                invalidation=d * x[1],
                stop=d * x[4],
                score=float(np.mean([_closeness(r2, 0.618), _closeness(r3, 1.618), _closeness(r4, 0.382)])),
                pivot_idx=tuple(p.idx for p in last),
            )

    if len(pivots) >= 3:
        last = pivots[-3:]
        d, x = _normalise(last)
        r2 = _waves_1_2(x, cfg)
        if r2 is not None:
            w1 = x[1] - x[0]
            return WaveSetup(
                label="W3",
                direction=d,
                target=d * (x[2] + cfg.w3_target * w1),
                invalidation=d * x[0],
                stop=d * x[2],
                score=_closeness(r2, 0.618),
                pivot_idx=tuple(p.idx for p in last),
            )
    return None


def find_impulses(pivots: Sequence[Pivot], cfg: WaveConfig = WaveConfig()) -> list[dict]:
    """Every complete 5-wave impulse in the pivot history (for charting)."""
    found = []
    for i in range(len(pivots) - 5):
        window = pivots[i:i + 6]
        d, x = _normalise(window)
        score = _impulse(x, cfg)
        if score is not None:
            found.append({
                "direction": d,
                "score": score,
                "pivot_idx": [p.idx for p in window],
                "prices": [p.price for p in window],
            })
    return found


def wave_context(
    df: pd.DataFrame,
    pivots: Sequence[Pivot],
    cfg: WaveConfig = WaveConfig(),
) -> pd.DataFrame:
    """Per-bar wave setup using only pivots confirmed on or before that bar."""
    n = len(df)
    label = np.full(n, "", dtype=object)
    direction = np.zeros(n, dtype=int)
    target = np.full(n, np.nan)
    invalidation = np.full(n, np.nan)
    stop = np.full(n, np.nan)
    score = np.full(n, np.nan)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)

    confirmed: list[Pivot] = []
    k = 0
    setup: Optional[WaveSetup] = None
    for t in range(n):
        while k < len(pivots) and pivots[k].confirmed_at <= t:
            confirmed.append(pivots[k])
            k += 1
            setup = setup_from_pivots(confirmed, cfg)
        if setup is not None:
            broken = low[t] < setup.invalidation if setup.direction == 1 else high[t] > setup.invalidation
            if broken:
                setup = None
        if setup is not None:
            label[t] = setup.label
            direction[t] = setup.direction
            target[t] = setup.target
            invalidation[t] = setup.invalidation
            stop[t] = setup.stop
            score[t] = setup.score

    return pd.DataFrame(
        {
            "wave_label": label,
            "wave_dir": direction,
            "wave_target": target,
            "wave_invalidation": invalidation,
            "wave_stop": stop,
            "wave_score": score,
        },
        index=df.index,
    )
