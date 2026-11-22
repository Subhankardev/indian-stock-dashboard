"""Walk-forward selection among signal variants.

Each window: pick the variant with the best date-clustered mean excess return over the
previous ``train_days`` (in-sample), then record only that variant's events in the next
``test_days`` (out-of-sample). Stitching the out-of-sample parts gives an honest estimate
of "choose the best rule, then trade it".
"""
from __future__ import annotations


import pandas as pd

from .event_study import first_in_run, forward_returns, passes, summarize


def walk_forward(panel: pd.DataFrame, variants: dict[str, pd.Series], bench: pd.Series, rules: dict,
                 direction: str = "bullish", train_days: int = 504, test_days: int = 126,
                 min_train_events: int = 30) -> dict:
    h = rules["pass_horizon"]
    sign = -1.0 if direction == "bearish" else 1.0
    cost = rules["cost_bps_round_trip"] / 100.0
    fwd = forward_returns(panel, bench, rules["horizons"])
    events = {}
    for name, mask in variants.items():
        ev = first_in_run(mask, panel, h)
        events[name] = pd.concat([panel.loc[ev, ["date", "symbol"]], fwd.loc[ev]], axis=1)

    dates = sorted(panel["date"].unique())
    chosen, oos = [], []
    i = train_days
    while i < len(dates):
        tr_start, tr_end = dates[i - train_days], dates[i - 1]
        te_end = dates[min(i + test_days, len(dates)) - 1]
        best, best_score = None, None
        for name, ev in events.items():
            tr = ev[(ev["date"] >= tr_start) & (ev["date"] <= tr_end)]
            r = (sign * tr[f"fwd_{h}"] - cost).dropna()
            if len(r) < min_train_events:
                continue
            score = (tr.assign(r=r).groupby("date")["r"].mean()).mean()
            if best_score is None or score > best_score:
                best, best_score = name, score
        if best is not None:
            te = events[best]
            part = te[(te["date"] > tr_end) & (te["date"] <= te_end)]
            oos.append(part)
            chosen.append({"from": str(pd.Timestamp(dates[i]).date()), "to": str(pd.Timestamp(te_end).date()),
                           "variant": best, "in_sample_mean": float(best_score), "oos_events": int(len(part))})
        i += test_days
    stitched = pd.concat(oos) if oos else pd.DataFrame(columns=["date", "symbol"] + [f"fwd_{x}" for x in rules["horizons"]])
    summary = summarize(stitched, rules["horizons"], cost, direction, rules["subperiods"])
    ok, why = passes(summary, rules)
    summary.update(passed=ok, verdict=why, windows=chosen)
    return summary
