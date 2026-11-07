"""Walk-forward tuning of the Wave · GTI · OI rules.

For every combination in ``PARAM_GRID`` the full strategy + backtest runs on
each symbol. Trades are split by entry date:

* **in-sample** - the older part of each symbol's history (``train_frac``),
  used to *choose* the settings;
* **out-of-sample** - the most recent part, never looked at while choosing.
  Only these numbers estimate how the chosen settings might do going forward.

Everything is measured in **R** (profit / amount risked per trade, after fees),
so trades on NIFTY and on a ₹500 stock count the same. Combinations are ranked
by an SQN-style score, ``mean(R) / std(R) * sqrt(n)``, on pooled in-sample
trades - it rewards a consistent edge over many trades rather than one lucky
trade - and a combination needs at least ``min_trades`` trades to count.

The chosen settings can be saved to ``backend/strategy.json``; the dashboard,
backtests and alert scanner then use them (see ``load_rules``).
"""
from __future__ import annotations

import itertools
import json
import math
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np
import pandas as pd

from .backtest import BacktestConfig, run_backtest
from .strategy import StrategyConfig, analyze

RULES_FILE = Path(__file__).resolve().parents[2] / "strategy.json"

# Strategy settings + one backtest setting (exit_on_gti_flip).
PARAM_GRID: dict[str, list] = {
    "zz_atr_mult": [1.5, 2.0, 2.5, 3.0, 3.5],
    "gti_mult": [2.0, 2.5, 3.5],
    "min_rr": [1.0, 1.5, 2.5],
    "stop_mode": ["pivot", "invalidation"],
    "use_oi": [True, False],
    "trade_corrections": [False, True],
    "exit_on_gti_flip": [True, False],
}
BACKTEST_KEYS = {"exit_on_gti_flip", "exit_mode"}
DEFAULTS = {**{k: getattr(StrategyConfig(), k) for k in PARAM_GRID if k not in BACKTEST_KEYS},
            "exit_on_gti_flip": BacktestConfig().exit_on_gti_flip}


# --------------------------------------------------------------------------- #
# Settings <-> configs
# --------------------------------------------------------------------------- #
def split_params(params: dict) -> tuple[StrategyConfig, BacktestConfig]:
    strat_fields = {f.name for f in fields(StrategyConfig)}
    s = {k: v for k, v in params.items() if k in strat_fields}
    b = {k: v for k, v in params.items() if k in BACKTEST_KEYS}
    return StrategyConfig(**s), BacktestConfig(**b)


def load_rules(path: Path | str | None = None) -> dict:
    """Active rule settings: defaults, overridden by backend/strategy.json if present."""
    path = Path(path) if path is not None else RULES_FILE
    out = {"params": dict(DEFAULTS), "source": "defaults", "tuned_at": None, "summary": None}
    if path.is_file():
        try:
            saved = json.loads(path.read_text())
            params = {k: v for k, v in saved.get("params", {}).items() if k in DEFAULTS}
            out.update(params={**DEFAULTS, **params}, source="tuned",
                       tuned_at=saved.get("tuned_at"), summary=saved.get("summary"))
        except (OSError, ValueError, TypeError):
            out["source"] = "defaults (strategy.json unreadable)"
    return out


def save_rules(params: dict, summary: dict, path: Path | str | None = None) -> Path:
    path = Path(path) if path is not None else RULES_FILE
    path.write_text(json.dumps({
        "params": {k: params[k] for k in DEFAULTS},
        "tuned_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
    }, indent=2))
    return path


# --------------------------------------------------------------------------- #
# Metrics (in R)
# --------------------------------------------------------------------------- #
def net_r(trade: dict) -> float:
    risk = trade["qty"] * abs(trade["entry"] - trade["stop"])
    return trade["pnl"] / risk if risk > 0 else 0.0


def r_stats(rs: Iterable[float]) -> dict:
    r = np.asarray(list(rs), dtype=float)
    n = len(r)
    if n == 0:
        return {"trades": 0, "win_rate": None, "avg_r": None, "total_r": 0.0,
                "profit_factor": None, "max_dd_r": 0.0, "sqn": None}
    wins, losses = r[r > 0], r[r <= 0]
    curve = np.cumsum(r)
    dd = float((curve - np.maximum.accumulate(np.concatenate([[0.0], curve]))[1:]).min())
    sd = r.std(ddof=1) if n > 1 else 0.0
    return {
        "trades": n,
        "win_rate": float(len(wins) / n),
        "avg_r": float(r.mean()),
        "total_r": float(r.sum()),
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else None,
        "max_dd_r": min(dd, 0.0),
        "sqn": float(r.mean() / sd * math.sqrt(n)) if sd > 0 else None,
    }


