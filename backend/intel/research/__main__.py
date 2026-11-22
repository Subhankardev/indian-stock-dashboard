"""Test signals on stored history.

    python -m intel.research                         # every signal, all history
    python -m intel.research --signal rs_leaders --from 2023-01-01
    python -m intel.research --list

Prints each signal's event study (excess returns vs the benchmark after costs) and the
pass/fail verdict, and records the result in the ``signal_tests`` table.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone

from sqlalchemy import insert

from .. import db
from ..config import load
from ..panel import build_panel, load_index
from . import event_study
from .signals import SIGNALS

RULE_VERSION = "p5.0"


def test_all(eng, cfg, names=None, start=None) -> dict:
    from ..evidence import attach
    panel = attach(eng, build_panel(eng, cfg, start=start))
    if panel.empty:
        raise SystemExit("No price data. Run: python -m intel.ingest --from 2022-01-01")
    rules = cfg["research"]
    bench = load_index(eng, rules["benchmark"]) if rules["benchmark"] else None
    if bench is None or bench.empty:
        bench = load_index(eng, cfg["relative_strength"]["benchmark"])
    results = {}
    for name in names or list(SIGNALS):
        fn, direction, desc = SIGNALS[name]
        res = event_study.run(panel, fn(panel), bench, rules, direction)
        res["description"] = desc
        results[name] = res
        with eng.begin() as c:
            c.execute(insert(db.signal_tests).values(
                signal_name=name, rule_version=RULE_VERSION,
                run_at=datetime.now(timezone.utc).replace(tzinfo=None),
                universe=cfg["universe"]["name"],
                period=f"{res.get('first')}..{res.get('last')}",
                stats=json.dumps(res, default=str), passed=int(res["passed"])))
    return results


def _fmt(v, d=2, pct=False):
    if v is None:
        return "–"
    return f"{v * 100:.0f}%" if pct else f"{v:.{d}f}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--signal", action="append", help="signal name (repeatable); default all")
    ap.add_argument("--from", dest="start")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)
    if args.list:
        for n, (_, d, desc) in SIGNALS.items():
            print(f"{n:<22} {d:<8} {desc}")
        return 0
    cfg = load()
    eng = db.engine()
    res = test_all(eng, cfg, args.signal, date.fromisoformat(args.start) if args.start else None)
    h = cfg["research"]["pass_horizon"]
    print(f"\n{'Signal':<22}{'Dir':<9}{'Events':>7}{'Hit':>6}{f'{h}d excess':>11}{'95% range':>18}  Verdict")
    for name, r in res.items():
        s = r["horizons"].get(h, {})
        ci = s.get("ci95") or [None, None]
        print(f"{name:<22}{r['direction']:<9}{r['events']:>7}{_fmt(s.get('hit_rate'), pct=True):>6}"
              f"{_fmt(s.get('day_mean_pct')):>10}%{(_fmt(ci[0]) + ' to ' + _fmt(ci[1])):>18}  "
              f"{'PASS' if r['passed'] else 'fail'}: {r['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
