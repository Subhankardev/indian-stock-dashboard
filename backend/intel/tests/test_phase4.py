from datetime import date

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from intel import db, demo, intraday, orderflow, vwap
from intel.vwap import _argext_pos


def bars(n=300, seed=4):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.015, n)))
    v = rng.uniform(1e5, 3e5, n)
    idx = pd.bdate_range("2024-01-01", periods=n)
    avg = c * rng.uniform(0.995, 1.005, n)
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": v,
                         "turnover_lacs": avg * v / 1e5}, index=idx)


def test_day_vwap_is_nse_average_price():
    g = bars(5)
    np.testing.assert_allclose(vwap.day_vwap(g), g["turnover_lacs"] * 1e5 / g["volume"])


def test_rolling_extreme_positions_match_brute_force():
    x = pd.Series(np.random.default_rng(1).normal(size=200))
    for fn, f in (("max", np.argmax), ("min", np.argmin)):
        got = _argext_pos(x, 20, fn)
        for t in range(200):
            lo = max(0, t - 19)
            w = x.to_numpy()[lo:t + 1]
            assert x.iloc[got[t]] == w[f(w)], (fn, t)


def test_anchored_vwap_values_and_no_lookahead():
    g = bars()
    full = vwap.compute(g)
    part = vwap.compute(g.iloc[:200])
    pd.testing.assert_frame_equal(full.iloc[:200], part, check_exact=False, rtol=1e-9)
    t = 260
    a = int(_argext_pos(g["volume"], 60, "max")[t])
    dv = vwap.day_vwap(g)
    manual = (dv.iloc[a:t + 1] * g["volume"].iloc[a:t + 1]).sum() / g["volume"].iloc[a:t + 1].sum()
    assert full["avwap_volspike60"].iloc[t] == pytest.approx(manual)
    ytd = g.index[t].year
    first = np.where(g.index.year == ytd)[0][0]
    manual_ytd = (dv.iloc[first:t + 1] * g["volume"].iloc[first:t + 1]).sum() / g["volume"].iloc[first:t + 1].sum()
    assert full["avwap_ytd"].iloc[t] == pytest.approx(manual_ytd)


def test_absorption_proxy():
    g = bars(60)
    g.iloc[-1, g.columns.get_loc("volume")] = g["volume"].iloc[-21:-1].mean() * 3
    c = g["close"].iloc[-1]
    g.iloc[-1, g.columns.get_loc("high")] = c * 1.001
    g.iloc[-1, g.columns.get_loc("low")] = c * 0.997
    out = vwap.compute(g)
    assert out["absorption_up"].iloc[-1] and not out["absorption_down"].iloc[-1]


def test_intraday_vwap_and_profile():
    m = intraday.synthetic_session(date(2026, 11, 20), base=500)
    sv = intraday.session_vwap(m)
    tp = (m["high"] + m["low"] + m["close"]) / 3
    assert sv["vwap"].iloc[-1] == pytest.approx((tp * m["volume"]).sum() / m["volume"].sum())
    assert (sv["upper2"] >= sv["upper1"]).all() and (sv["lower1"] >= sv["lower2"]).all()
    prof = intraday.volume_profile(m)
    vols = [b["volume"] for b in prof["bins"]]
    assert sum(vols) == pytest.approx(m["volume"].sum())
    assert prof["val"] <= prof["poc"] <= prof["vah"]
    inside = sum(b["volume"] for b in prof["bins"] if prof["val"] <= b["price"] <= prof["vah"])
    assert inside >= 0.7 * sum(vols)


def test_parse_upstox_minute_candles():
    rows = [["2026-11-20T09:16:00+05:30", 10, 11, 9, 10.5, 1000, 0], ["2026-11-20T09:15:00+05:30", 9, 10, 9, 10, 500, 0]]
    m = intraday.parse_minutes(rows)
    assert list(m.index.strftime("%H:%M")) == ["09:15", "09:16"] and m["volume"].sum() == 1500


def test_orderflow_disabled_explains_needs():
    s = orderflow.status()
    assert s["enabled"] is False and all(not f["available"] and f["needs"] for f in s["features"])
    assert any("tick" in p["name"].lower() for p in s["providers"])


def test_phase4_api():
    eng = db.engine()
    demo.seed(eng, demo.make(n_symbols=12, days=300))
    c = TestClient(__import__("app.main", fromlist=["app"]).app)
    v = c.get("/api/intel/stock/DEMO01/vwap", params={"days": 60}).json()
    assert len(v["series"]) == 60 and set(v["anchors"]) == set(vwap.ANCHORS) and v["latest"]["vwap20"]
    i = c.get("/api/intel/stock/DEMO01/intraday").json()
    assert i["demo"] and i["source"] == "synthetic" and i["profile"]["poc"] and len(i["bars"]) == 375
    o = c.get("/api/intel/orderflow").json()
    assert o["enabled"] is False
