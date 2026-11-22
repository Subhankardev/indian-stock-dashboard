"""Technical indicators on one stock's daily bars.

Input: DataFrame indexed by date with open, high, low, close, volume (and optionally
deliv_qty, deliv_pct). Every indicator uses only data up to each row (no look-ahead).
Relative Strength (vs an index) lives in relative_strength.py — it is not RSI.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(dn != 0, 100.0).where(up.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig})


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def adx(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / tr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return pd.DataFrame({"adx": dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean(),
                         "plus_di": plus_di, "minus_di": minus_di})


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std()
    return pd.DataFrame({"bb_mid": mid, "bb_upper": mid + k * sd, "bb_lower": mid - k * sd,
                         "bb_pctb": (close - (mid - k * sd)) / (2 * k * sd)})


def donchian(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    """Channel of the PREVIOUS n sessions, so a close above dc_upper is a breakout."""
    return pd.DataFrame({"dc_upper": df["high"].rolling(n, min_periods=n).max().shift(1),
                         "dc_lower": df["low"].rolling(n, min_periods=n).min().shift(1)})


def roc(close: pd.Series, n: int = 20) -> pd.Series:
    return (close / close.shift(n) - 1) * 100


def supertrend(df: pd.DataFrame, n: int = 10, mult: float = 3.0) -> pd.DataFrame:
    a = atr(df, n).to_numpy()
    hl2 = ((df["high"] + df["low"]) / 2).to_numpy()
    close = df["close"].to_numpy()
    upper, lower = hl2 + mult * a, hl2 - mult * a
    st, direction = np.full(len(df), np.nan), np.zeros(len(df), dtype=int)
    fu, fl, d = np.nan, np.nan, 1
    for i in range(len(df)):
        if np.isnan(a[i]):
            continue
        if np.isnan(fu):
            fu, fl = upper[i], lower[i]
        else:
            fu = upper[i] if (upper[i] < fu or close[i - 1] > fu) else fu
            fl = lower[i] if (lower[i] > fl or close[i - 1] < fl) else fl
            if d == 1 and close[i] < fl:
                d = -1
            elif d == -1 and close[i] > fu:
                d = 1
        direction[i], st[i] = d, fl if d == 1 else fu
    return pd.DataFrame({"supertrend": st, "supertrend_dir": direction}, index=df.index)


def rvol(volume: pd.Series, n: int = 20) -> pd.Series:
    """Today's volume ÷ average of the previous n sessions."""
    return volume / volume.rolling(n, min_periods=n).mean().shift(1)


def obv(df: pd.DataFrame) -> pd.Series:
    sign = np.sign(df["close"].diff()).fillna(0)
    return (sign * df["volume"]).cumsum()


def _clv(df: pd.DataFrame) -> pd.Series:
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    return (((df["close"] - df["low"]) - (df["high"] - df["close"])) / rng).fillna(0)


def ad_line(df: pd.DataFrame) -> pd.Series:
    return (_clv(df) * df["volume"]).cumsum()


def cmf(df: pd.DataFrame, n: int = 20) -> pd.Series:
    mfv = _clv(df) * df["volume"]
    return mfv.rolling(n, min_periods=n).sum() / df["volume"].rolling(n, min_periods=n).sum()


def mfi(df: pd.DataFrame, n: int = 14) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    flow = tp * df["volume"]
    pos = flow.where(tp > tp.shift(1), 0.0).rolling(n, min_periods=n).sum()
    neg = flow.where(tp < tp.shift(1), 0.0).rolling(n, min_periods=n).sum()
    return 100 - 100 / (1 + pos / neg.replace(0, np.nan))


def up_down_volume_ratio(df: pd.DataFrame, n: int = 20) -> pd.Series:
    chg = df["close"].diff()
    up = df["volume"].where(chg > 0, 0.0).rolling(n, min_periods=n).sum()
    dn = df["volume"].where(chg < 0, 0.0).rolling(n, min_periods=n).sum()
    return up / dn.replace(0, np.nan)


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """Every Phase-1 indicator as columns next to the input bars."""
    df = df.sort_index()
    c = df["close"]
    out = pd.DataFrame(index=df.index)
    out["ret_1d"] = c.pct_change() * 100
    out["ema20"], out["dma50"], out["dma100"], out["dma200"] = ema(c, 20), sma(c, 50), sma(c, 100), sma(c, 200)
    out["rsi14"] = rsi(c)
    out = out.join(macd(c)).join(adx(df)).join(bollinger(c)).join(donchian(df)).join(supertrend(df))
    out["atr14"] = atr(df)
    out["atr_pct"] = out["atr14"] / c * 100
    out["roc20"] = roc(c, 20)
    out["rvol20"] = rvol(df["volume"])
    out["obv"] = obv(df)
    out["ad_line"] = ad_line(df)
    out["cmf20"] = cmf(df)
    out["mfi14"] = mfi(df)
    out["updown_vol20"] = up_down_volume_ratio(df)
    out["vol_avg20"] = sma(df["volume"], 20)
    out["vol_avg50"] = sma(df["volume"], 50)
    out["volatility20"] = c.pct_change().rolling(20, min_periods=20).std() * 100
    out["high_52w"] = df["high"].rolling(252, min_periods=120).max()
    out["dist_52w_high_pct"] = (c / out["high_52w"] - 1) * 100
    if "deliv_qty" in df:
        dq = df["deliv_qty"]
        out["deliv_pct"] = df.get("deliv_pct")
        for n in (5, 20, 50):
            out[f"deliv_pct_avg{n}"] = df["deliv_pct"].rolling(n, min_periods=max(3, n // 2)).mean()
        out["deliv_qty_avg20"] = dq.rolling(20, min_periods=10).mean()
        out["deliv_qty_avg50"] = dq.rolling(50, min_periods=25).mean()
    return out
