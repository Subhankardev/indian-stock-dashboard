"""Download NSE daily files into the database.

    python -m intel.ingest --from 2023-01-01               # backfill cm + indices (Phase 1)
    python -m intel.ingest --days 10 --datasets cm,indices,fo,participant_oi
    python -m intel.ingest --lists                         # Nifty 50/100/200/500 constituents
    python -m intel.ingest --today                         # daily job: files + FII/DII + bulk/block

Already-loaded days are skipped (use --force to reload). Weekday 404s are recorded as
holidays. Every load is recorded in ``source_files`` with URL, time, hash and row count.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from typing import Callable, Iterable, Optional

from sqlalchemy import select

from . import db, parsers
from .sources import DATASETS, DEALS_TODAY, FII_DII_API, INDEX_LISTS, Fetcher, sha256

TABLE_FOR = {
    "cm": (db.prices_daily, lambda raw: parsers.cm_delivery(raw)),
    "indices": (db.index_daily, parsers.index_close),
    "fo": (db.fo_daily, parsers.fo_udiff),
    "participant_oi": (db.participant_oi, parsers.participant_oi),
}


def weekdays(start: date, end: date) -> Iterable[date]:
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def _loaded(eng, dataset: str, day: date) -> bool:
    with eng.connect() as c:
        row = c.execute(select(db.source_files.c.status).where(
            db.source_files.c.dataset == dataset, db.source_files.c.as_of == day)).first()
    return bool(row and row[0] in ("ok", "holiday", "empty"))


def load_day(eng, fetcher: Fetcher, dataset: str, day: date, force: bool = False) -> str:
    """Fetch, parse and store one dataset for one day. Returns the status."""
    if not force and _loaded(eng, dataset, day):
        return "skipped"
    spec = DATASETS[dataset]
    table, parse = TABLE_FOR[dataset]
    url = spec.url(day)
    try:
        raw = fetcher.get(url, cache_as=f"{dataset}/{spec.filename(day)}")
    except Exception as exc:
        db.record_source(eng, dataset, day, url, None, 0, "error", str(exc)[:500])
        return "error"
    if raw is None:
        db.record_source(eng, dataset, day, url, None, 0, "holiday")
        return "holiday"
    try:
        df = parse(raw)
    except Exception as exc:
        db.record_source(eng, dataset, day, url, sha256(raw), 0, "error", f"parse: {exc}"[:500])
        return "error"
    src = db.record_source(eng, dataset, day, url, sha256(raw), len(df), "ok" if len(df) else "empty")
    df = df.assign(source_id=src)
    if "date" in df.columns:
        df = df[df["date"] == day] if (df["date"] == day).any() else df
    if dataset == "fo":
        from .config import load
        from .derivatives import store_day
        store_day(eng, df, src)  # per-underlying summary (futures OI, basis, PCR, ATM IV)
        if not load()["nse"].get("store_option_rows"):
            df = df[df["instrument"].isin(["STF", "IDF"])]
    db.replace_rows(eng, table, df, [table.c.date == day])
    return "ok" if len(df) else "empty"


def load_index_lists(eng, fetcher: Fetcher, names: Optional[list] = None) -> dict:
    out = {}
    today = date.today()
    for name in names or list(INDEX_LISTS):
        url = INDEX_LISTS[name]
        raw = fetcher.get(url, cache_as=f"lists/{name}_{today:%Y%m%d}.csv")
        if raw is None:
            out[name] = 0
            continue
        df = parsers.index_list(raw)
        src = db.record_source(eng, f"list_{name}", today, url, sha256(raw), len(df), "ok")
        df = df.assign(index_name=name, as_of=today, source_id=src)
        db.replace_rows(eng, db.index_members, df,
                        [db.index_members.c.index_name == name, db.index_members.c.as_of == today])
        out[name] = len(df)
    return out


def load_deals_today(eng, fetcher: Fetcher) -> dict:
    """Today's bulk/block files (NSE only publishes the current day here — store them daily)."""
    out = {}
    for kind, url in DEALS_TODAY.items():
        raw = fetcher.get(url, use_cache=False)
        if raw is None:
            continue
        df = parsers.deals(raw, kind)
        day = df["date"].iloc[0] if len(df) else date.today()
        src = db.record_source(eng, f"deals_{kind}", day, url, sha256(raw), len(df), "ok" if len(df) else "empty")
        db.replace_rows(eng, db.deals, df.assign(source_id=src),
                        [db.deals.c.date == day, db.deals.c.kind == kind])
        out[kind] = len(df)
    return out


def load_fii_dii(eng, fetcher: Fetcher) -> int:
    rows = fetcher.get_json(FII_DII_API)
    df = parsers.fii_dii(rows if isinstance(rows, list) else rows.get("data", []))
    if df.empty:
        return 0
    day = df["date"].iloc[0]
    src = db.record_source(eng, "fii_dii", day, FII_DII_API, None, len(df), "ok")
    db.replace_rows(eng, db.fii_dii_daily, df.assign(source_id=src), [db.fii_dii_daily.c.date == day])
    return len(df)


def backfill(eng, fetcher: Fetcher, datasets: list, start: date, end: date, force: bool = False,
             progress: Optional[Callable[[str, date, str], None]] = None) -> dict:
    counts: dict = {}
    for day in weekdays(start, end):
        for ds in datasets:
            st = load_day(eng, fetcher, ds, day, force)
            counts.setdefault(ds, {}).setdefault(st, 0)
            counts[ds][st] += 1
            if progress:
                progress(ds, day, st)
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", help="YYYY-MM-DD")
    ap.add_argument("--to", dest="end", help="YYYY-MM-DD (default today)")
    ap.add_argument("--days", type=int, help="last N calendar days")
    ap.add_argument("--datasets", default="cm,indices", help=f"comma list from {','.join(DATASETS)}")
    ap.add_argument("--lists", action="store_true", help="download index constituent lists")
    ap.add_argument("--today", action="store_true", help="daily job: last 5 days of all files + FII/DII + deals")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    eng = db.engine()
    f = Fetcher()
    end = date.fromisoformat(args.end) if args.end else date.today()
    if args.today:
        args.days, args.datasets, args.lists = 5, ",".join(DATASETS), True
    start = (date.fromisoformat(args.start) if args.start
             else end - timedelta(days=args.days or 0) if args.days else None)
    if args.lists:
        print("Index lists:", load_index_lists(eng, f))
    if start:
        datasets = [d.strip() for d in args.datasets.split(",") if d.strip()]
        unknown = set(datasets) - set(DATASETS)
        if unknown:
            print(f"Unknown datasets {unknown}; choose from {list(DATASETS)}")
            return 2
        t0, n = time.time(), [0]

        def progress(ds, day, st):
            n[0] += 1
            if st not in ("skipped",):
                sys.stdout.write(f"\r  {day} {ds:<15} {st:<8} ({n[0]} checked, {time.time() - t0:.0f}s)   ")
                sys.stdout.flush()

        print(f"Loading {datasets} from {start} to {end}...")
        counts = backfill(eng, f, datasets, start, end, args.force, progress)
        print("\n", counts)
    if args.today:
        try:
            print("FII/DII rows:", load_fii_dii(eng, f))
        except Exception as exc:
            print("FII/DII failed:", exc)
        try:
            print("Deals:", load_deals_today(eng, f))
        except Exception as exc:
            print("Deals failed:", exc)
    print(db.dataset_freshness(eng).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
