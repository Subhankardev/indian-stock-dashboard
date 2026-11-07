"""Bar-by-bar backtest of the signals produced by ``strategy.analyze``.

* A signal on bar t is filled at the open of bar t+1 (no look-ahead).
* Position size risks ``risk_per_trade`` of equity between entry and stop,
  capped by ``max_leverage``.
* Stop is checked before target inside a bar (conservative). Gaps through a
  level fill at the open.
* Optionally exits at the next open after the GTI flips against the trade.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class BacktestConfig:
    initial_equity: float = 100_000.0
    risk_per_trade: float = 0.01
    fee_bps: float = 5.0          # per side, covers brokerage + slippage
    max_leverage: float = 1.0
    exit_on_gti_flip: bool = True
    exit_mode: str = "target"     # "target": fixed target; "trail": no target, stop trails the GTI line


@dataclass
class _Position:
    side: int
    entry_bar: int
    entry: float
    stop: float
    target: float
    qty: float
    risk_per_unit: float
    fee_in: float
    exit_next_open: bool = False
    initial_stop: float = 0.0


def run_backtest(an: pd.DataFrame, cfg: Optional[BacktestConfig] = None) -> dict:
    cfg = cfg or BacktestConfig()
    o = an["open"].to_numpy(float)
    h = an["high"].to_numpy(float)
    lo = an["low"].to_numpy(float)
    c = an["close"].to_numpy(float)
    sig = an["signal"].to_numpy(int)
    stops = an["stop"].to_numpy(float)
    targets = an["target"].to_numpy(float)
    flips = an["gti_flip"].to_numpy(int)
    gti_line = an["gti_line"].to_numpy(float)
    gti_dir = an["gti_dir"].to_numpy(int)
    trail = cfg.exit_mode == "trail"
    idx = an.index
    n = len(an)
    fee = cfg.fee_bps / 10_000.0

    equity = cfg.initial_equity
    curve = np.empty(n)
    trades: list[dict] = []
    pos: Optional[_Position] = None
    pending: Optional[tuple] = None

    def close_pos(t: int, price: float, why: str) -> None:
        nonlocal equity, pos
        gross = pos.qty * pos.side * (price - pos.entry)
        fee_out = pos.qty * price * fee
        pnl = gross - pos.fee_in - fee_out
        equity += pnl
        trades.append({
            "side": "LONG" if pos.side == 1 else "SHORT",
            "entry_date": str(idx[pos.entry_bar]),
            "exit_date": str(idx[t]),
            "entry": pos.entry,
            "exit": price,
            "stop": pos.initial_stop,          # the stop the trade was sized on (R uses this)
            "exit_stop": pos.stop,             # where the (possibly trailed) stop ended
            "target": pos.target,
            "qty": pos.qty,
            "pnl": pnl,
            "r_multiple": gross / (pos.qty * pos.risk_per_unit),
            "exit_reason": why,
            "bars_held": t - pos.entry_bar,
        })
        pos = None

    for t in range(n):
        if pending is not None and pos is None:
            side, stop, target = pending
            entry = o[t]
            risk_per_unit = side * (entry - stop)
            if risk_per_unit > 0 and side * (target - entry) > 0 and equity > 0:
                qty = equity * cfg.risk_per_trade / risk_per_unit
                qty = min(qty, equity * cfg.max_leverage / entry)
                pos = _Position(side, t, entry, stop, target, qty, risk_per_unit, qty * entry * fee,
                                initial_stop=stop)
        pending = None

        if pos is not None and trail and t > pos.entry_bar and gti_dir[t - 1] == pos.side:
            # ratchet the stop to yesterday's GTI line - never loosen it
            line = gti_line[t - 1]
            if line == line:  # not NaN
                pos.stop = max(pos.stop, line) if pos.side == 1 else min(pos.stop, line)

        if pos is not None:
            if pos.exit_next_open:
                close_pos(t, o[t], "gti_flip")
            elif pos.side == 1:
                if lo[t] <= pos.stop:
                    close_pos(t, min(o[t], pos.stop), "trailing_stop" if pos.stop != pos.initial_stop else "stop")
                elif not trail and h[t] >= pos.target:
                    close_pos(t, max(o[t], pos.target), "target")
            else:
                if h[t] >= pos.stop:
                    close_pos(t, max(o[t], pos.stop), "trailing_stop" if pos.stop != pos.initial_stop else "stop")
                elif not trail and lo[t] <= pos.target:
                    close_pos(t, min(o[t], pos.target), "target")
            if pos is not None and cfg.exit_on_gti_flip and flips[t] == -pos.side:
                pos.exit_next_open = True

        if pos is None and sig[t] != 0 and t < n - 1:
            pending = (int(sig[t]), stops[t], targets[t])

        curve[t] = equity + (pos.qty * pos.side * (c[t] - pos.entry) if pos is not None else 0.0)

    if pos is not None:
        close_pos(n - 1, c[n - 1], "end_of_data")
        curve[n - 1] = equity

    equity_curve = pd.Series(curve, index=idx, name="equity")
    return {"trades": trades, "equity": equity_curve, "metrics": _metrics(trades, equity_curve, c, cfg)}


def _metrics(trades: list[dict], curve: pd.Series, close: np.ndarray, cfg: BacktestConfig) -> dict:
    pnl = np.array([t["pnl"] for t in trades], dtype=float)
    r = np.array([t["r_multiple"] for t in trades], dtype=float)
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    peak = curve.cummax()
    dd = ((curve - peak) / peak).min() if len(curve) else 0.0
    return {
        "trades": int(len(trades)),
        "win_rate": float(len(wins) / len(pnl)) if len(pnl) else None,
        "total_return_pct": float((curve.iloc[-1] / cfg.initial_equity - 1) * 100) if len(curve) else 0.0,
        "buy_hold_return_pct": float((close[-1] / close[0] - 1) * 100) if len(close) else 0.0,
        "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else None,
        "avg_r": float(r.mean()) if len(r) else None,
        "max_drawdown_pct": float(dd * 100),
        "final_equity": float(curve.iloc[-1]) if len(curve) else cfg.initial_equity,
    }
