"""Combine wave structure, GTI timing and OI confirmation into trade signals.

A LONG signal fires on the first bar where all of these hold:

* wave context points up (riding wave 3 or wave 5; optionally the A-B-C
  correction after a down impulse),
* GTI direction is up,
* OI confirms: the price/OI regime is ``new_longs`` or a bullish ghost
  build-up happened within ``ghost_window`` bars,
* reward/risk to the wave target vs. the wave invalidation >= ``min_rr``.

SHORT is the mirror image. Targets are the Fibonacci projection. By default
the stop sits just beyond the latest pivot (the end of wave 2 or wave 4);
``stop_mode="invalidation"`` uses the level that proves the whole count wrong.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .gti import compute_gti
from .oi import ghost_activity, has_oi, oi_divergence, oi_regime
from .waves import Pivot, WaveConfig, wave_context, zigzag


@dataclass
class StrategyConfig:
    atr_len: int = 14
    zz_atr_mult: float = 2.5
    gti_atr_len: int = 10
    gti_mult: float = 2.5
    gti_smooth: int = 3
    oi_lookback: int = 5
    oi_min_change: float = 0.01
    ghost_window: int = 10
    ghost_z: float = 2.0
    ghost_max_body_atr: float = 0.5
    ghost_z_window: int = 50
    use_wave: bool = True
    use_oi: bool = True
    allow_short: bool = True
    trade_corrections: bool = False
    min_rr: float = 1.5
    stop_mode: str = "pivot"  # "pivot": beyond the wave 2/4 low; "invalidation": full-count level
    long_oi_regimes: tuple = ("new_longs",)
    short_oi_regimes: tuple = ("new_shorts",)
    wave: WaveConfig = field(default_factory=WaveConfig)


def analyze(df: pd.DataFrame, cfg: StrategyConfig | None = None) -> tuple[pd.DataFrame, list[Pivot]]:
    """Return (bar-by-bar analysis frame, swing pivots). Every column is causal."""
    cfg = cfg or StrategyConfig()
    df = df.sort_index()
    pivots = zigzag(df, cfg.atr_len, cfg.zz_atr_mult)
    out = pd.concat(
        [
            df,
            wave_context(df, pivots, cfg.wave),
            compute_gti(df, cfg.gti_atr_len, cfg.gti_mult, cfg.gti_smooth),
        ],
        axis=1,
    )

    oi_ok = has_oi(df)
    use_oi = cfg.use_oi and oi_ok
    if oi_ok:
        out = pd.concat(
            [
                out,
                oi_regime(df, cfg.oi_lookback, cfg.oi_min_change),
                ghost_activity(df, cfg.ghost_z_window, cfg.ghost_z, cfg.ghost_max_body_atr, cfg.atr_len),
                oi_divergence(df),
            ],
            axis=1,
        )
    else:
        out["oi_regime"] = "n/a"
        out["oi_change"] = np.nan
        out["ghost"] = False
        out["ghost_bias"] = 0
        out["oi_divergence"] = 0

    close = out["close"]
    gti_line = out["gti_line"]
    recent_ghost_up = (out["ghost_bias"] > 0).astype(int).rolling(cfg.ghost_window, min_periods=1).max() > 0
    recent_ghost_dn = (out["ghost_bias"] < 0).astype(int).rolling(cfg.ghost_window, min_periods=1).max() > 0

    tradeable = {"W3", "W5"} | ({"ABC"} if cfg.trade_corrections else set())
    wave_ok = out["wave_label"].isin(tradeable)

    def side(sign: int) -> tuple[pd.Series, pd.Series, pd.Series]:
        if cfg.use_wave:
            stop = out["wave_stop"] if cfg.stop_mode == "pivot" else out["wave_invalidation"]
            target = out["wave_target"]
            wave_cond = wave_ok & (out["wave_dir"] == sign)
        else:
            stop = gti_line
            target = close + sign * 2.0 * (close - gti_line).abs()
            wave_cond = pd.Series(True, index=out.index)
        risk = sign * (close - stop)
        reward = sign * (target - close)
        rr = reward / risk.where(risk > 0)
        if use_oi:
            regimes = cfg.long_oi_regimes if sign == 1 else cfg.short_oi_regimes
            ghost = recent_ghost_up if sign == 1 else recent_ghost_dn
            # Bars before OI history begins can't be confirmed either way: don't filter them.
            oi_cond = out["oi_regime"].isin(regimes) | ghost | out["oi_change"].isna()
        else:
            oi_cond = pd.Series(True, index=out.index)
        cond = wave_cond & (out["gti_dir"] == sign) & oi_cond & (rr >= cfg.min_rr)
        return cond.fillna(False), stop, target

    long_cond, long_stop, long_target = side(1)
    short_cond, short_stop, short_target = side(-1)
    if not cfg.allow_short:
        short_cond = pd.Series(False, index=out.index)

    long_sig = long_cond & ~long_cond.shift(1, fill_value=False)
    short_sig = short_cond & ~short_cond.shift(1, fill_value=False)
    out["signal"] = np.where(long_sig, 1, np.where(short_sig, -1, 0))
    out["stop"] = np.where(long_sig, long_stop, np.where(short_sig, short_stop, np.nan))
    out["target"] = np.where(long_sig, long_target, np.where(short_sig, short_target, np.nan))
    out["reason"] = [
        _reason(row, use_oi, cfg.use_wave) if s != 0 else ""
        for s, row in zip(out["signal"], out.itertuples())
    ]
    return out, pivots


def _reason(row, use_oi: bool, use_wave: bool) -> str:
    parts = []
    if use_wave:
        arrow = "up" if row.wave_dir == 1 else "down"
        parts.append(f"{row.wave_label} {arrow} (score {row.wave_score:.2f})")
    parts.append("GTI " + ("up" if row.gti_dir == 1 else "down"))
    if use_oi:
        parts.append(f"OI {row.oi_regime}" + (" + ghost build-up" if row.ghost_bias != 0 else ""))
    return " | ".join(parts)
