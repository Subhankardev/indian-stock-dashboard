"""Tune the Wave · GTI · OI rules on real data and compare backtests.

Run from the backend folder (with the virtualenv active):

    python -m app.tune                       # default basket, report only
    python -m app.tune --apply               # ...and use the tuned rules everywhere
    python -m app.tune --symbols NIFTY,RELIANCE,INFY --days 1460
    python -m app.tune --reset               # go back to the default rules

Data comes from Upstox (needs a working token; daily candles are public but
OI history needs the day's token) and is cached in backend/.cache for the day.
The report is written to backend/reports/.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

from app.config import load_settings

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_SYMBOLS = ["NIFTY", "BANKNIFTY", "RELIANCE", "HDFCBANK", "ICICIBANK",
                   "INFY", "TCS", "SBIN", "AXISBANK", "ITC"]


def load_data(symbols: list[str], days: int, source: str) -> dict[str, pd.DataFrame]:
    if source == "synthetic":
        from app.wave_oi import make_synthetic
        return {s: make_synthetic(int(days * 250 / 365), seed=i + 1) for i, s in enumerate(symbols)}

    from app import upstox_data
    cache = BACKEND / ".cache"
    cache.mkdir(exist_ok=True)
    out = {}
    for s in symbols:
        f = cache / f"{s}_{days}_{date.today():%Y%m%d}.pkl"
        if f.exists():
            out[s] = pd.read_pickle(f)
            print(f"  {s:<11} {len(out[s]):>4} bars (cached)")
            continue
        try:
            df, warning = upstox_data.get_price_oi_history(s, days)
        except Exception as exc:
            print(f"  {s:<11} skipped: {exc}")
            continue
        df.to_pickle(f)
        out[s] = df
        oi = "with OI" if "open_interest" in df and df["open_interest"].notna().any() else "no OI"
        print(f"  {s:<11} {len(df):>4} bars, {oi}" + (f"  ({warning})" if warning else ""))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS), help="comma-separated NSE symbols")
    ap.add_argument("--days", type=int, default=1095, help="calendar days of history (default ~3 years)")
    ap.add_argument("--train", type=float, default=2 / 3, help="fraction of history used to choose settings")
    ap.add_argument("--min-trades", type=int, default=15, help="minimum pooled in-sample trades per setting")
    ap.add_argument("--apply", action="store_true", help="save the tuned rules to backend/strategy.json")
    ap.add_argument("--reset", action="store_true", help="delete backend/strategy.json (back to defaults)")
    ap.add_argument("--source", choices=["upstox", "synthetic"], default="upstox", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    from app.wave_oi import tuning

    if args.reset:
        if tuning.RULES_FILE.exists():
            tuning.RULES_FILE.unlink()
            print("Removed backend/strategy.json - the default rules are active. Restart the backend.")
        else:
            print("Already using the default rules.")
        return 0

    load_settings()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    print(f"Loading {args.days} days of daily data for {len(symbols)} symbols...")
    dfs = load_data(symbols, args.days, args.source)
    if not dfs:
        print("No data loaded - check the backend can reach Upstox (open the dashboard first).")
        return 1

    n = len(tuning.combos())
    print(f"\nTesting {n} rule combinations on {len(dfs)} symbols (about {n * len(dfs) * 0.015:.0f}s)...")
    t0 = time.time()

    def progress(i, total):
        if i == total or i % 25 == 0:
            bar = "#" * int(30 * i / total)
            sys.stdout.write(f"\r  [{bar:<30}] {i}/{total}")
            sys.stdout.flush()

    try:
        result = tuning.tune(dfs, train_frac=args.train, min_trades=args.min_trades, progress=progress)
    except ValueError as exc:
        print(f"\n{exc}")
        return 1
    print(f"\n  done in {time.time() - t0:.0f}s\n")

    reports = BACKEND / "reports"
    reports.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M")
    md = reports / f"tuning-{stamp}.md"
    md.write_text(tuning.render_report(result))
    result["table"].to_csv(reports / f"tuning-{stamp}.csv", index=False)

    b, d = result["best_row"], result["default_row"]
    fmt = lambda v, p=2: "–" if v is None else f"{v:.{p}f}"  # noqa: E731
    print("Out-of-sample (the honest numbers):")
    print(f"  default rules: {d['oos_trades']:>3} trades, avg R {fmt(d['oos_avg_r'])}, total R {fmt(d['oos_total_r'], 1)}")
    print(f"  tuned rules:   {b['oos_trades']:>3} trades, avg R {fmt(b['oos_avg_r'])}, total R {fmt(b['oos_total_r'], 1)}")
    print(f"\nTuned settings: {result['best']}")
    print(f"Full report: {md}")

    if args.apply:
        path = tuning.save_rules(result["best"], tuning.oos_summary(result))
        print(f"\nSaved to {path}. Restart the backend to use the tuned rules everywhere.")
    else:
        print("\nNothing changed. Re-run with --apply to use the tuned rules.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
