from datetime import date, datetime

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert

from intel import alerts, config, db, demo, paper, setups
from intel.panel import build_panel
from intel.scoring import enrich

CFG = config.load()


def test_cost_model_delivery_round_trip():
    c = setups.costs(100000, 110000, CFG)
    assert c["stt"] == pytest.approx(210000 * 0.001)
    assert c["stamp"] == pytest.approx(100000 * 0.00015)
    assert c["gst"] == pytest.approx((c["brokerage"] + c["exchange"] + c["sebi"]) * 0.18)
    assert c["total"] == pytest.approx(sum(v for k, v in c.items() if k not in ("total", "pct_of_buy")))
    assert 0.2 < c["pct_of_buy"] < 0.6


def _row(**kw):
    base = {"symbol": "X", "industry": "FMCG", "date": pd.Timestamp("2026-01-05"), "high": 102.0, "low": 98.0,
            "close": 101.0, "atr14": 2.0, "vol_avg20": 1e6}
    base.update(kw)
    return pd.Series(base)


def test_setup_stop_targets_and_sizing():
    hist = pd.DataFrame({"low": [97.0] * 10, "high": [103.0] * 10})
    s = setups.setup_for(_row(), hist, "bullish", CFG)
    assert s["trigger"] == 102 and s["stop"] == 98.0, "max(102 - 2*2, swing low 97)"
    assert s["targets"] == [108.0, 114.0] and s["risk_per_share"] == 4
    assert s["qty"] == 980 and s["sized_by"] == "position cap", "risk 5000/4=1250 capped by 10% of 10L at 102"
    short = setups.setup_for(_row(), hist, "bearish", CFG)
    assert short["trigger"] == 98 and short["stop"] == 102 and short["targets"][0] == 92
    thin = setups.setup_for(_row(vol_avg20=50_000), hist, "bullish", CFG)
    assert thin["qty"] == 500 and thin["sized_by"] == "liquidity"


def test_exposure_limits():
    cand = {"symbol": "X", "industry": "FMCG", "position_value": 250000}
    open_ = pd.DataFrame({"symbol": ["A", "B"], "industry": ["FMCG", "FMCG"], "status": ["open", "pending"],
                          "qty": [1000, 1000], "entry": [100.0, None], "trigger": [100.0, 100.0]})
    probs = setups.check_limits(cand, open_, CFG)
    assert any("FMCG exposure" in p for p in probs)
    assert setups.check_limits({**cand, "industry": "IT"}, open_, CFG) == []
    dup = setups.check_limits({**cand, "symbol": "A", "industry": "IT"}, open_, CFG)
    assert any("already holding" in p for p in dup)


def _bars(rows):
    return pd.DataFrame(rows, columns=["date", "open", "high", "low", "close"]).set_index("date")


def _tr(**kw):
    t = {"direction": "bullish", "status": "pending", "trigger": 100.0, "stop": 95.0, "qty": 100,
         "opened": date(2026, 1, 1), "_target": 110.0}
    t.update(kw)
    return t


def test_breakout_fill_gap_and_stop_first():
    b = _bars([(date(2026, 1, 2), 99, 99.5, 98, 99), (date(2026, 1, 5), 101, 103, 100.5, 102)])
    t = paper.step(_tr(), b, CFG)
    assert t["status"] == "open" and t["entry"] == 101 and t["filled"] == date(2026, 1, 5), "gap above trigger fills at open"
    both = _bars([(date(2026, 1, 2), 100, 111, 94, 105)])
    t = paper.step(_tr(), both, CFG)
    assert t["status"] == "closed" and t["exit_reason"] == "stop" and t["exit_price"] == 95, "stop assumed first"
    assert t["pnl"] < -500 and t["r_multiple"] < -1


def test_target_time_exit_gap_stop_and_expiry():
    up = _bars([(date(2026, 1, 2), 100, 101, 99, 100.5), (date(2026, 1, 5), 104, 111, 103, 110)])
    t = paper.step(_tr(), up, CFG)
    assert t["exit_reason"] == "target" and t["exit_price"] == 110 and t["pnl"] > 0
    gap = _bars([(date(2026, 1, 2), 100, 101, 99, 100.5), (date(2026, 1, 5), 90, 92, 89, 91)])
    t = paper.step(_tr(), gap, CFG)
    assert t["exit_reason"] == "stop (gap)" and t["exit_price"] == 90
    flat = _bars([(date(2026, 1, 2), 100, 101, 99, 100.5)] + [(pd.Timestamp("2026-01-05") + pd.offsets.BDay(i), 100, 101, 99, 100.2)
                                                              for i in range(25)])
    t = paper.step(_tr(), flat, CFG)
    assert t["exit_reason"] == "time" and t["exit_price"] == 100.2
    never = _bars([(date(2026, 1, d), 97, 98, 96, 97) for d in (2, 5, 6)])
    assert paper.step(_tr(), never, CFG)["status"] == "expired"


