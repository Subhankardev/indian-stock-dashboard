from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from intel import db, demo, derivatives as dv, evidence, ingest, parsers
from intel.panel import build_panel, latest_snapshot
from intel.sources import DATASETS, Fetcher

FIX = Path(__file__).parent / "fixtures"


def test_implied_vol_round_trip_and_bounds():
    for call in (True, False):
        px = dv.bs_price(250.0, 240.0, 30 / 365, 0.065, 0.32, call)
        assert dv.implied_vol(float(px), 250.0, 240.0, 30 / 365, 0.065, call) == pytest.approx(0.32, abs=1e-4)
    assert dv.implied_vol(0.0, 100, 100, 0.1, 0.06, True) is None
    assert dv.implied_vol(150.0, 100, 100, 0.1, 0.06, True) is None, "above spot is impossible for a call"


def test_summary_from_real_udiff_file():
    fo = parsers.fo_udiff((FIX / "BhavCopy_NSE_FO_sample.csv").read_bytes())
    s = dv.summarize_day(fo).iloc[0]
    assert s["symbol"] == "CUB" and s["kind"] == "stock" and s["underlying"] == 215.01
    assert s["fut_oi"] > 0 and s["basis_pct"] == pytest.approx((s["fut_close"] / 215.01 - 1) * 100)
    assert s["pcr_oi"] == pytest.approx(s["pe_oi"] / s["ce_oi"]) and 5 < s["atm_iv"] < 150


def test_ingest_fo_keeps_summary_and_only_futures_rows(tmp_path):
    eng = db.engine()
    raw = (FIX / "BhavCopy_NSE_FO_sample.csv").read_bytes()
    day = date(2026, 10, 28)

    class S:
        def get(self, url, **kw):
            class R: pass
            r = R(); r.status_code, r.content = (200, raw) if url == DATASETS["fo"].url(day) else (404, b"")
            return r
    f = Fetcher(session=S(), cache_dir=tmp_path, min_interval=0, retries=0)
    assert ingest.load_day(eng, f, "fo", day) == "ok"
    rows = db.read_sql(eng, select(db.fo_daily))
    assert set(rows["instrument"]) == {"STF"}, "option rows are summarised, not stored (nse.store_option_rows)"
    summ = db.read_sql(eng, select(db.deriv_daily))
    assert len(summ) == 1 and summ["source_id"].notna().all()


def test_buildup_classes():
    r = pd.Series([2.0, -2.0, 2.0, -2.0, 0.5, np.nan])
    oi = pd.Series([8.0, 8.0, -8.0, -8.0, 1.0, 5.0])
    assert list(dv.classify_buildup(r, oi, 5.0)) == ["long_buildup", "short_buildup", "short_covering",
                                                    "long_unwinding", "neutral", None]


def test_rollover_does_not_fake_unwinding_and_expiry_is_blanked():
    eng = db.engine()
    dates = pd.bdate_range("2024-01-01", periods=40)
    px = np.linspace(100, 110, 40)
    prices = pd.DataFrame({"symbol": "S", "series": "EQ", "date": dates.date, "open": px, "high": px * 1.01,
                           "low": px * 0.99, "close": px, "volume": 1e6, "turnover_lacs": 1e6 * px / 1e5,
                           "deliv_qty": 4e5, "deliv_pct": 40.0})
    db.replace_rows(eng, db.prices_daily, prices, [db.prices_daily.c.symbol == "S"])
    exp = [date(2024, 1, 25) if d.date() <= date(2024, 1, 25) else date(2024, 2, 29) for d in dates]
    oi = np.geomspace(1e6, 3e6, 40)
    deriv = pd.DataFrame({"date": dates.date, "symbol": "S", "kind": "stock", "underlying": px, "near_expiry": exp,
                          "fut_oi": oi})
    db.replace_rows(eng, db.deriv_daily, deriv, [db.deriv_daily.c.symbol == "S"])
    panel = build_panel(eng)
    f = dv.features(eng, panel)
    assert f["f_has_fo"].all()
    roll_day = panel.index[panel["date"] == pd.Timestamp("2024-01-26")][0]
    assert f.at[roll_day, "f_buildup"] == "expiry" and f.at[roll_day, "f_expiry_window"]
    assert (f["f_buildup"].dropna().isin(["long_buildup", "neutral", "expiry"])).all()
    assert f["f_buildup"].iloc[-1] == "long_buildup"


def test_scores_include_derivatives_component():
    eng = db.engine()
    demo.seed(eng, demo.make(n_symbols=24, days=300))
    panel = evidence.attach(eng, build_panel(eng))
    snap = latest_snapshot(panel)
    sc = evidence.scores(snap, panel, passed=set())
    fo = snap["f_has_fo"].astype(bool).to_numpy()
    assert np.allclose(sc.loc[fo, "accumulation_confidence"], 1.0)
    assert np.allclose(sc.loc[~fo, "accumulation_confidence"], 0.85), "no F&O -> derivatives missing"


def test_phase3_api():
    eng = db.engine()
    demo.seed(eng, demo.make(n_symbols=24, days=300))
    c = TestClient(__import__("app.main", fromlist=["app"]).app)
    r = c.get("/api/intel/derivatives").json()
    assert r["participants"] and r["indices"] and set(r["buildups"]) == set(dv.BUILDUPS)
    assert "not institutional buying" in r["note"]
    d = c.get("/api/intel/stock/DEMO00/derivatives").json()
    assert d["has_fo"] and d["series"][-1]["fut_oi"] > 0
    assert c.get("/api/intel/stock/DEMO23/derivatives").json()["has_fo"] is False
