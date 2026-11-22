"""CSV imports for data NSE does not serve as a single free file.

    python -m intel.imports deals path/to/bulk_deals.csv --kind bulk     # NSE "Bulk deals" CSV download
    python -m intel.imports shareholding path/to/shareholding.csv
    python -m intel.imports mf path/to/mf_holdings.csv
    python -m intel.imports fii_dii path/to/fii_dii.csv
    python -m intel.imports fundamentals path/to/fundamentals.csv

Formats (header row required; extra columns ignored):

* deals: NSE's own download (Date, Symbol, Security Name, Client Name, Buy/Sell,
  Quantity Traded, Trade Price / Wght. Avg. Price, Remarks).
* shareholding: symbol, quarter_end (YYYY-MM-DD), category, pct[, shares][, published_on]
  category ∈ promoter, fpi, dii, mutual_funds, insurance, public, other. Without
  ``published_on`` the publication date is assumed to be quarter_end + 21 days (the listing
  deadline), so nothing is used before it could have been known.
* mf: month_end, amc, scheme, symbol, shares[, market_value_cr][, published_on]
  (default published_on = month_end + 10 days, the SEBI deadline).
* fii_dii: date, category (FII|DII), buy_value, sell_value, net_value  (₹ crore).
* fundamentals: symbol, period_end, period_type (Q|A), metric, value[, published_on]
  (see ``intel/fundamentals.py`` for metric names and units; default published_on =
  period_end + 45 days for quarters, + 60 days for annual results).

Each import is recorded in ``source_files`` with the file name and sha256.
"""
from __future__ import annotations

import argparse
import hashlib
from datetime import timedelta
from pathlib import Path

import pandas as pd

from . import db, parsers

SH_CATEGORIES = {"promoter", "fpi", "dii", "mutual_funds", "insurance", "public", "other"}


class ImportErrorCSV(ValueError):
    pass


def _read(path) -> tuple[pd.DataFrame, bytes]:
    raw = Path(path).read_bytes()
    df = pd.read_csv(path, dtype=str)
    df.columns = [c.strip().lower() for c in df.columns]
    return df, raw


def _need(df, cols, what):
    miss = [c for c in cols if c not in df.columns]
    if miss:
        raise ImportErrorCSV(f"{what}: missing columns {miss}; have {list(df.columns)}")


def _src(eng, dataset, as_of, path, raw, n):
    return db.record_source(eng, dataset, as_of, f"file://{Path(path).name}", hashlib.sha256(raw).hexdigest(), n, "ok")


def import_deals(eng, path, kind: str = "bulk") -> int:
    raw = Path(path).read_bytes()
    df = parsers.deals(raw, kind)
    if df.empty:
        return 0
    src = _src(eng, f"deals_{kind}_import", df["date"].max(), path, raw, len(df))
    for day, g in df.groupby("date"):
        db.replace_rows(eng, db.deals, g.assign(source_id=src), [db.deals.c.date == day, db.deals.c.kind == kind])
    return len(df)


def import_shareholding(eng, path) -> int:
    df, raw = _read(path)
    _need(df, ["symbol", "quarter_end", "category", "pct"], "shareholding")
    df["category"] = df["category"].str.strip().str.lower()
    bad = set(df["category"]) - SH_CATEGORIES
    if bad:
        raise ImportErrorCSV(f"unknown categories {sorted(bad)}; use {sorted(SH_CATEGORIES)}")
    out = pd.DataFrame({
        "symbol": df["symbol"].str.strip().str.upper(),
        "quarter_end": pd.to_datetime(df["quarter_end"]).dt.date,
        "category": df["category"],
        "pct": pd.to_numeric(df["pct"], errors="coerce"),
        "shares": pd.to_numeric(df.get("shares"), errors="coerce") if "shares" in df else None,
    })
    pub = pd.to_datetime(df["published_on"], errors="coerce") if "published_on" in df else pd.Series(pd.NaT, index=df.index)
    out["published_on"] = pub.fillna(pd.to_datetime(out["quarter_end"]) + timedelta(days=21)).dt.date
    if out["pct"].isna().any() or ((out["pct"] < 0) | (out["pct"] > 100)).any():
        raise ImportErrorCSV("pct must be a number between 0 and 100")
    src = _src(eng, "shareholding_import", out["quarter_end"].max(), path, raw, len(out))
    for (sym, q), g in out.groupby(["symbol", "quarter_end"]):
        db.replace_rows(eng, db.shareholding, g.assign(source_id=src),
                        [db.shareholding.c.symbol == sym, db.shareholding.c.quarter_end == q])
    return len(out)


