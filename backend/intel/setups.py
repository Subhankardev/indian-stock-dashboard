"""Trade setups, position sizing, costs and exposure limits (Phase 7).

Everything here is for **paper trading and research**. Nothing places an order.

Setup (long; short is the mirror):
    trigger  = today's high (entry = "breakout": only if the next session trades above it)
               or today's close (entry = "close": enter at the next open)
    stop     = max(trigger − atr_mult·ATR14, lowest low of the last ``swing_lookback`` sessions)
    targets  = trigger + r·(trigger − stop) for r in ``targets_r``
Sizing:
    qty = risk_amount / (trigger − stop), where risk_amount = capital·risk_per_trade_pct, then
    capped by max_position_pct of capital and max_adv_pct of 20-day average volume.
Costs (equity delivery, India; verify with your broker): brokerage, STT, exchange charges,
SEBI fee, stamp duty (buy), GST on brokerage+exchange+SEBI, DP charge (sell), and slippage.
"""
from __future__ import annotations

import math
from typing import Optional

import pandas as pd

from .config import load


def costs(buy_value: float, sell_value: float, cfg: Optional[dict] = None) -> dict:
    """Round-trip charges in ₹ for a delivery trade (buy then sell)."""
    c = (cfg or load())["costs"]
    turnover = buy_value + sell_value
    brokerage = 2 * c["brokerage_per_order"] + turnover * c["brokerage_pct"] / 100
    stt = turnover * c["stt_pct"] / 100
    exchange = turnover * c["exchange_pct"] / 100
    sebi = turnover * c["sebi_per_crore"] / 1e7
    stamp = buy_value * c["stamp_pct"] / 100
    gst = (brokerage + exchange + sebi) * c["gst_pct"] / 100
    dp = c["dp_per_sell"] if sell_value > 0 else 0.0
    slippage = turnover * c["slippage_bps"] / 1e4
    total = brokerage + stt + exchange + sebi + stamp + gst + dp + slippage
    return {"brokerage": brokerage, "stt": stt, "exchange": exchange, "sebi": sebi, "stamp": stamp, "gst": gst,
            "dp": dp, "slippage": slippage, "total": total,
            "pct_of_buy": total / buy_value * 100 if buy_value else None}


def setup_for(row: pd.Series, history: pd.DataFrame, direction: str = "bullish", cfg: Optional[dict] = None) -> dict:
    """Setup for one stock from its latest enriched row and recent bars (oldest first)."""
    cfg = cfg or load()
    t = cfg["trading"]
    atr = float(row.get("atr14") or float("nan"))
    if not math.isfinite(atr) or atr <= 0:
        return {"symbol": row.get("symbol"), "valid": False, "reason": "no ATR (need 14+ sessions)"}
    look = history.tail(t["swing_lookback"])
    long_ = direction == "bullish"
    trigger = float(row["high"] if t["entry"] == "breakout" else row["close"]) if long_ else \
        float(row["low"] if t["entry"] == "breakout" else row["close"])
    if long_:
        stop = max(trigger - t["atr_mult"] * atr, float(look["low"].min()))
        if stop >= trigger:
            stop = trigger - t["atr_mult"] * atr
    else:
        stop = min(trigger + t["atr_mult"] * atr, float(look["high"].max()))
        if stop <= trigger:
            stop = trigger + t["atr_mult"] * atr
    risk_ps = abs(trigger - stop)
    targets = [trigger + r * risk_ps if long_ else trigger - r * risk_ps for r in t["targets_r"]]
    capital = float(t["capital"])
    risk_amount = capital * t["risk_per_trade_pct"] / 100
    qty_risk = math.floor(risk_amount / risk_ps) if risk_ps > 0 else 0
    qty_cap = math.floor(capital * t["max_position_pct"] / 100 / trigger)
    adv = float(row.get("vol_avg20") or 0)
    qty_liq = math.floor(adv * t["max_adv_pct"] / 100) if adv > 0 else qty_risk
    qty = max(min(qty_risk, qty_cap, qty_liq), 0)
    limiter = min((("risk", qty_risk), ("position cap", qty_cap), ("liquidity", qty_liq)), key=lambda x: x[1])[0]
    value = qty * trigger
    c1 = costs(value, qty * targets[0], cfg) if qty else None
    cstop = costs(value, qty * stop, cfg) if qty else None
    gross_r1 = qty * risk_ps * t["targets_r"][0]
    notes = []
    if row.get("f_buildup") == "short_buildup" and long_:
        notes.append("futures show a short build-up")
    if row.get("dist_52w_high_pct") is not None and long_ and -3 <= float(row.get("dist_52w_high_pct") or -99) < 0:
        notes.append("just below the 52-week high (overhead supply)")
    if row.get("max_ce_oi_strike") and long_ and trigger < float(row["max_ce_oi_strike"]) < targets[0]:
        notes.append(f"call-OI wall at {row['max_ce_oi_strike']} before target 1")
    industry = row.get("industry")
    industry = industry if isinstance(industry, str) and industry else None   # NaN without sector lists
    return {
        "symbol": row.get("symbol"), "industry": industry, "direction": direction, "valid": qty > 0,
        "reason": None if qty > 0 else "position size rounds to 0",
        "as_of": str(pd.Timestamp(row["date"]).date()), "entry_rule": t["entry"],
        "trigger": round(trigger, 2), "stop": round(stop, 2), "targets": [round(x, 2) for x in targets],
        "risk_per_share": round(risk_ps, 2), "atr14": round(atr, 2), "stop_pct": round(risk_ps / trigger * 100, 2),
        "qty": int(qty), "position_value": round(value, 2), "position_pct": round(value / capital * 100, 2),
        "risk_amount": round(qty * risk_ps, 2), "sized_by": limiter,
        "costs_at_target1": None if c1 is None else round(c1["total"], 2),
        "costs_at_stop": None if cstop is None else round(cstop["total"], 2),
        "net_reward_risk_t1": None if not qty else round((gross_r1 - c1["total"]) / (qty * risk_ps + cstop["total"]), 2),
        "time_exit_sessions": t["time_exit_sessions"], "notes": notes,
    }


def check_limits(candidate: dict, open_trades: pd.DataFrame, cfg: Optional[dict] = None) -> list[str]:
    """Reasons a new paper position would breach the exposure limits (empty = OK)."""
    t = (cfg or load())["trading"]
    capital = float(t["capital"])
    problems = []
    live = open_trades[open_trades["status"].isin(["pending", "open"])] if len(open_trades) else open_trades
    if len(live) >= t["max_open_positions"]:
        problems.append(f"already {len(live)} open/pending positions (max {t['max_open_positions']})")
    if len(live) and (live["symbol"] == candidate["symbol"]).any():
        problems.append("already holding or pending in this stock")
    exposure = float((live["qty"] * live["entry"].fillna(live["trigger"])).sum()) if len(live) else 0.0
    if (exposure + candidate["position_value"]) / capital * 100 > t["max_total_exposure_pct"]:
        problems.append(f"total exposure would exceed {t['max_total_exposure_pct']}% of capital")
    if candidate.get("industry") and len(live):
        same = live[live["industry"] == candidate["industry"]]
        sector_val = float((same["qty"] * same["entry"].fillna(same["trigger"])).sum()) + candidate["position_value"]
        if sector_val / capital * 100 > t["max_sector_pct"]:
            problems.append(f"{candidate['industry']} exposure would exceed {t['max_sector_pct']}% of capital")
    return problems
