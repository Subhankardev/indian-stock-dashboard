from datetime import datetime

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert

from intel import db, demo, market, scoring
from intel.panel import build_panel
from intel.research.signals import SIGNALS


def _pass(eng, *names):
    with eng.begin() as c:
        for n in names:
            c.execute(insert(db.signal_tests).values(signal_name=n, rule_version="t", run_at=datetime(2026, 1, 1),
                                                     universe="all", period="", stats="{}", passed=1))


@pytest.fixture
def enriched():
    eng = db.engine()
    demo.seed(eng, demo.make(n_symbols=36, days=320))
    return eng, scoring.enrich(eng, build_panel(eng))


def test_factor_percentiles_are_cross_sectional_among_liquid(enriched):
    eng, p = enriched
    liquid = p["liquid"].fillna(False).astype(bool)
    for f in scoring.FACTORS:
        v = p.loc[liquid, f"p_{f}"].dropna()
        assert v.between(0, 100).all(), f
        assert p.loc[~liquid, f"p_{f}"].isna().all() or f in ("relative_strength",), f
    last = p[(p["date"] == p["date"].max()) & liquid]
    assert last["p_momentum"].max() == pytest.approx(100, abs=5)


def test_score_formula_confidence_and_agreement():
    df = pd.DataFrame({"p_relative_strength": [90.0, 90.0], "c_relative_strength": [1.0, 1.0],
                       "p_momentum": [30.0, np.nan], "c_momentum": [1.0, 0.0],
                       "p_fundamentals": [80.0, 80.0], "c_fundamentals": [0.5, 0.5]})
    w = {"relative_strength": 15, "momentum": 15, "fundamentals": 10}
    s = scoring.score(df, {"relative_strength", "momentum", "fundamentals"}, w)
    assert s["bullish_score"].iloc[0] == pytest.approx((15 * 90 + 15 * 30 + 10 * 80 * 0.5) / (15 + 15 + 5))
    assert s["bullish_confidence"].iloc[0] == pytest.approx(35 / 40)
    assert s["bullish_score"].iloc[1] == pytest.approx((15 * 90 + 5 * 80) / 20), "missing data is not neutral"
    assert s["bullish_confidence"].iloc[1] == pytest.approx(20 / 40)
    assert s["bullish_agreement"].iloc[0] == pytest.approx(2 / 3)
    only_rs = scoring.score(df, {"relative_strength"}, w)
    assert only_rs["bullish_score"].iloc[0] == 90 and only_rs["bullish_confidence"].iloc[0] == 1


def test_rankings_need_passed_factors_and_confidence(enriched):
    eng, p = enriched
    r = scoring.rankings(eng, p)
    assert r["bullish"] == [] and "No factor has passed" in r["message"]
    _pass(eng, "factor_relative_strength", "factor_momentum")
    r = scoring.rankings(eng, p, limit=10)
    assert r["active"] == ["momentum", "relative_strength"] and len(r["bullish"]) == 10
    scores = [x["score"] for x in r["bullish"]]
    assert scores == sorted(scores, reverse=True) and all(x["confidence"] >= 0.6 for x in r["bullish"])
    prev = scoring.rankings(eng, p, preview=True)
    assert prev["preview"] and set(prev["active"]) == set(scoring.FACTORS)


def test_conflicts_flagged():
    row = pd.Series({"p_accumulation": 85, "q_accumulation": 80, "p_relative_strength": 90, "x_score": 3,
                     "f_buildup": "short_buildup"})
    c = scoring.conflicts(row)
    assert len(c) == 3 and any("both strong" in x for x in c)


def test_factor_signals_exist_and_run(enriched):
    eng, p = enriched
    for f in scoring.FACTORS:
        m = SIGNALS[f"factor_{f}"][0](p)
        assert m.dtype == bool and m.index.equals(p.index)


def test_market_regime_breadth_sectors(enriched):
    eng, p = enriched
    r = market.regime(eng, p)
    assert r["label"] in {"uptrend", "downtrend", "mixed"} and r["reasons"]
    b = market.breadth(p)
    assert b["pct_above_200dma"].between(0, 100).all() and (b["advancers"] + b["decliners"] <= b["stocks"]).all()
    t = market.sector_table(p)
    assert t["sector_rs_6m"].is_monotonic_decreasing and t["sector_rank_pct"].between(0, 100).all()


def test_phase6_api(enriched):
    eng, _ = enriched
    c = TestClient(__import__("app.main", fromlist=["app"]).app)
    assert c.get("/api/intel/rankings").json()["bullish"] == []
    pv = c.get("/api/intel/rankings", params={"preview": True, "limit": 5}).json()
    assert pv["preview"] and len(pv["bullish"]) == 5 and "special" in pv
    m = c.get("/api/intel/market").json()
    assert m["regime"]["label"] and m["breadth"] and m["sectors"]
    s = c.get("/api/intel/stock/DEMO01/score", params={"preview": True}).json()
    assert len(s["factors"]) == len(scoring.FACTORS) and s["scores"]["bullish"]["bullish_score"] is not None
    assert c.get("/api/intel/stock/DEMO01/score").json()["scores"]["bullish"]["bullish_score"] is None