def import_mf(eng, path) -> int:
    df, raw = _read(path)
    _need(df, ["month_end", "amc", "scheme", "symbol", "shares"], "mf holdings")
    out = pd.DataFrame({
        "month_end": pd.to_datetime(df["month_end"]).dt.date,
        "amc": df["amc"].str.strip(), "scheme": df["scheme"].str.strip(),
        "symbol": df["symbol"].str.strip().str.upper(),
        "shares": pd.to_numeric(df["shares"].str.replace(",", ""), errors="coerce"),
        "market_value_cr": pd.to_numeric(df["market_value_cr"], errors="coerce") if "market_value_cr" in df else None,
    })
    pub = pd.to_datetime(df["published_on"], errors="coerce") if "published_on" in df else pd.Series(pd.NaT, index=df.index)
    out["published_on"] = pub.fillna(pd.to_datetime(out["month_end"]) + timedelta(days=10)).dt.date
    out = out.groupby(["month_end", "amc", "scheme", "symbol"], as_index=False).agg(
        shares=("shares", "sum"), market_value_cr=("market_value_cr", "sum"), published_on=("published_on", "max"))
    src = _src(eng, "mf_import", out["month_end"].max(), path, raw, len(out))
    for (m, amc), g in out.groupby(["month_end", "amc"]):
        db.replace_rows(eng, db.mf_holdings, g.assign(source_id=src),
                        [db.mf_holdings.c.month_end == m, db.mf_holdings.c.amc == amc])
    return len(out)


def import_fii_dii(eng, path) -> int:
    df, raw = _read(path)
    _need(df, ["date", "category", "buy_value", "sell_value", "net_value"], "fii_dii")
    out = pd.DataFrame({"date": pd.to_datetime(df["date"], dayfirst=False).dt.date,
                        "category": df["category"].str.strip().str.upper()})
    for c in ("buy_value", "sell_value", "net_value"):
        out[c] = pd.to_numeric(df[c].str.replace(",", ""), errors="coerce")
    out = out[out["category"].isin(["FII", "DII"])]
    src = _src(eng, "fii_dii_import", out["date"].max(), path, raw, len(out))
    for d, g in out.groupby("date"):
        db.replace_rows(eng, db.fii_dii_daily, g.assign(source_id=src), [db.fii_dii_daily.c.date == d])
    return len(out)


def import_fundamentals(eng, path) -> int:
    from .fundamentals import METRICS
    df, raw = _read(path)
    _need(df, ["symbol", "period_end", "period_type", "metric", "value"], "fundamentals")
    df["metric"] = df["metric"].str.strip().str.lower()
    df["period_type"] = df["period_type"].str.strip().str.upper()
    bad = set(df["period_type"]) - {"Q", "A"}
    if bad:
        raise ImportErrorCSV(f"period_type must be Q or A, got {sorted(bad)}")
    unknown = sorted(set(df["metric"]) - set(METRICS))
    out = pd.DataFrame({
        "symbol": df["symbol"].str.strip().str.upper(),
        "period_end": pd.to_datetime(df["period_end"]).dt.date,
        "metric": df["period_type"] + ":" + df["metric"],
        "value": pd.to_numeric(df["value"].astype(str).str.replace(",", ""), errors="coerce"),
    })
    lag = df["period_type"].map({"Q": 45, "A": 60})
    pub = pd.to_datetime(df["published_on"], errors="coerce") if "published_on" in df else pd.Series(pd.NaT, index=df.index)
    out["published_on"] = pub.fillna(pd.to_datetime(out["period_end"]) + pd.to_timedelta(lag, unit="D")).dt.date
    src = _src(eng, "fundamentals_import", out["period_end"].max(), path, raw, len(out))
    for (sym, pe), g in out.groupby(["symbol", "period_end"]):
        db.replace_rows(eng, db.fundamentals, g.assign(source_id=src),
                        [db.fundamentals.c.symbol == sym, db.fundamentals.c.period_end == pe,
                         db.fundamentals.c.metric.in_(list(g["metric"]))])
    if unknown:
        print(f"note: stored but not scored: {unknown}")
    return len(out)


IMPORTERS = {"deals": import_deals, "shareholding": import_shareholding, "mf": import_mf, "fii_dii": import_fii_dii,
             "fundamentals": import_fundamentals}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=list(IMPORTERS))
    ap.add_argument("path")
    ap.add_argument("--kind", default="bulk", choices=["bulk", "block"], help="for deals")
    a = ap.parse_args(argv)
    eng = db.engine()
    n = import_deals(eng, a.path, a.kind) if a.what == "deals" else IMPORTERS[a.what](eng, a.path)
    print(f"Imported {n} {a.what} rows from {a.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
