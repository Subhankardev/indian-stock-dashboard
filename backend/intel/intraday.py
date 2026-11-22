"""Intraday VWAP and volume profile from 1-minute candles (Phase 4).

Source: Upstox historical/intraday 1-minute candles (free with the daily token). These are
*bar-level* approximations — each minute's volume is placed at its typical price. They are
not order flow: no bid/ask side, no trade sizes (see ``intel.orderflow``).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import numpy as np
import pandas as pd


def parse_minutes(rows) -> pd.DataFrame:
    cols = ["timestamp", "open", "high", "low", "close", "volume", "oi"]
    rows = list(rows or [])
    if not rows:
        return pd.DataFrame(columns=cols[1:6], index=pd.DatetimeIndex([], name="ts"))
    df = pd.DataFrame([list(r) + [None] * (7 - len(r)) for r in rows], columns=cols)
    ts = pd.to_datetime(df.pop("timestamp"), utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    df.index = pd.DatetimeIndex(ts, name="ts")
    return df[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce").sort_index()


def session_vwap(m: pd.DataFrame) -> pd.DataFrame:
    """Cumulative VWAP and ±1/±2 standard-deviation bands for one session of minute bars."""
    tp = (m["high"] + m["low"] + m["close"]) / 3
    v = m["volume"].fillna(0)
    cv = v.cumsum().replace(0, np.nan)
    vwap = (tp * v).cumsum() / cv
    var = ((tp - vwap) ** 2 * v).cumsum() / cv
    sd = np.sqrt(var)
    return pd.DataFrame({"close": m["close"], "volume": v, "vwap": vwap, "upper1": vwap + sd, "lower1": vwap - sd,
                         "upper2": vwap + 2 * sd, "lower2": vwap - 2 * sd}, index=m.index)


def volume_profile(m: pd.DataFrame, bins: int = 40, value_area: float = 0.70) -> dict:
    """Volume by price (typical price per bar), point of control and value area."""
    if m.empty or m["volume"].sum() <= 0:
        return {"bins": [], "poc": None, "vah": None, "val": None}
    tp = ((m["high"] + m["low"] + m["close"]) / 3).to_numpy()
    lo, hi = float(m["low"].min()), float(m["high"].max())
    if hi <= lo:
        hi = lo + 1e-6
    edges = np.linspace(lo, hi, bins + 1)
    idx = np.clip(np.digitize(tp, edges) - 1, 0, bins - 1)
    vol = np.bincount(idx, weights=m["volume"].fillna(0).to_numpy(), minlength=bins)
    mids = (edges[:-1] + edges[1:]) / 2
    poc_i = int(vol.argmax())
    lo_i, hi_i = poc_i, poc_i
    total, acc = vol.sum(), vol[poc_i]
    while acc < value_area * total and (lo_i > 0 or hi_i < bins - 1):
        down = vol[lo_i - 1] if lo_i > 0 else -1
        up = vol[hi_i + 1] if hi_i < bins - 1 else -1
        if up >= down:
            hi_i += 1
            acc += vol[hi_i]
        else:
            lo_i -= 1
            acc += vol[lo_i]
    return {"bins": [{"price": float(p), "volume": float(x)} for p, x in zip(mids, vol)],
            "poc": float(mids[poc_i]), "vah": float(edges[hi_i + 1]), "val": float(edges[lo_i])}


def synthetic_session(day: date, base: float = 1000.0, seed: int = 0) -> pd.DataFrame:
    """Demo minute bars 09:15–15:30 IST (375 bars). Always labelled demo by the API."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(f"{day} 09:15", periods=375, freq="min")
    c = base * np.exp(np.cumsum(rng.normal(0, 0.0008, 375)))
    o = np.concatenate([[base], c[:-1]])
    u = np.linspace(-1, 1, 375)
    vol = (1e4 * (1.5 + u ** 2) * rng.uniform(0.5, 1.5, 375)).round()
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.0005, "low": np.minimum(o, c) * 0.9995,
                         "close": c, "volume": vol}, index=pd.DatetimeIndex(idx, name="ts"))


def summary(m: pd.DataFrame) -> dict:
    sv = session_vwap(m)
    prof = volume_profile(m)
    last = sv.iloc[-1] if len(sv) else None
    return {
        "bars": [{"ts": t.strftime("%H:%M"), **{k: (None if pd.isna(v) else float(v)) for k, v in r.items()}}
                 for t, r in sv.iterrows()],
        "profile": prof,
        "last": None if last is None else {"close": float(last["close"]), "vwap": float(last["vwap"]),
                                           "dist_vwap_pct": float((last["close"] / last["vwap"] - 1) * 100)},
    }


def fetch_minutes(symbol: str, day: Optional[date] = None) -> pd.DataFrame:
    """1-minute candles from Upstox for ``day`` (default today). Needs UPSTOX_ACCESS_TOKEN."""
    from urllib.parse import quote
    from app import upstox_data
    cl = upstox_data.client()
    inst = upstox_data.find_equity(cl.instruments(), symbol)
    key = quote(inst["instrument_key"], safe="")
    if day is None or day >= date.today():
        body = cl._get(f"/v3/historical-candle/intraday/{key}/minutes/1")
    else:
        body = cl._get(f"/v3/historical-candle/{key}/minutes/1/{day:%Y-%m-%d}/{day:%Y-%m-%d}")
    return parse_minutes(body.get("data", {}).get("candles", []))
