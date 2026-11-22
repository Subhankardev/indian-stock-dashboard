"""Load the research database from the Upstox API instead of (or alongside) NSE archives.

    python -m intel.upstox_ingest --from 2023-01-01             # backfill: stocks, indices, futures OI
    python -m intel.upstox_ingest --days 10                     # daily top-up (also run by intel.daily)
    python -m intel.upstox_ingest --from 2023-01-01 --fo-only   # only the ~200 F&O stocks (fast)
    python -m intel.upstox_ingest --symbols RELIANCE,TCS --from 2024-01-01

Needs ``UPSTOX_ACCESS_TOKEN`` in backend/.env (one-click login: /api/upstox/login).

What Upstox provides, and what it does not (the system degrades honestly):

* ✅ Daily OHLCV for every currently listed NSE equity and for indices (Nifty 50, Nifty 500, …).
* ✅ Futures open interest per contract → ``deriv_daily`` (total OI across expiries, near-month
  close, basis). Expired contracts need the access token.
* ✅ Today's option chain (PCR, max-OI strikes, ATM IV) for indices with ``--options``.
* ❌ **Delivery quantity / delivery %** — not in Upstox. Delivery-based class-D clues stay blank.
* ❌ Exact daily traded value / VWAP — approximated as volume × typical price (flagged).
* ❌ **Delisted stocks** — the instrument list holds only today's listings, so a backtest over
  this data has **survivorship bias** (results look better than reality). Reports say so.
* ❌ Bulk/block deals, FII/DII, participant OI, index constituents/industries — use NSE files
  (``python -m intel.ingest --today --lists``) or CSV imports (``python -m intel.imports``).

NSE rows always win: dates already loaded from NSE bhavcopies (which carry delivery data) are
never overwritten, and a later NSE load replaces Upstox rows for its day.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from typing import Callable, Optional

import numpy as np
import pandas as pd
from sqlalchemy import select

from . import db

DATASET = "upstox_cm"
INDEX_KEYS = {"Nifty 50": "NSE_INDEX|Nifty 50", "Nifty 500": "NSE_INDEX|Nifty 500",
              "Nifty Bank": "NSE_INDEX|Nifty Bank", "Nifty 100": "NSE_INDEX|Nifty 100",
              "Nifty 200": "NSE_INDEX|Nifty 200"}
FO_INDEX = {"NIFTY": "NSE_INDEX|Nifty 50", "BANKNIFTY": "NSE_INDEX|Nifty Bank"}


def equities(instruments: list[dict], fo_only: bool = False, symbols: Optional[list] = None) -> list[dict]:
    eq = [i for i in instruments if i.get("segment") == "NSE_EQ" and i.get("instrument_type") == "EQ"]
    if fo_only:
        fo = {str(i.get("underlying_symbol", "")).upper() for i in instruments
              if i.get("segment") == "NSE_FO" and i.get("instrument_type") == "FUT"}
        eq = [i for i in eq if str(i.get("trading_symbol", "")).upper() in fo]
    if symbols:
        want = {s.upper() for s in symbols}
        eq = [i for i in eq if str(i.get("trading_symbol", "")).upper() in want]
    return sorted(eq, key=lambda i: i["trading_symbol"])


def to_price_rows(symbol: str, candles: pd.DataFrame) -> pd.DataFrame:
    """Upstox daily candles → prices_daily rows. Turnover/VWAP approximated; no delivery data."""
    if candles.empty:
        return pd.DataFrame()
    c = candles.dropna(subset=["close"]).copy()
    tp = (c["high"] + c["low"] + c["close"]) / 3
    prev = c["close"].shift(1)
    return pd.DataFrame({
        "symbol": symbol, "series": "EQ", "date": c.index.date, "prev_close": prev.to_numpy(),
        "open": c["open"].to_numpy(), "high": c["high"].to_numpy(), "low": c["low"].to_numpy(),
        "last": c["close"].to_numpy(), "close": c["close"].to_numpy(), "avg_price": tp.to_numpy(),
        "volume": c["volume"].to_numpy(), "turnover_lacs": (c["volume"] * tp / 1e5).to_numpy(),
        "trades": np.nan, "deliv_qty": np.nan, "deliv_pct": np.nan})


def _finalize_source(eng, src_id: int, dataset: str, as_of: date, rows: int, status: str,
                     error: Optional[str]) -> None:
    """Update the run's provenance record in place (as_of = latest data actually received).
    An older record for the same dataset/day is merged into this one."""
    from sqlalchemy import delete, update
    t = db.source_files
    with eng.begin() as c:
        other = c.execute(select(t.c.id).where(t.c.dataset == dataset, t.c.as_of == as_of, t.c.id != src_id)).scalar()
        if other:
            for tbl in (db.prices_daily, db.deriv_daily):
                c.execute(update(tbl).where(tbl.c.source_id == other).values(source_id=src_id))
            c.execute(delete(t).where(t.c.id == other))
        c.execute(update(t).where(t.c.id == src_id).values(as_of=as_of, rows=rows, status=status, error=error))


def _start_source(eng, dataset: str, url: str) -> int:
    """A provenance record for this run, dated provisionally far in the past (fixed at the end)."""
    from datetime import datetime, timezone
    from sqlalchemy import insert
    with eng.begin() as c:
        r = c.execute(insert(db.source_files).values(
            dataset=dataset, as_of=None, url=url, sha256=None, rows=0, status="running",
            fetched_at=datetime.now(timezone.utc).replace(tzinfo=None)))
        return int(r.inserted_primary_key[0])


def _existing_dates(eng, symbol: str, start: date) -> set:
    t = db.prices_daily
    with eng.connect() as c:
        return {r[0] for r in c.execute(select(t.c.date).where(t.c.symbol == symbol, t.c.date >= start))}


def _nse_dates(eng, start: date) -> set:
    """Days already loaded from NSE bhavcopies (they carry delivery data and win)."""
    with eng.connect() as c:
        return {r[0] for r in c.execute(select(db.source_files.c.as_of).where(
            db.source_files.c.dataset == "cm", db.source_files.c.status == "ok", db.source_files.c.as_of >= start))}


def load_equities(eng, client, start: date, end: date, fo_only=False, symbols=None, force=False,
                  progress: Optional[Callable[[int, int, str], None]] = None) -> dict:
    ins = client.instruments()
    todo = equities(ins, fo_only, symbols)
    src = _start_source(eng, DATASET, f"upstox://historical-candle/day ({len(todo)} symbols, {start}..{end})")
    nse_days = _nse_dates(eng, start)
    rows_total, errors, latest = 0, [], None
    for n, inst in enumerate(todo, 1):
        sym = str(inst["trading_symbol"]).upper()
        try:
            candles = client.daily_candles(inst["instrument_key"], start, end, include_today=False)
        except Exception as exc:
            errors.append(f"{sym}: {exc}")
            continue
        rows = to_price_rows(sym, candles)
        if rows.empty:
            continue
        skip = set(nse_days) | (set() if force else _existing_dates(eng, sym, start))
        rows = rows[~rows["date"].isin(skip)]
        if len(rows):
            db.replace_rows(eng, db.prices_daily, rows.assign(source_id=src),
                            [db.prices_daily.c.symbol == sym, db.prices_daily.c.series == "EQ",
                             db.prices_daily.c.date.in_(list(rows["date"]))])
            rows_total += len(rows)
            latest = max(latest or rows["date"].max(), rows["date"].max())
        if progress:
            progress(n, len(todo), sym)
    _finalize_source(eng, src, DATASET, latest or end, rows_total,
                     "ok" if rows_total or not errors else "error", "; ".join(errors[:5]) or None)
    return {"symbols": len(todo), "rows": rows_total, "errors": len(errors), "first_errors": errors[:5]}


def load_indices(eng, client, start: date, end: date) -> dict:
    out = {}
    for name, key in INDEX_KEYS.items():
        try:
            c = client.daily_candles(key, start, end, include_today=False)
        except Exception as exc:
            out[name] = f"error: {exc}"
            continue
        if c.empty:
            out[name] = 0
            continue
        src = db.record_source(eng, f"upstox_index_{name}", c.index.max().date(), f"upstox://{key}", None, len(c), "ok")
        df = pd.DataFrame({"index_name": name, "date": c.index.date, "open": c["open"].to_numpy(),
                           "high": c["high"].to_numpy(), "low": c["low"].to_numpy(), "close": c["close"].to_numpy(),
                           "volume": c["volume"].to_numpy(), "turnover_cr": None, "pe": None, "pb": None,
                           "div_yield": None, "source_id": src})
        db.replace_rows(eng, db.index_daily, df, [db.index_daily.c.index_name == name,
                                                  db.index_daily.c.date.in_(list(df["date"]))])
        out[name] = len(df)
    return out


def deriv_rows(symbol: str, kind: str, spot: pd.Series, contracts: list[tuple[date, pd.DataFrame]]) -> pd.DataFrame:
    """deriv_daily rows from per-contract futures candles [(expiry, candles)]: total OI across
    contracts, near-month close, basis vs spot, rollover share."""
    frames = []
    for exp, c in contracts:
        if c is None or c.empty:
            continue
        f = c[["close", "open_interest", "volume"]].copy()
        f["expiry"] = exp
        frames.append(f)
    if not frames:
        return pd.DataFrame()
    allf = pd.concat(frames)
    allf = allf[allf["open_interest"].fillna(0) > 0]
    allf = allf[pd.to_datetime(allf["expiry"]) >= allf.index]            # contract alive that day
    rows = []
    for d, g in allf.groupby(level=0):
        g = g.sort_values("expiry")
        near = g.iloc[0]
        tot = float(g["open_interest"].sum())
        s = float(spot.get(d, np.nan)) if len(spot) else np.nan
        rows.append({"date": d.date(), "symbol": symbol, "kind": kind, "underlying": s if np.isfinite(s) else None,
                     "near_expiry": near["expiry"], "days_to_expiry": (pd.Timestamp(near["expiry"]) - d).days,
                     "fut_close": float(near["close"]), "fut_oi": tot, "fut_volume": float(g["volume"].sum()),
                     "basis_pct": (float(near["close"]) / s - 1) * 100 if np.isfinite(s) and s else None,
                     "rollover_pct": float(g["open_interest"].iloc[1:].sum() / tot * 100) if tot else None})
    return pd.DataFrame(rows)


def load_futures(eng, client, start: date, end: date, symbols=None, max_expired: int = 40) -> dict:
    """Futures OI history for F&O stocks and NIFTY/BANKNIFTY into deriv_daily (keeps option fields)."""
    from app.upstox_data import find_futures
    ins = client.instruments()
    unders = sorted({str(i.get("underlying_symbol", "")).upper() for i in ins
                     if i.get("segment") == "NSE_FO" and i.get("instrument_type") == "FUT"})
    if symbols:
        unders = [u for u in unders if u in {s.upper() for s in symbols}]
    eq_keys = {str(i["trading_symbol"]).upper(): i["instrument_key"] for i in equities(ins)}
    src = _start_source(eng, "upstox_fo", f"upstox://futures candles + expired contracts ({start}..{end})")
    done, errors, latest = 0, [], None
    for sym in unders:
        kind = "index" if sym in FO_INDEX else "stock"
        under_key = FO_INDEX.get(sym) or eq_keys.get(sym)
        if not under_key:
            continue
        try:
            spot = client.daily_candles(under_key, start, end, include_today=False)["close"]
            contracts = [(pd.Timestamp(f["expiry"], unit="ms").date() if isinstance(f.get("expiry"), (int, float))
                          else pd.Timestamp(f["expiry"]).date(),
                          client.daily_candles(f["instrument_key"], start, end, include_today=False))
                         for f in find_futures(ins, sym)]
            if client.token and start < end - timedelta(days=95):
                contracts += _expired(client, under_key, start, max_expired)
        except Exception as exc:
            errors.append(f"{sym}: {exc}")
            continue
        rows = deriv_rows(sym, kind, spot, contracts)
        if rows.empty:
            continue
        rows = rows[(rows["date"] >= start) & (rows["date"] <= end)]
        _merge_deriv(eng, rows, src)
        done += 1
        latest = max(latest or rows["date"].max(), rows["date"].max())
    _finalize_source(eng, src, "upstox_fo", latest or end, done, "ok" if done or not errors else "error",
                     "; ".join(errors[:5]) or None)
    return {"underlyings": done, "errors": len(errors), "first_errors": errors[:5]}


def _expired(client, under_key: str, start: date, max_contracts: int) -> list:
    from urllib.parse import quote
    from app.upstox_data import parse_candles
    expiries = client._get("/v2/expired-instruments/expiries", {"instrument_key": under_key}).get("data") or []
    expiries = sorted((e for e in expiries if e >= f"{start:%Y-%m-%d}"), reverse=True)[:max_contracts]
    out = []
    for exp in expiries:
        for ct in client._get("/v2/expired-instruments/future/contract",
                              {"instrument_key": under_key, "expiry_date": exp}).get("data") or []:
            ek = quote(ct["instrument_key"], safe="")
            d = date.fromisoformat(exp)
            frm = max(start, d - timedelta(days=100))
            body = client._get(f"/v2/expired-instruments/historical-candle/{ek}/day/{d:%Y-%m-%d}/{frm:%Y-%m-%d}")
            out.append((d, parse_candles(body.get("data", {}).get("candles", []))))
    return out


def _merge_deriv(eng, rows: pd.DataFrame, src: int) -> None:
    """Upsert futures fields; keep option fields (PCR, IV …) already stored for those days."""
    t = db.deriv_daily
    sym = rows["symbol"].iloc[0]
    old = db.read_sql(eng, select(t).where(t.c.symbol == sym, t.c.date.in_(list(rows["date"]))))
    if len(old):
        keep = [c for c in ("ce_oi", "pe_oi", "pcr_oi", "ce_volume", "pe_volume", "pcr_volume", "max_ce_oi_strike",
                            "max_pe_oi_strike", "atm_strike", "atm_iv", "lot_size") if c in old]
        rows = rows.merge(old[["date"] + keep], on="date", how="left")
    db.replace_rows(eng, t, rows.assign(source_id=src), [t.c.symbol == sym, t.c.date.in_(list(rows["date"]))])


def load_options_today(eng, client, underlyings=("NIFTY", "BANKNIFTY")) -> dict:
    """Today's option-chain summary (PCR, max-OI strikes, ATM IV) — Upstox has no option history,
    so this builds it forward one day at a time when run daily."""
    from app.option_analytics import analyze_chain
    out = {}
    for sym in underlyings:
        try:
            chain = client.option_chain(sym)
            a = analyze_chain(chain, window=0)
        except Exception as exc:
            out[sym] = f"error: {exc}"
            continue
        rows = chain.get("rows") or []
        ce = sum((r.get("CE") or {}).get("oi") or 0 for r in rows)
        pe = sum((r.get("PE") or {}).get("oi") or 0 for r in rows)
        ce_max = max(rows, key=lambda r: (r.get("CE") or {}).get("oi") or 0, default=None)
        pe_max = max(rows, key=lambda r: (r.get("PE") or {}).get("oi") or 0, default=None)
        day = date.today()
        rec = {"date": day, "symbol": sym, "ce_oi": ce, "pe_oi": pe, "pcr_oi": pe / ce if ce else None,
               "max_ce_oi_strike": ce_max["strike"] if ce_max else None,
               "max_pe_oi_strike": pe_max["strike"] if pe_max else None,
               "atm_strike": a.get("atm_strike"), "atm_iv": a.get("atm_iv")}
        t = db.deriv_daily
        old = db.read_sql(eng, select(t).where(t.c.symbol == sym, t.c.date == day))
        base = old.iloc[0].to_dict() if len(old) else {"date": day, "symbol": sym, "kind": "index"}
        base.update({k: v for k, v in rec.items() if v is not None})
        src = db.record_source(eng, "upstox_options", day, f"upstox://option/chain/{sym}", None, len(rows), "ok")
        base["source_id"] = src
        db.replace_rows(eng, t, pd.DataFrame([base]), [t.c.symbol == sym, t.c.date == day])
        out[sym] = round(rec["pcr_oi"], 3) if rec["pcr_oi"] else None
    return out


def survivorship_warning(eng) -> Optional[str]:
    """Non-empty when price history comes (partly) from Upstox, whose instrument list omits delisted stocks."""
    with eng.connect() as c:
        up = c.execute(select(db.source_files.c.id).where(db.source_files.c.dataset == DATASET).limit(1)).first()
    if not up:
        return None
    return ("Price history includes Upstox data, which covers only currently listed stocks: delisted names are "
            "missing, so backtests and signal tests have survivorship bias (results look better than reality). "
            "Upstox has no delivery data, so delivery-based clues are blank for those rows.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="end")
    ap.add_argument("--days", type=int, help="last N calendar days (daily top-up)")
    ap.add_argument("--fo-only", action="store_true", help="only stocks with futures (~200)")
    ap.add_argument("--symbols", help="comma list, e.g. RELIANCE,TCS")
    ap.add_argument("--no-futures", action="store_true")
    ap.add_argument("--options", action="store_true", help="store today's NIFTY/BANKNIFTY option-chain summary")
    ap.add_argument("--force", action="store_true", help="re-load dates already stored from Upstox")
    a = ap.parse_args(argv)
    from app import upstox_data
    client = upstox_data.client()
    if not client.token:
        print("Set UPSTOX_ACCESS_TOKEN in backend/.env first (one-click: http://localhost:8000/api/upstox/login).")
        return 2
    end = date.fromisoformat(a.end) if a.end else date.today()
    start = date.fromisoformat(a.start) if a.start else end - timedelta(days=a.days or 10)
    syms = [s.strip() for s in a.symbols.split(",")] if a.symbols else None
    eng = db.engine()
    t0 = time.time()

    def progress(n, total, sym):
        sys.stdout.write(f"\r  {n}/{total} {sym:<15} ({time.time() - t0:.0f}s)   ")
        sys.stdout.flush()

    print(f"Indices: {load_indices(eng, client, start, end)}")
    print(f"Equities {start} → {end}…")
    print("\n", load_equities(eng, client, start, end, a.fo_only, syms, a.force, progress))
    if not a.no_futures:
        print("Futures OI:", load_futures(eng, client, start, end, syms))
    if a.options:
        print("Options today:", load_options_today(eng, client))
    w = survivorship_warning(eng)
    if w:
        print("Note:", w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
