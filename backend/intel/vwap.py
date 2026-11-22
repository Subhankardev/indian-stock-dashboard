"""VWAP on daily data (Phase 4).

NSE's daily bhavcopy gives each day's *average traded price* (``turnover / volume``), which is
that day's exact VWAP. From it:

* rolling VWAP over N sessions = Σ(avg_price·volume) / Σ volume
* anchored VWAP (AVWAP) from an event: the 52-week high, the 52-week low, the highest-volume
  session of the last 60, and the start of the year. Each anchor is chosen using only data up
  to that row, so there is no look-ahead.
* daily-bar absorption (class D): heavy volume (RVOL ≥2) in a narrow range (≤0.6× ATR)
  closing in the upper (lower) third — supply (demand) absorbed. A daily-bar proxy only;
  true absorption needs tick data (see ``intel.orderflow``).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ANCHORS = {"hi52": "52-week high", "lo52": "52-week low", "volspike60": "highest-volume session (60D)",
           "ytd": "start of year"}


def day_vwap(g: pd.DataFrame) -> pd.Series:
    """Per-day VWAP: turnover/volume when available, else typical price."""
    if "turnover_lacs" in g:
        v = g["turnover_lacs"] * 1e5 / g["volume"].replace(0, np.nan)
        return v.fillna((g["high"] + g["low"] + g["close"]) / 3)
    return (g["high"] + g["low"] + g["close"]) / 3


def rolling_vwap(g: pd.DataFrame, n: int) -> pd.Series:
    pv = day_vwap(g) * g["volume"]
    return pv.rolling(n, min_periods=n).sum() / g["volume"].rolling(n, min_periods=n).sum()


def _anchored(pv: np.ndarray, vol: np.ndarray, anchor_pos: np.ndarray) -> np.ndarray:
    """AVWAP for each row t from anchor_pos[t] (≤ t) to t."""
    cpv, cv = np.concatenate([[0], np.cumsum(pv)]), np.concatenate([[0], np.cumsum(vol)])
    out = np.full(len(pv), np.nan)
    ok = anchor_pos >= 0
    t = np.arange(len(pv))[ok]
    a = anchor_pos[ok].astype(int)
    den = cv[t + 1] - cv[a]
    out[ok] = np.where(den > 0, (cpv[t + 1] - cpv[a]) / np.where(den > 0, den, 1), np.nan)
    return out


def _argext_pos(x: pd.Series, n: int, fn: str) -> np.ndarray:
    """Position of the rolling max/min over the window of n rows ending at each row
    (latest position wins ties). O(n) monotonic deque; -1 until a non-NaN value is seen."""
    from collections import deque
    arr = x.to_numpy(dtype=float)
    sign = 1.0 if fn == "max" else -1.0
    out = np.full(len(arr), -1)
    dq: deque = deque()
    for t, v in enumerate(arr):
        while dq and dq[0] <= t - n:
            dq.popleft()
        if not np.isnan(v):
            while dq and sign * arr[dq[-1]] <= sign * v:
                dq.pop()
            dq.append(t)
        if dq:
            out[t] = dq[0]
    return out


def compute(g: pd.DataFrame) -> pd.DataFrame:
    """VWAP features for one symbol (index = dates, sorted)."""
    out = pd.DataFrame(index=g.index)
    vol = g["volume"].fillna(0).to_numpy(dtype=float)
    pv = (day_vwap(g) * g["volume"]).fillna(0).to_numpy(dtype=float)
    out["vwap20"] = rolling_vwap(g, 20)
    out["vwap50"] = rolling_vwap(g, 50)
    anchors = {
        "hi52": _argext_pos(g["high"], 252, "max"),
        "lo52": _argext_pos(g["low"], 252, "min"),
        "volspike60": _argext_pos(g["volume"].astype(float), 60, "max"),
    }
    years = pd.DatetimeIndex(g.index).year.to_numpy()
    first_of_year = np.zeros(len(years), dtype=int)
    for i in range(1, len(years)):
        first_of_year[i] = i if years[i] != years[i - 1] else first_of_year[i - 1]
    anchors["ytd"] = first_of_year
    close = g["close"].to_numpy(dtype=float)
    for k, pos in anchors.items():
        av = _anchored(pv, vol, pos)
        out[f"avwap_{k}"] = av
        out[f"dist_avwap_{k}_pct"] = (close / av - 1) * 100
    out["dist_vwap20_pct"] = (g["close"] / out["vwap20"] - 1) * 100
    rng = (g["high"] - g["low"])
    atr = rng.rolling(14, min_periods=14).mean()
    pos_in_range = (g["close"] - g["low"]) / rng.replace(0, np.nan)
    rvol = g["volume"] / g["volume"].shift(1).rolling(20, min_periods=20).mean()
    narrow = rng <= 0.6 * atr
    out["absorption_up"] = (rvol >= 2) & narrow & (pos_in_range >= 2 / 3)
    out["absorption_down"] = (rvol >= 2) & narrow & (pos_in_range <= 1 / 3)
    prev = out["dist_avwap_volspike60_pct"].shift(1)
    out["avwap_reclaim"] = (prev < 0) & (out["dist_avwap_volspike60_pct"] > 0) & (rvol >= 1.2)
    out["avwap_loss"] = (prev > 0) & (out["dist_avwap_volspike60_pct"] < 0) & (rvol >= 1.2)
    return out


def features(panel: pd.DataFrame) -> pd.DataFrame:
    if panel.empty:
        return pd.DataFrame(index=panel.index)
    parts = []
    for _, g in panel.groupby("symbol", sort=False):
        parts.append(compute(g.set_index("date")).set_index(g.index))
    return pd.concat(parts).reindex(panel.index)
