from datetime import date

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from intel import db, upstox_ingest as ui
from intel.panel import build_panel, universe_symbols

DATES = pd.bdate_range("2024-01-01", "2024-12-31")


def candles(base=100.0, oi=None, seed=0, dates=DATES):
    rng = np.random.default_rng(seed)
    c = base * np.exp(np.cumsum(rng.normal(0, 0.01, len(dates))))
    df = pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
                       "volume": rng.uniform(1e6, 2e6, len(dates)).round(),
                       "open_interest": np.nan if oi is None else oi}, index=pd.DatetimeIndex(dates, name="date"))
    return df


class FakeUpstox:
    token = "t"

    def __init__(self):
        exp1 = int(pd.Timestamp("2025-01-30").timestamp() * 1000)
        self.ins = [
            {"segment": "NSE_EQ", "instrument_type": "EQ", "trading_symbol": "AAA", "instrument_key": "NSE_EQ|A"},
            {"segment": "NSE_EQ", "instrument_type": "EQ", "trading_symbol": "BBB", "instrument_key": "NSE_EQ|B"},
            {"segment": "NSE_FO", "instrument_type": "FUT", "underlying_symbol": "AAA", "instrument_key": "NSE_FO|A1",
             "expiry": exp1},
        ]
        self.calls = []

    def instruments(self):
        return self.ins

    def daily_candles(self, key, start, end, include_today=True):
        self.calls.append(key)
        d = DATES[(DATES.date >= start) & (DATES.date <= end)]
        if key == "NSE_FO|A1":
            return candles(101, oi=np.linspace(1e6, 3e6, len(d)), seed=1, dates=d)
        if key.startswith("NSE_INDEX"):
            return candles(20000, seed=2, dates=d)
        return candles(100 if key.endswith("A") else 500, seed=3 if key.endswith("A") else 4, dates=d)

    def _get(self, path, params=None):
        return {"data": []}


def test_price_rows_flag_missing_delivery_and_approximate_turnover():
    rows = ui.to_price_rows("AAA", candles())
    r = rows.iloc[5]
    assert np.isnan(r["deliv_pct"]) and np.isnan(r["deliv_qty"])
    assert r["turnover_lacs"] == pytest.approx(r["volume"] * (r["high"] + r["low"] + r["close"]) / 3 / 1e5)


def test_backfill_equities_indices_futures_and_provenance():
    eng = db.engine()
    f = FakeUpstox()
    s, e = date(2024, 1, 1), date(2024, 12, 31)
    assert ui.load_indices(eng, f, s, e)["Nifty 50"] == len(DATES)
    res = ui.load_equities(eng, f, s, e)
    assert res == {"symbols": 2, "rows": 2 * len(DATES), "errors": 0, "first_errors": []}
    src = db.read_sql(eng, select(db.source_files).where(db.source_files.c.dataset == "upstox_cm")).iloc[0]
    assert src["status"] == "ok" and src["as_of"] == date(2024, 12, 31) and src["rows"] == 2 * len(DATES)
    prices = db.read_sql(eng, select(db.prices_daily))
    assert (prices["source_id"] == src["id"]).all()
    assert ui.load_futures(eng, f, s, e)["underlyings"] == 1
    d = db.read_sql(eng, select(db.deriv_daily))
    assert set(d["symbol"]) == {"AAA"} and d["fut_oi"].iloc[-1] == pytest.approx(3e6) and d["basis_pct"].notna().all()
    # a second run is idempotent and keeps one provenance record per day
    assert ui.load_equities(eng, f, s, e)["rows"] == 0
    assert len(db.read_sql(eng, select(db.source_files).where(db.source_files.c.dataset == "upstox_cm"))) == 1
    # the panel builds from Upstox data; the F&O universe comes from deriv_daily
    panel = build_panel(eng)
    assert set(panel["symbol"]) == {"AAA", "BBB"} and panel["deliv_pct"].isna().all()
    assert universe_symbols(eng, {"universe": {"name": "fno", "series": ["EQ"]}}) == ["AAA"]
    assert "survivorship" in ui.survivorship_warning(eng)


def test_nse_rows_win():
    eng = db.engine()
    day = date(2024, 3, 5)
    nse = pd.DataFrame([{"symbol": "AAA", "series": "EQ", "date": day, "open": 1, "high": 1, "low": 1, "close": 1,
                         "volume": 10, "turnover_lacs": 1, "deliv_qty": 5, "deliv_pct": 50.0}])
    sid = db.record_source(eng, "cm", day, "nse://x", "h", 1, "ok")
    db.replace_rows(eng, db.prices_daily, nse.assign(source_id=sid), [db.prices_daily.c.date == day])
    ui.load_equities(eng, FakeUpstox(), date(2024, 3, 1), date(2024, 3, 8), symbols=["AAA"])
    row = db.read_sql(eng, select(db.prices_daily).where(db.prices_daily.c.date == day)).iloc[0]
    assert row["deliv_pct"] == 50.0 and row["close"] == 1, "the NSE row (with delivery) is kept"


def test_fo_only_and_symbol_filter():
    f = FakeUpstox()
    assert [i["trading_symbol"] for i in ui.equities(f.ins, fo_only=True)] == ["AAA"]
    assert [i["trading_symbol"] for i in ui.equities(f.ins, symbols=["bbb"])] == ["BBB"]


def test_deriv_rows_sum_contracts_and_drop_expired():
    d = pd.bdate_range("2024-01-01", periods=10)
    near = candles(100, oi=np.full(10, 1e6), dates=d)
    nxt = candles(101, oi=np.full(10, 5e5), dates=d)
    rows = ui.deriv_rows("X", "stock", near["close"], [(date(2024, 1, 5), near), (date(2024, 2, 29), nxt)])
    first, last = rows.iloc[0], rows.iloc[-1]
    assert first["fut_oi"] == 1.5e6 and first["near_expiry"] == date(2024, 1, 5)
    assert last["fut_oi"] == 5e5 and last["near_expiry"] == date(2024, 2, 29), "expired contract dropped"


def test_daily_job_skips_upstox_without_token(monkeypatch, capsys):
    from intel import daily, ingest
    monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(ingest, "main", lambda argv=None: 0)
    daily.main()
    assert "Skipping Upstox" in capsys.readouterr().out
