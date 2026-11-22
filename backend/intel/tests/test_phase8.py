import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from intel import backtest as bt, config, db, demo
from intel.panel import build_panel
from intel.research import event_study
from intel.scoring import enrich

CFG = config.load()


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    db.reset_engines()
    eng = db.engine(f"sqlite:///{tmp_path_factory.mktemp('bt') / 'bt.db'}")
    demo.seed(eng, demo.make(n_symbols=30, days=650))
    return eng, enrich(eng, build_panel(eng))


def test_metrics_on_known_series():
    idx = pd.bdate_range("2024-01-01", periods=251)
    eq = pd.Series(100 * 1.1 ** (np.arange(251) / 250), index=idx)
    m = bt.metrics(eq, rf_pct=0)
    assert m["cagr_pct"] == pytest.approx(10, abs=0.05) and m["max_drawdown_pct"] == pytest.approx(0)
    dd = eq.copy()
    dd.iloc[100:] *= 0.8
    assert bt.metrics(dd)["max_drawdown_pct"] == pytest.approx(-20, abs=0.1)
    vs = bt.metrics(eq, 0, {"B": eq})["vs_B"]
    assert vs["excess_cagr_pct"] == pytest.approx(0, abs=1e-9) and vs["beta"] == pytest.approx(1)


def test_tiered_slippage():
    liquid = bt._side_cost_pct(1e5, 500, CFG)
    mid = bt._side_cost_pct(1e5, 20, CFG)
    assert mid - liquid == pytest.approx((30 - 10) / 1e4, rel=1e-6), "slippage is per side, tiered by liquidity"


def test_ranked_trades_next_open_and_costs_hurt(world):
    eng, p = world
    free = {**CFG, "costs": {**CFG["costs"], "brokerage_per_order": 0, "stt_pct": 0, "exchange_pct": 0, "sebi_per_crore": 0,
                             "stamp_pct": 0, "dp_per_sell": 0, "slippage_bps_liquid": 0, "slippage_bps_mid": 0}}
    a = bt.ranked(p, ["relative_strength"], 10, 5, free)
    b = bt.ranked(p, ["relative_strength"], 10, 5, CFG)
    c2 = bt.ranked(p, ["relative_strength"], 10, 5, CFG, cost_mult=3)
    assert a["equity"].iloc[-1] > b["equity"].iloc[-1] > c2["equity"].iloc[-1]
    assert a["equity"].iloc[0] == CFG["trading"]["capital"], "record starts in cash the session before the first fill"
    assert a["equity"].index[0] > sorted(p["date"].unique())[0], "warm-up period is not counted"
    assert 0 < a["exposure_pct"] <= 100 and a["turnover"] > 0


def test_ranked_no_lookahead(world):
    eng, p = world
    dates = sorted(p["date"].unique())
    cut = dates[400]
    full = bt.ranked(p, ["momentum"], 10, 5, CFG)["equity"]
    part = bt.ranked(p[p["date"] <= cut], ["momentum"], 10, 5, CFG)["equity"]
    common = part.index[:-6]      # the last rebalance in `part` can't trade (no t+1 open)
    pd.testing.assert_series_equal(full.loc[common], part.loc[common], check_names=False, rtol=1e-9)


def test_signal_portfolio_respects_max_positions(world):
    eng, p = world
    cfg = {**CFG, "backtest": {**CFG["backtest"], "max_positions": 2}}
    mask = p["liquid"].fillna(False) & (p["rvol20"] >= 1.2)
    r = bt.signal_portfolio(p, mask, "bullish", cfg)
    tl = r["trade_log"]
    assert len(tl) > 0
    for d in pd.date_range(tl["signal_date"].min(), tl["closed"].max(), freq="7D"):
        live = ((tl["signal_date"] < d) & (tl["closed"] > d)).sum()
        assert live <= 2
    assert set(tl["reason"]) <= {"stop", "stop (gap)", "target", "time", "end"}


def test_walk_forward_and_sensitivity(world):
    eng, p = world
    cfg = {**CFG, "backtest": {**CFG["backtest"], "train_sessions": 300, "test_sessions": 100}}
    wf = bt.walk_forward_ranked(p, {"rs": ["relative_strength"], "mom": ["momentum"]}, cfg)
    assert wf["windows"] and all(w["chosen"] in {"rs", "mom"} for w in wf["windows"])
    first_test = pd.Timestamp(wf["windows"][0]["test"].split("..")[0])
    assert wf["equity"].index.min() >= first_test, "only out-of-sample equity is reported"
    s = bt.sensitivity(p, ["relative_strength"], CFG, top_ks=(5, 10), rebalances=(5,), cost_mults=(1.0,))
    assert len(s["grid"]) == 2 and isinstance(s["fragile"], bool)


def test_event_study_by_year_regime_bootstrap():
    rng = np.random.default_rng(0)
    dates = pd.bdate_range("2022-01-03", periods=700)
    frames = []
    for i in range(40):
        c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 700)))
        frames.append(pd.DataFrame({"date": dates, "symbol": f"S{i}", "open": c, "close": c}))
    panel = pd.concat(frames, ignore_index=True)
    mask = pd.Series(rng.random(len(panel)) < 0.02, index=panel.index)
    bench = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0003, 0.008, 700))), index=dates)
    rules = {**CFG["research"], "pass_min_events": 50}
    s = event_study.run(panel, mask, bench, rules)["horizons"][20]
    assert set(s["by_year"]) >= {"2022", "2023"} and set(s["by_regime"]) <= {"up", "down"}
    lo, hi = s["bootstrap_ci95"]
    assert lo < s["day_mean_pct"] < hi and 0 <= s["top_month_share"] <= 1


def test_phase8_api(world):
    eng, _ = world
    db.reset_engines()
    import os
    os.environ["DATABASE_URL"] = str(eng.url)
    c = TestClient(__import__("app.main", fromlist=["app"]).app)
    r = c.post("/api/intel/backtests", json={"kind": "ranked", "factors": ["relative_strength", "momentum"], "top_k": 8}).json()
    assert r["id"] and r["metrics"]["cagr_pct"] is not None and "Nifty 500" in r["series"]["benchmarks"]
    assert c.get(f"/api/intel/backtests/{r['id']}").json()["name"].startswith("ranked")
    lst = c.get("/api/intel/backtests").json()
    assert lst["runs"][0]["id"] == r["id"] and "relative_strength" in lst["factors"]
    assert c.post("/api/intel/backtests", json={"kind": "ranked", "factors": ["nope"]}).status_code == 422
    assert c.post("/api/intel/backtests", json={"kind": "signal", "signal": "nope"}).status_code == 422
    s = c.post("/api/intel/backtests", json={"kind": "signal", "signal": "breakout_volume"}).json()
    assert "hit_rate" in s["metrics"] or s["metrics"].get("trades") == 0