def score(stats: dict, min_trades: int) -> float:
    if stats["trades"] < min_trades or stats["sqn"] is None:
        return float("-inf")
    return stats["sqn"]


# --------------------------------------------------------------------------- #
# Running the grid
# --------------------------------------------------------------------------- #
def combos(grid: Optional[dict] = None) -> list[dict]:
    grid = grid if grid is not None else PARAM_GRID  # looked up at call time
    keys = list(grid)
    return [dict(zip(keys, vals)) for vals in itertools.product(*(grid[k] for k in keys))]


def split_date(df: pd.DataFrame, train_frac: float) -> pd.Timestamp:
    return df.index[min(len(df) - 1, int(len(df) * train_frac))]


def run_params(dfs: dict[str, pd.DataFrame], params: dict, splits: dict[str, pd.Timestamp]) -> dict:
    """Trades (net R) per symbol, split in-sample / out-of-sample by entry date."""
    scfg, bcfg = split_params(params)
    out = {}
    for sym, df in dfs.items():
        an, _ = analyze(df, scfg)
        trades = run_backtest(an, bcfg)["trades"]
        cut = splits[sym]
        ins = [net_r(t) for t in trades if pd.Timestamp(t["entry_date"]) < cut]
        oos = [net_r(t) for t in trades if pd.Timestamp(t["entry_date"]) >= cut]
        out[sym] = {"is": ins, "oos": oos}
    return out


def tune(
    dfs: dict[str, pd.DataFrame],
    grid: Optional[dict] = None,
    train_frac: float = 2 / 3,
    min_trades: int = 15,
    progress: Optional[Callable[[int, int], None]] = None,
) -> dict:
    grid = grid if grid is not None else PARAM_GRID
    dfs = {s: d.sort_index() for s, d in dfs.items() if len(d) >= 150}
    if not dfs:
        raise ValueError("Need at least one symbol with 150+ daily bars")
    splits = {s: split_date(d, train_frac) for s, d in dfs.items()}
    all_combos = combos(grid)
    if DEFAULTS not in all_combos:
        all_combos.append(dict(DEFAULTS))
    rows, per_symbol = [], {}
    for i, params in enumerate(all_combos):
        res = run_params(dfs, params, splits)
        is_stats = r_stats(r for s in res.values() for r in s["is"])
        oos_stats = r_stats(r for s in res.values() for r in s["oos"])
        key = json.dumps(params, sort_keys=True)
        per_symbol[key] = res
        rows.append({**params, "score": score(is_stats, min_trades),
                     **{f"is_{k}": v for k, v in is_stats.items()},
                     **{f"oos_{k}": v for k, v in oos_stats.items()}})
        if progress:
            progress(i + 1, len(all_combos))

    table = pd.DataFrame(rows).sort_values(["score", "is_total_r"], ascending=False).reset_index(drop=True)
    valid = table[np.isfinite(table["score"])]
    if valid.empty:
        raise ValueError(f"No setting produced {min_trades}+ in-sample trades; add symbols or more history")
    best = {k: _py(valid.iloc[0][k]) for k in grid}
    default_row = table[(table[list(grid)] == pd.Series(DEFAULTS)).all(axis=1)].iloc[0]

    def symbol_rows(params):
        res = per_symbol[json.dumps(params, sort_keys=True)]
        out = []
        for sym, df in dfs.items():
            cut = splits[sym]
            close = df["close"]
            bh = float(close.iloc[-1] / close.loc[cut] - 1) * 100
            st = r_stats(res[sym]["oos"])
            out.append({"symbol": sym, "oos_from": cut.strftime("%Y-%m-%d"), **st, "buy_hold_pct": bh})
        return out

    top10 = valid.head(10)
    return {
        "symbols": list(dfs),
        "bars": {s: len(d) for s, d in dfs.items()},
        "splits": {s: c.strftime("%Y-%m-%d") for s, c in splits.items()},
        "train_frac": train_frac,
        "min_trades": min_trades,
        "combos_tested": len(all_combos),
        "combos_valid": int(len(valid)),
        "best": best,
        "best_row": {k: _py(v) for k, v in valid.iloc[0].items()},
        "default_row": {k: _py(v) for k, v in default_row.items()},
        "best_by_symbol": symbol_rows(best),
        "default_by_symbol": symbol_rows({k: _py(default_row[k]) for k in grid}),
        "top10_oos_median_avg_r": _py(top10["oos_avg_r"].median()),
        "best_oos_rank": int((valid["oos_total_r"] > valid.iloc[0]["oos_total_r"]).sum() + 1),
        "table": table,
    }


