"""Event studies: does a signal predict excess returns?

For each event (symbol, date t): enter at the NEXT session's open, exit at the close h
sessions later; subtract the benchmark's return over the same span and round-trip costs.
Bearish signals are measured as shorts (sign flipped).

Statistics are reported per horizon, with two views:
  * event-level: mean, median, hit rate
  * date-clustered: events on the same day are averaged first, then a t-statistic and 95%
    interval are computed across days — 50 stocks firing on one day are one observation,
    not 50, which keeps the test honest in market-wide moves.

``passes`` applies the design's gate (docs/DESIGN.md §11).
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd


def forward_returns(panel: pd.DataFrame, bench: pd.Series, horizons: list[int]) -> pd.DataFrame:
    """Per row: next-open-to-close-after-h return minus benchmark, for each h (in %)."""
    panel = panel.sort_values(["symbol", "date"])
    b = bench.sort_index()
    out = {}
    for h in horizons:
        g = panel.groupby("symbol", sort=False)
        entry = g["open"].shift(-1)
        exit_ = g["close"].shift(-h)
        exit_date = g["date"].shift(-h)
        stock = (exit_ / entry - 1) * 100
        b_start = b.reindex(panel["date"].values, method="ffill").to_numpy()
        b_end = b.reindex(exit_date.values, method="ffill").to_numpy() if len(b) else np.nan
        bench_ret = (b_end / b_start - 1) * 100
        out[f"fwd_{h}"] = stock.to_numpy() - bench_ret
    return pd.DataFrame(out, index=panel.index).reindex(panel.index)


def first_in_run(mask: pd.Series, panel: pd.DataFrame, cooldown: int = 0) -> pd.Series:
    """Keep only the first day of each run of True per symbol, plus an optional cooldown
    (no new event within ``cooldown`` sessions of the previous one, same symbol)."""
    m = mask.reindex(panel.index).fillna(False).astype(bool)
    prev = m.groupby(panel["symbol"]).shift(1, fill_value=False)
    starts = m & ~prev
    if cooldown <= 0 or not starts.any():
        return starts
    pos = panel.groupby("symbol").cumcount()
    keep = pd.Series(False, index=panel.index)
    last: dict = {}
    for ix in panel.index[starts.to_numpy()]:
        sym, p = panel.at[ix, "symbol"], pos.at[ix]
        if p - last.get(sym, -10**9) > cooldown:
            keep.at[ix] = True
            last[sym] = p
    return keep


def _t_ci(values: np.ndarray) -> tuple[Optional[float], Optional[float], Optional[float]]:
    v = values[~np.isnan(values)]
    if len(v) < 3:
        return None, None, None
    m, sd = v.mean(), v.std(ddof=1)
    se = sd / math.sqrt(len(v))
    t = m / se if se > 0 else None
    return t, m - 1.96 * se, m + 1.96 * se


def summarize(events: pd.DataFrame, horizons: list[int], cost_pct: float, direction: str,
              subperiods: int = 3) -> dict:
    sign = -1.0 if direction == "bearish" else 1.0
    res = {"events": int(len(events)), "direction": direction, "horizons": {}}
    if events.empty:
        return res
    res["first"] = str(events["date"].min().date())
    res["last"] = str(events["date"].max().date())
    for h in horizons:
        r = sign * events[f"fwd_{h}"] - cost_pct
        ev = events.assign(r=r).dropna(subset=["r"])
        if ev.empty:
            res["horizons"][h] = {"n": 0}
            continue
        by_day = ev.groupby("date")["r"].mean()
        t, lo, hi = _t_ci(by_day.to_numpy())
        chunks = np.array_split(by_day.sort_index(), subperiods) if len(by_day) >= subperiods else [by_day]
        sub = [float(c.mean()) for c in chunks if len(c)]
        top_symbol_share = float(ev.groupby("symbol")["r"].sum().abs().max() / ev["r"].abs().sum()) if ev["r"].abs().sum() else 0.0
        res["horizons"][h] = {
            "n": int(len(ev)), "days": int(len(by_day)),
            "mean_excess_pct": float(ev["r"].mean()), "median_excess_pct": float(ev["r"].median()),
            "hit_rate": float((ev["r"] > 0).mean()),
            "day_mean_pct": float(by_day.mean()), "t_stat": t, "ci95": [lo, hi],
            "subperiod_means": sub, "top_symbol_share": top_symbol_share,
        }
    return res


def passes(summary: dict, rules: dict) -> tuple[bool, str]:
    h = rules["pass_horizon"]
    s = summary["horizons"].get(h) or summary["horizons"].get(str(h))
    if not s or not s.get("n"):
        return False, "no events with a complete forward window"
    if s["n"] < rules["pass_min_events"]:
        return False, f"only {s['n']} events (need {rules['pass_min_events']})"
    lo = (s.get("ci95") or [None])[0]
    if lo is None or lo <= 0:
        return False, f"{h}-day excess return's 95% range includes or is below 0 (low end {lo if lo is None else round(lo, 2)}%)"
    pos = sum(1 for m in s["subperiod_means"] if m > 0)
    if pos < rules["pass_min_positive_subperiods"]:
        return False, f"positive in only {pos} of {len(s['subperiod_means'])} sub-periods"
    if s["top_symbol_share"] > 0.25:
        return False, f"one stock drives {s['top_symbol_share']:.0%} of the result"
    return True, f"passes: {h}-day excess {s['day_mean_pct']:.2f}% per event-day, 95% range above 0, positive in {pos} sub-periods"


def run(panel: pd.DataFrame, mask: pd.Series, bench: pd.Series, rules: dict, direction: str = "bullish",
        cooldown: Optional[int] = None) -> dict:
    """Full event study for a boolean signal ``mask`` aligned to ``panel`` rows."""
    horizons = rules["horizons"]
    cooldown = rules["pass_horizon"] if cooldown is None else cooldown
    fwd = forward_returns(panel, bench, horizons)
    ev_mask = first_in_run(mask, panel, cooldown)
    events = pd.concat([panel.loc[ev_mask, ["date", "symbol"]], fwd.loc[ev_mask]], axis=1)
    summary = summarize(events, horizons, rules["cost_bps_round_trip"] / 100.0, direction, rules["subperiods"])
    ok, why = passes(summary, rules)
    summary.update(passed=ok, verdict=why)
    return summary
