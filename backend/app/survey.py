"""Backtest the Wave · GTI · OI rules on every NIFTY 50 stock and report how
many signals they generate and how often they win.

Run from the backend folder (virtualenv active, logged in to Upstox today):

    python -m app.survey                     # NIFTY 50, ~3 years
    python -m app.survey --days 730          # ~2 years
    python -m app.survey --symbols RELIANCE,TCS,INFY

For each stock: signals generated (BUY / SELL), trades taken (one position
at a time, so fewer than signals), wins, win rate, average R, total R,
profit factor and buy & hold over the same period. Then all stocks pooled,
with a 95% confidence range for the win rate and the average R, and the same
pooled numbers for simpler variants of the rules (to see which part helps).

R = profit after fees / amount risked on the trade. Uses the active rules
(backend/strategy.json if you applied tuned rules, else the defaults).
"""
from __future__ import annotations

import argparse
import io
import math
import os
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from app.config import load_settings

BACKEND = Path(__file__).resolve().parents[1]
NIFTY50_CSV = "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv"

# Fallback if NSE's list can't be downloaded. NIFTY 50 changes a few times a
# year, so the downloaded list is preferred; unknown symbols are just skipped.
NIFTY50_FALLBACK = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO", "BAJFINANCE",
    "BAJAJFINSV", "BEL", "BHARTIARTL", "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDIGO",
    "INFY", "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "M&M", "MARUTI", "MAXHEALTH",
    "NESTLEIND", "NTPC", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN",
    "SUNPHARMA", "TATACONSUM", "TMPV", "TATASTEEL", "TCS", "TECHM", "TITAN", "TRENT",
    "ULTRACEMCO", "WIPRO",
]

VARIANTS = {
    "A. Current rules (long & short)": {},
    "B. Long only": {"allow_short": False},
    "C. Long only + 200-day trend": {"allow_short": False, "trend_filter_ma": 200},
    "D. Long + 200-day trend + trailing exit": {"allow_short": False, "trend_filter_ma": 200, "exit_mode": "trail"},
    "E. Plain trend-following (no wave/OI): long + 200-day + trailing": {
        "use_wave": False, "use_oi": False, "allow_short": False, "trend_filter_ma": 200, "exit_mode": "trail"},
    "F. GTI trend only (long & short)": {"use_wave": False, "use_oi": False},
}
MAX_DAILY_MOVE = 0.35  # a bigger one-day move is almost always an unadjusted split/demerger


# --------------------------------------------------------------------------- #
# Universe
# --------------------------------------------------------------------------- #
def parse_index_csv(text: str) -> list[str]:
    df = pd.read_csv(io.StringIO(text))
    col = next((c for c in df.columns if c.strip().lower() == "symbol"), None)
    if col is None:
        raise ValueError("no Symbol column")
    return [str(s).strip().upper() for s in df[col].dropna() if str(s).strip()]