def _py(v):
    if isinstance(v, (np.generic,)):
        v = v.item()
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def _f(v, d=2, pct=False):
    if v is None:
        return "–"
    return f"{v * 100:.0f}%" if pct else f"{v:.{d}f}"


def render_report(result: dict) -> str:
    b, d = result["best_row"], result["default_row"]
    lines = [
        f"# Strategy tuning report — {datetime.now():%d %b %Y %H:%M}",
        "",
        f"Symbols: {', '.join(result['symbols'])}  ",
        f"Combinations tested: {result['combos_tested']} ({result['combos_valid']} had ≥{result['min_trades']} in-sample trades)  ",
        f"Walk-forward: settings chosen on the first {result['train_frac']:.0%} of each symbol's history; "
        "the rest is out-of-sample (never used for choosing).",
        "",
        "## Default vs tuned rules",
        "",
        "| | Default | Tuned |",
        "|---|---|---|",
    ]
    for k in PARAM_GRID:
        lines.append(f"| `{k}` | {d[k]} | {b[k]} |")
    lines += [
        "",
        "## Results (pooled across symbols, in R = multiples of risk, after fees)",
        "",
        "| | Trades | Win rate | Avg R | Total R | Profit factor | Max DD (R) | SQN |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for label, row, part in (("Default — in-sample", d, "is"), ("Tuned — in-sample", b, "is"),
                             ("**Default — out-of-sample**", d, "oos"), ("**Tuned — out-of-sample**", b, "oos")):
        g = lambda k: row.get(f"{part}_{k}")  # noqa: E731
        lines.append(f"| {label} | {g('trades')} | {_f(g('win_rate'), pct=True)} | {_f(g('avg_r'))} | "
                     f"{_f(g('total_r'), 1)} | {_f(g('profit_factor'))} | {_f(g('max_dd_r'), 1)} | {_f(g('sqn'))} |")
    lines += [
        "",
        f"Out-of-sample rank of the tuned setting among valid combinations: "
        f"{result['best_oos_rank']} of {result['combos_valid']}.  ",
        f"Median out-of-sample avg R of the 10 best in-sample settings: {_f(result['top10_oos_median_avg_r'])}.",
        "",
        "## Tuned rules, out-of-sample by symbol",
        "",
        "| Symbol | OOS from | Trades | Win rate | Avg R | Total R | ≈ Return at 1% risk | Buy & hold |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in result["best_by_symbol"]:
        lines.append(f"| {r['symbol']} | {r['oos_from']} | {r['trades']} | {_f(r['win_rate'], pct=True)} | "
                     f"{_f(r['avg_r'])} | {_f(r['total_r'], 1)} | {r['total_r']:+.1f}% | {r['buy_hold_pct']:+.1f}% |")
    lines += ["", "## Default rules, out-of-sample by symbol", "",
              "| Symbol | Trades | Win rate | Avg R | Total R |", "|---|---|---|---|---|"]
    for r in result["default_by_symbol"]:
        lines.append(f"| {r['symbol']} | {r['trades']} | {_f(r['win_rate'], pct=True)} | {_f(r['avg_r'])} | {_f(r['total_r'], 1)} |")
    lines += ["", "## How to read this", "",
              "- Judge by the **out-of-sample** rows only; in-sample numbers are flattered by the search itself.",
              "- An avg R near 0, few trades, or a tuned setting that ranks poorly out-of-sample all mean "
              "there's no reliable edge yet — keep the alerts as prompts to look, not signals to trade.",
              "- Buy & hold is the bar to beat over the same out-of-sample period. \"≈ Return at 1% risk\" "
              "assumes each trade risks 1% of capital (total R × 1%, ignoring compounding).", ""]
    return "\n".join(lines)


def oos_summary(result: dict) -> dict:
    b, d = result["best_row"], result["default_row"]
    keys = ("trades", "win_rate", "avg_r", "total_r", "sqn")
    return {"symbols": result["symbols"],
            "tuned_oos": {k: b.get(f"oos_{k}") for k in keys},
            "default_oos": {k: d.get(f"oos_{k}") for k in keys}}