def test_paper_lifecycle_in_db_and_alerts():
    eng = db.engine()
    demo.seed(eng, demo.make(n_symbols=12, days=300))
    panel = enrich(eng, build_panel(eng))
    sym = "DEMO03"
    g = panel[panel["symbol"] == sym]
    # pretend we set it up 40 sessions ago so there are bars to replay
    past = g.iloc[:-40]
    s = setups.setup_for(past.iloc[-1], past, "bullish", CFG)
    res = paper.open_trade(eng, s)
    assert res["ok"] and paper.open_trade(eng, s)["ok"] is False, "duplicate blocked by limits"
    counts = paper.update_all(eng)
    t = paper.trades(eng).iloc[0]
    assert t["status"] in {"expired", "closed", "open"} and counts["unchanged"] == 0
    again = paper.update_all(eng)
    assert again["filled"] == 0 and again["closed"] == 0, "idempotent"
    kinds = set(alerts.recent(eng)["kind"])
    assert "paper_open" in kinds
    rep = paper.report(eng)
    assert rep["capital"] == CFG["trading"]["capital"]


def test_alerts_generate_dedupe_and_telegram():
    eng = db.engine()
    demo.seed(eng, demo.make(n_symbols=24, days=300))
    panel = enrich(eng, build_panel(eng))
    with eng.begin() as c:
        c.execute(insert(db.signal_tests).values(signal_name="factor_relative_strength", rule_version="t",
                                                 run_at=datetime(2026, 1, 1), universe="all", period="",
                                                 stats="{}", passed=1))
    first = alerts.generate(eng, panel)
    second = alerts.generate(eng, panel)
    assert first["ranking_entry"] >= 1 and second == {"special": 0, "ranking_entry": 0, "stale_data": second["stale_data"]}

    class FakeN:
        configured = True
        def __init__(self): self.msgs = []
        def send_now(self, a): self.msgs.append(a["html"])
        def info(self): return {"configured": True}
    n = FakeN()
    out = alerts.send_pending(eng, n)
    assert out["sent"] == len(n.msgs) >= 1 and "not advice" in n.msgs[0]
    assert alerts.send_pending(eng, n)["sent"] == 0, "sent once"


def test_phase7_api():
    eng = db.engine()
    demo.seed(eng, demo.make(n_symbols=12, days=300))
    c = TestClient(__import__("app.main", fromlist=["app"]).app)
    s = c.get("/api/intel/stock/DEMO02/setup").json()
    assert s["qty"] > 0 and s["stop"] < s["trigger"] < s["targets"][0] and "costs_at_target1" in s
    r = c.post("/api/intel/paper", json={"symbol": "DEMO02"}).json()
    assert r["ok"]
    assert c.post("/api/intel/paper", json={"symbol": "DEMO02"}).status_code == 409
    lst = c.get("/api/intel/paper").json()
    assert lst["trades"][0]["status"] == "pending"
    assert c.post(f"/api/intel/paper/{r['id']}/close").json()["status"] == "cancelled"
    assert c.post("/api/intel/paper/auto").json()["opened"] == []  # no factor passed -> no auto trades
    a = c.get("/api/intel/alerts").json()
    assert a["telegram"]["configured"] is False and a["alerts"]
    assert c.post("/api/intel/alerts/test").status_code == 400
    assert c.post("/api/intel/alerts/run", params={"send": False}).json()["generated"]


def test_add_missing_columns_migrates_old_tables(tmp_path):
    import sqlalchemy as sa
    url = f"sqlite:///{tmp_path / 'old.db'}"
    e = sa.create_engine(url)
    with e.begin() as c:
        c.execute(sa.text("CREATE TABLE paper_trades (id INTEGER PRIMARY KEY, symbol VARCHAR(40))"))
    e.dispose()
    db.reset_engines()
    eng = db.engine(url)
    cols = {c["name"] for c in sa.inspect(eng).get_columns("paper_trades")}
    assert {"trigger", "filled", "exit_reason", "r_multiple"} <= cols


def test_setup_without_industry_is_json_safe():
    """Upstox-only data has no industry (NaN): the setup must still serialise (it broke the Trade setup panel)."""
    import json
    hist = pd.DataFrame({"low": [97.0] * 10, "high": [103.0] * 10})
    s = setups.setup_for(_row(industry=float("nan")), hist, "bullish", CFG)
    assert s["industry"] is None and s["valid"]
    json.dumps(s, allow_nan=False)
    assert setups.check_limits({**s}, pd.DataFrame(columns=["symbol", "industry", "status", "qty", "entry", "trigger"]), CFG) == []