def nifty50(session=None) -> tuple[list[str], str]:
    """(symbols, where the list came from)."""
    import requests
    session = session or requests.Session()
    try:
        r = session.get(NIFTY50_CSV, timeout=15, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"})
        if r.status_code == 200:
            syms = parse_index_csv(r.text)
            if 40 <= len(syms) <= 60:
                return syms, "NSE index file (current)"
    except Exception:
        pass
    return list(NIFTY50_FALLBACK), "built-in list (NSE file unavailable - may be slightly out of date)"


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #
def wilson(wins: int, n: int, z: float = 1.96) -> tuple[Optional[float], Optional[float]]:
    """95% confidence range for a win rate (Wilson score interval)."""
    if n == 0:
        return None, None
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def mean_ci(values: list[float], z: float = 1.96) -> tuple[Optional[float], Optional[float]]:
    if len(values) < 2:
        return None, None
    m, sd = float(np.mean(values)), float(np.std(values, ddof=1))
    half = z * sd / math.sqrt(len(values))
    return m - half, m + half


# --------------------------------------------------------------------------- #
# Running
# --------------------------------------------------------------------------- #
def run_symbol(df: pd.DataFrame, params: dict) -> dict:
    from app.wave_oi import analyze, run_backtest
    from app.wave_oi.tuning import net_r, split_params

    scfg, bcfg = split_params(params)
    an, _ = analyze(df, scfg)
    trades = run_backtest(an, bcfg)["trades"]
    rs = [net_r(t) for t in trades]
    dates = [pd.Timestamp(t["entry_date"]) for t in trades]
    wins = sum(r > 0 for r in rs)
    losses = [r for r in rs if r <= 0]
    years = max((df.index[-1] - df.index[0]).days / 365.25, 1e-9)
    return {
        "bars": len(df),
        "from": df.index[0].strftime("%Y-%m-%d"), "to": df.index[-1].strftime("%Y-%m-%d"),
        "buy_signals": int((an["signal"] == 1).sum()), "sell_signals": int((an["signal"] == -1).sum()),
        "signals_per_year": float((an["signal"] != 0).sum() / years),
        "trades": len(rs), "wins": int(wins),
        "win_rate": wins / len(rs) if rs else None,
        "avg_r": float(np.mean(rs)) if rs else None,
        "total_r": float(np.sum(rs)) if rs else 0.0,
        "profit_factor": (sum(r for r in rs if r > 0) / -sum(losses)) if losses and sum(losses) < 0 else None,
        "best_r": max(rs) if rs else None, "worst_r": min(rs) if rs else None,
        "buy_hold_pct": float(df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100,
        "rs": rs, "dates": dates,
        "mid": df.index[0] + (df.index[-1] - df.index[0]) / 2,
    }


def pooled(rows: list[dict]) -> dict:
    rs = [r for row in rows for r in row["rs"]]
    wins = sum(r > 0 for r in rs)
    lo, hi = wilson(wins, len(rs))
    mlo, mhi = mean_ci(rs)
    losses = [r for r in rs if r <= 0]
    return {
        "stocks": len(rows),
        "signals": sum(r["buy_signals"] + r["sell_signals"] for r in rows),
        "buy_signals": sum(r["buy_signals"] for r in rows),
        "sell_signals": sum(r["sell_signals"] for r in rows),
        "trades": len(rs), "wins": wins,
        "win_rate": wins / len(rs) if rs else None, "win_rate_ci": (lo, hi),
        "avg_r": float(np.mean(rs)) if rs else None, "avg_r_ci": (mlo, mhi),
        "total_r": float(np.sum(rs)) if rs else 0.0,
        "profit_factor": (sum(r for r in rs if r > 0) / -sum(losses)) if losses and sum(losses) < 0 else None,
        "stocks_positive": sum(1 for r in rows if r["total_r"] > 0),
        "stocks_no_trades": sum(1 for r in rows if r["trades"] == 0),
        "median_buy_hold_pct": float(np.median([r["buy_hold_pct"] for r in rows])) if rows else None,
        **_halves(rows),
    }


def _halves(rows: list[dict]) -> dict:
    """Average R in the first vs second half of each stock's period - a real edge should show in both."""
    first = [r for row in rows for r, d in zip(row["rs"], row["dates"]) if d < row["mid"]]
    second = [r for row in rows for r, d in zip(row["rs"], row["dates"]) if d >= row["mid"]]
    return {"first_half_trades": len(first), "second_half_trades": len(second),
            "first_half_avg_r": float(np.mean(first)) if first else None,
            "second_half_avg_r": float(np.mean(second)) if second else None}


def passes(v: dict, min_trades: int = 50) -> bool:
    """Pass = enough trades, average R's 95% range above zero, and positive in BOTH halves."""
    lo, _ = v["avg_r_ci"]
    return (v["trades"] >= min_trades and lo is not None and lo > 0
            and (v["first_half_avg_r"] or 0) > 0 and (v["second_half_avg_r"] or 0) > 0)


def corporate_action_suspects(dfs: dict[str, pd.DataFrame]) -> dict[str, str]:
    """Stocks whose history has a one-day move over 35% - nearly always an unadjusted split/demerger."""
    out = {}
    for s, df in dfs.items():
        ret = df["close"].pct_change().abs()
        if len(ret.dropna()) and ret.max() > MAX_DAILY_MOVE:
            day = ret.idxmax()
            out[s] = (f"excluded: {ret.max():.0%} one-day move on {day:%Y-%m-%d} "
                      "(likely an unadjusted split/demerger)")
    return out


def survey(dfs: dict[str, pd.DataFrame], params: dict) -> dict:
    per_symbol = {s: run_symbol(df, params) for s, df in dfs.items()}
    variants = {}
    for name, override in VARIANTS.items():
        rows = per_symbol.values() if not override else [run_symbol(df, {**params, **override}) for df in dfs.values()]
        variants[name] = pooled(list(rows))
    return {"per_symbol": per_symbol, "total": variants[next(iter(VARIANTS))], "variants": variants}


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def _p(v, d=0):
    return "–" if v is None else f"{v * 100:.{d}f}%"


def _n(v, d=2):
    return "–" if v is None else f"{v:.{d}f}"


def verdict(t: dict) -> str:
    lo, hi = t["avg_r_ci"]
    if t["trades"] < 30:
        return (f"Only {t['trades']} trades across all stocks - still too few to judge. "
                "The rules are very selective.")
    if lo is not None and lo > 0:
        return (f"Positive edge: average {t['avg_r']:.2f}R per trade, and the 95% range "
                f"({lo:.2f}R to {hi:.2f}R) stays above zero.")
    if hi is not None and hi < 0:
        return (f"Negative edge: average {t['avg_r']:.2f}R per trade, and the 95% range "
                f"({lo:.2f}R to {hi:.2f}R) stays below zero - these rules lose money.")
    return (f"No clear edge: average {t['avg_r']:.2f}R per trade, but the 95% range "
            f"({_n(lo)}R to {_n(hi)}R) includes zero - can't tell it apart from luck.")


def render(result: dict, universe_note: str, rules: dict, skipped: dict) -> str:
    t = result["total"]
    lo, hi = t["win_rate_ci"]
    lines = [
        f"# NIFTY 50 signal survey — {time.strftime('%d %b %Y %H:%M')}",
        "",
        f"Universe: {universe_note}. Rules: {rules['source']}"
        + (f" ({rules['tuned_at']})" if rules.get("tuned_at") else "") + ".",
        "",
        "## Overall (all stocks pooled)",
        "",
        f"- **Signals generated:** {t['signals']} ({t['buy_signals']} BUY, {t['sell_signals']} SELL) "
        f"across {t['stocks']} stocks",
        f"- **Trades taken:** {t['trades']} (one position at a time per stock)",
        f"- **Win rate:** {_p(t['win_rate'])} — 95% range {_p(lo)} to {_p(hi)}",
        f"- **Average per trade:** {_n(t['avg_r'])}R · total {_n(t['total_r'], 1)}R · "
        f"profit factor {_n(t['profit_factor'])}",
        f"- **Stocks where the rules made money:** {t['stocks_positive']} of {t['stocks']} "
        f"({t['stocks_no_trades']} had no trades)",
        f"- **Median buy & hold** over the same period: {t['median_buy_hold_pct']:+.1f}%",
        "",
        f"**Verdict:** {verdict(t)}",
        "",
        "## Rule variants (same stocks, same period)",
        "",
        "A rule set **passes** only with 50+ trades, a 95% range for average R entirely above 0, "
        "and a positive average R in **both** halves of the period.",
        "",
        "| Rules | Signals | Trades | Win rate | Avg R | 95% range | 1st half avg R | 2nd half avg R | Total R | Passes? |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, v in result["variants"].items():
        a, b = v["avg_r_ci"]
        lines.append(f"| {name} | {v['signals']} | {v['trades']} | {_p(v['win_rate'])} | {_n(v['avg_r'])} | "
                     f"{_n(a)} to {_n(b)} | {_n(v['first_half_avg_r'])} | {_n(v['second_half_avg_r'])} | "
                     f"{_n(v['total_r'], 1)} | {'✅ yes' if passes(v) else '❌ no'} |")
    winners = [n for n, v in result["variants"].items() if passes(v)]
    lines += [
        "",
        "**Result:** " + (
            f"{', '.join(winners)} passed. Before trading it, confirm with `python -m app.tune` "
            "(walk-forward) and paper-trade it first." if winners else
            "no rule set passed - none of these versions shows a reliable edge on this data. "
            "Keep the alerts paused."),
        "",
        "## By stock (rule set A: current rules)",
        "",
        "| Stock | Period | BUY sig. | SELL sig. | Signals/yr | Trades | Wins | Win rate | Avg R | Total R | Best R | Worst R | Buy & hold |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    rows = sorted(result["per_symbol"].items(), key=lambda kv: -kv[1]["total_r"])
    for s, r in rows:
        lines.append(
            f"| {s} | {r['from']} → {r['to']} | {r['buy_signals']} | {r['sell_signals']} | "
            f"{r['signals_per_year']:.1f} | {r['trades']} | {r['wins']} | {_p(r['win_rate'])} | "
            f"{_n(r['avg_r'])} | {_n(r['total_r'], 1)} | {_n(r['best_r'])} | {_n(r['worst_r'])} | "
            f"{r['buy_hold_pct']:+.1f}% |")
    if skipped:
        lines += ["", "## Skipped", ""] + [f"- {s}: {why}" for s, why in skipped.items()]
    lines += [
        "", "## How to read this", "",
        "- **Signals** are days the rules fired; **trades** are what a single position per stock "
        "could actually take (a signal while already in a trade is ignored).",
        "- **R** = profit after fees ÷ amount risked. Win rate alone can mislead: a 35% win rate "
        "with 2.5R winners makes money; a 60% win rate with small winners can lose.",
        "- The **95% range** shows how much the result could be luck. If the range for average R "
        "includes 0, the rules haven't shown a real edge.",
        "- This is an in-sample backtest of the whole period with the active rules; for an honest "
        "estimate of tuned settings see `python -m app.tune` (walk-forward).", "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", help="comma-separated symbols (default: the NIFTY 50)")
    ap.add_argument("--days", type=int, default=1095,
                    help="calendar days of history (default ~3 years, so the 200-day average has time to warm up)")
    ap.add_argument("--max-expired", type=int, default=12,
                    help="expired monthly futures to fetch per stock for OI history (default 12)")
    ap.add_argument("--source", choices=["upstox", "synthetic"], default="upstox", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    from app.tune import load_data
    from app.wave_oi.tuning import load_rules

    load_settings()
    os.environ.setdefault("UPSTOX_MAX_EXPIRED", str(args.max_expired))
    if args.symbols:
        symbols, note = [s.strip().upper() for s in args.symbols.split(",") if s.strip()], "custom list"
    elif args.source == "synthetic":
        symbols, note = NIFTY50_FALLBACK[:6], "synthetic test data"
    else:
        symbols, note = nifty50()
    rules = load_rules()
    print(f"{len(symbols)} symbols from the {note}. Rules: {rules['source']}.")
    print(f"Loading {args.days} days of history (paced to respect Upstox limits; cached for today)...")
    t0 = time.time()
    dfs = load_data(symbols, args.days, args.source)
    skipped = {s: "no data (see message above)" for s in symbols if s not in dfs}
    dfs = {s: d for s, d in dfs.items() if len(d) >= 120}
    suspects = corporate_action_suspects(dfs)
    for sym, why in suspects.items():
        print(f"  {sym}: {why}")
        dfs.pop(sym)
    skipped.update(suspects)
    if not dfs:
        print("No usable data - is the backend logged in to Upstox today?")
        return 1
    print(f"  loaded {len(dfs)} stocks in {time.time() - t0:.0f}s\n")

    result = survey(dfs, rules["params"])
    t = result["total"]
    lo, hi = t["win_rate_ci"]
    print(f"Signals generated: {t['signals']}  ({t['buy_signals']} BUY / {t['sell_signals']} SELL) across {t['stocks']} stocks")
    print(f"Trades taken:      {t['trades']}   wins {t['wins']}   win rate {_p(t['win_rate'])} (95% range {_p(lo)}-{_p(hi)})")
    print(f"Average per trade: {_n(t['avg_r'])}R   total {_n(t['total_r'], 1)}R   "
          f"stocks in profit {t['stocks_positive']}/{t['stocks']}")
    print(f"\nVerdict: {verdict(t)}\n")
    print(f"{'Rules':<66}{'Trades':>7}{'Win':>6}{'Avg R':>7}{'1st half':>9}{'2nd half':>9}  Passes?")
    for name, v in result["variants"].items():
        print(f"{name:<66}{v['trades']:>7}{_p(v['win_rate']):>6}{_n(v['avg_r']):>7}"
              f"{_n(v['first_half_avg_r']):>9}{_n(v['second_half_avg_r']):>9}  {'YES' if passes(v) else 'no'}")
    winners = [n for n, v in result["variants"].items() if passes(v)]
    print("\n" + (f"Passed: {', '.join(winners)}" if winners else
                  "No rule set passed (needs 50+ trades, 95% range above 0, positive in both halves). "
                  "Keep alerts paused."))

    reports = BACKEND / "reports"
    reports.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M")
    md = reports / f"survey-{stamp}.md"
    md.write_text(render(result, note, rules, skipped))
    pd.DataFrame([{"symbol": s, **{k: v for k, v in r.items() if k != "rs"}}
                  for s, r in result["per_symbol"].items()]).to_csv(reports / f"survey-{stamp}.csv", index=False)
    print(f"\nFull report (every stock): {md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
