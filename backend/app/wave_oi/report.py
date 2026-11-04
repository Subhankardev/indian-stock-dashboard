"""Turn the analysis + backtest into a JSON-safe payload for the API."""
from __future__ import annotations

import math
from typing import Any, Optional

import pandas as pd

from .backtest import BacktestConfig, run_backtest
from .oi import has_oi
from .strategy import StrategyConfig, analyze
from .waves import find_impulses


def _clean(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if hasattr(v, "item"):  # numpy scalar
        v = v.item()
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    return v


def _date(idx, i: int) -> str:
    d = idx[i]
    return d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)


def build_report(
    df: pd.DataFrame,
    symbol: str,
    cfg: Optional[StrategyConfig] = None,
    bt_cfg: Optional[BacktestConfig] = None,
    chart_bars: int = 250,
    source: str = "nse",
) -> dict:
    cfg = cfg or StrategyConfig()
    an, pivots = analyze(df, cfg)
    idx = an.index
    last = an.iloc[-1]

    signals = an[an["signal"] != 0]
    last_signal = None
    if len(signals):
        s = signals.iloc[-1]
        last_signal = {
            "date": _date(idx, an.index.get_loc(signals.index[-1])),
            "action": "BUY" if s["signal"] == 1 else "SELL",
            "price": s["close"],
            "stop": s["stop"],
            "target": s["target"],
            "reason": s["reason"],
            "bars_ago": len(an) - 1 - an.index.get_loc(signals.index[-1]),
        }

    bt = run_backtest(an, bt_cfg)
    start = max(0, len(an) - chart_bars)
    chart = an.iloc[start:]

    payload = {
        "symbol": symbol.upper(),
        "source": source,
        "as_of": _date(idx, len(an) - 1),
        "bars": len(an),
        "oi_available": has_oi(df),
        "latest": {
            "close": last["close"],
            "wave": {
                "label": last["wave_label"] or None,
                "direction": int(last["wave_dir"]),
                "target": last["wave_target"],
                "invalidation": last["wave_invalidation"],
                "stop": last["wave_stop"],
                "score": last["wave_score"],
            },
            "gti": {"direction": int(last["gti_dir"]), "line": last["gti_line"]},
            "oi": {
                "regime": last["oi_regime"],
                "change_pct": last["oi_change"] * 100 if pd.notna(last["oi_change"]) else None,
                "open_interest": last.get("open_interest"),
                "divergence": int(last["oi_divergence"]),
                "ghost_today": bool(last["ghost"]),
                "ghost_bias_recent": int(an["ghost_bias"].iloc[-cfg.ghost_window:].sum()),
            },
            "signal_today": "BUY" if last["signal"] == 1 else "SELL" if last["signal"] == -1 else "NONE",
        },
        "last_signal": last_signal,
        "pivots": [
            {"date": _date(idx, p.idx), "price": p.price, "kind": p.kind,
             "confirmed": _date(idx, p.confirmed_at)}
            for p in pivots if p.idx >= start
        ],
        "impulses": [
            {**imp, "dates": [_date(idx, i) for i in imp["pivot_idx"]]}
            for imp in find_impulses(pivots, cfg.wave) if imp["pivot_idx"][0] >= start
        ],
        "chart": {
            "dates": [_date(idx, i) for i in range(start, len(an))],
            "close": chart["close"].tolist(),
            "gti_line": chart["gti_line"].tolist(),
            "gti_dir": chart["gti_dir"].tolist(),
            "open_interest": chart["open_interest"].tolist() if "open_interest" in chart else [],
            "signals": [
                {"date": _date(idx, an.index.get_loc(i)), "action": "BUY" if r.signal == 1 else "SELL", "price": r.close}
                for i, r in chart[chart["signal"] != 0].iterrows()
            ],
        },
        "backtest": {"metrics": bt["metrics"], "trades": bt["trades"][-25:]},
    }
    return _clean(payload)
