from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from intel import db, demo, entities, evidence, imports
from intel.panel import build_panel, latest_snapshot
from intel.research.signals import SIGNALS, EVIDENCE_CLASS

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def eng():
    return db.engine()


# ---------------------------------------------------------------- entities --- #
@pytest.mark.parametrize("name,cls", [
    ("HDFC MUTUAL FUND", "mutual_fund"),
    ("ICICI PRUDENTIAL MUTUAL FUND A/C ICICI PRUDENTIAL VALUE FUND", "mutual_fund"),
    ("SBI LIFE INSURANCE CO. LTD", "insurer"),
    ("GOVERNMENT OF SINGAPORE", "fpi"),
    ("EMPLOYEES PROVIDENT FUND ORGANISATION", "pension"),
    ("STATE BANK OF INDIA", "bank"),
    ("NK SECURITIES RESEARCH PRIVATE LIMITED", "broker_prop"),
    ("WAYBROAD TRADING PRIVATE LIMITED", "broker_prop"),
    ("VEENA RAJESH SHAH", "individual"),
])
def test_entity_rules(name, cls):
    assert entities.classify_name(name)[0] == cls


def test_real_bulk_deal_clients_are_not_called_institutions(eng):
    """None of the 11-Jul-2023 bulk-deal clients is an institution — the classifier must not invent one."""
    from intel import parsers
    d = parsers.deals((FIX / "bulk_11072023.csv").read_bytes(), "bulk")
    cl = entities.classify(eng, d["client"])
    assert not cl["institutional"].any(), cl[cl["institutional"]]


def test_manual_override_wins(eng):
    assert entities.classify(eng, ["BOFA SECURITIES EUROPE SA"]).iloc[0]["entity_class"] == "broker_prop"
    entities.set_manual(eng, "BOFA SECURITIES EUROPE SA", "fpi", "SEBI-registered FPI")
    r = entities.classify(eng, ["BOFA SECURITIES EUROPE SA"]).iloc[0]
    assert r["entity_class"] == "fpi" and r["method"] == "manual" and r["institutional"]


# ----------------------------------------------------------------- imports --- #
def test_shareholding_import_defaults_publication_lag(eng, tmp_path):
    p = tmp_path / "sh.csv"
    p.write_text("symbol,quarter_end,category,pct\nABC,2024-03-31,fpi,10\nABC,2024-03-31,dii,5\n"
                 "ABC,2024-06-30,fpi,11.2\nABC,2024-06-30,dii,5\n")
    assert imports.import_shareholding(eng, p) == 4
    oc = evidence.ownership_changes(eng, "ABC")
    last = oc.iloc[-1]
    assert last["published_on"] == date(2024, 7, 21) and last["inst_change_pp"] == pytest.approx(1.2)
    src = db.read_sql(eng, select(db.source_files)).iloc[0]
    assert src["dataset"] == "shareholding_import" and len(src["sha256"]) == 64


def test_imports_validate(eng, tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("symbol,quarter_end,category,pct\nABC,2024-03-31,whales,10\n")
    with pytest.raises(imports.ImportErrorCSV, match="unknown categories"):
        imports.import_shareholding(eng, p)
    p.write_text("symbol,month\nA,1\n")
    with pytest.raises(imports.ImportErrorCSV, match="missing columns"):
        imports.import_mf(eng, p)


def test_deals_and_fii_dii_imports(eng, tmp_path):
    assert imports.import_deals(eng, FIX / "bulk_11072023.csv", "bulk") > 50
    p = tmp_path / "f.csv"
    p.write_text("date,category,buy_value,sell_value,net_value\n2024-01-02,FII,100,50,50\n2024-01-02,DII,10,20,-10\n")
    assert imports.import_fii_dii(eng, p) == 2
    f = evidence.market_flows(eng)
    assert f.iloc[-1]["FII"] == 50 and f.iloc[-1]["DII"] == -10


# ---------------------------------------------------------------- evidence --- #
def _one_stock(n=120, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    return pd.DataFrame({"symbol": "S", "series": "EQ", "date": dates.date, "prev_close": c, "open": c, "high": c * 1.01,
                         "low": c * 0.99, "last": c, "close": c, "avg_price": c, "volume": 1e6,
                         "turnover_lacs": 1e6 * c / 1e5, "trades": 1000, "deliv_qty": 4e5, "deliv_pct": 40.0})


def test_ownership_event_lands_on_publication_not_quarter_end(eng, tmp_path):
    prices = _one_stock(200)
    db.replace_rows(eng, db.prices_daily, prices, [db.prices_daily.c.symbol == "S"])
    p = tmp_path / "sh.csv"
    p.write_text("symbol,quarter_end,category,pct,published_on\nS,2024-03-31,fpi,10,2024-04-20\n"
                 "S,2024-06-30,fpi,11,2024-07-20\n")
    imports.import_shareholding(eng, p)
    panel = evidence.attach(eng, build_panel(eng))
    fired = panel.loc[panel["b_inst_up"], "date"]
    assert list(fired.dt.date) == [date(2024, 7, 22)], "first session on/after 20-Jul-2024 (a Saturday)"
    before = panel[panel["date"] < pd.Timestamp("2024-07-22")]
    assert before["b_inst_change_pp_last"].isna().all(), "no look-ahead: unknown before publication"


def test_inferred_subsignals_on_constructed_accumulation():
    """Rising delivery on up-closes with heavy up-volume must light up class-D clues."""
    df = _one_stock(160, seed=2)
    df["date"] = pd.to_datetime(df["date"])
    up = np.arange(len(df)) >= 145
    df.loc[up, "deliv_qty"] = 9e5
    df.loc[up, "deliv_pct"] = 75.0
    df.loc[up, "close"] = df.loc[up, "high"] * 0.999   # closes near the high
    df.loc[up, "volume"] = np.where(np.arange(up.sum()) % 2 == 0, 3e6, 1e6)
    from intel.indicators import compute_all
    ind = compute_all(df.set_index("date")).reset_index()
    panel = pd.concat([df[["symbol", "high", "low", "close", "volume"]], ind.drop(columns=["date"]), df[["date"]]], axis=1)
    d = evidence.inferred(panel)
    last = d.iloc[-1]
    assert last["d_deliv_trend"] and last["d_hidel_updays"] and last["d_cmf"]
    assert last["d_score"] >= 3 and last["x_score"] <= 1


def test_institutional_deals_are_class_a_others_class_e(eng):
    rows = pd.DataFrame([
        {"date": date(2024, 5, 2), "kind": "bulk", "symbol": "S", "client": "HDFC MUTUAL FUND", "side": "BUY",
         "qty": 1e6, "price": 100.0},
        {"date": date(2024, 5, 2), "kind": "bulk", "symbol": "S", "client": "QE SECURITIES LLP", "side": "SELL",
         "qty": 1e6, "price": 100.0}])
    db.replace_rows(eng, db.deals, rows, [db.deals.c.symbol == "S"])
    d = evidence.deals_classified(eng, symbol="S").set_index("client")
    assert d.loc["HDFC MUTUAL FUND", "evidence_class"] == "A" and d.loc["QE SECURITIES LLP", "evidence_class"] == "E"
    db.replace_rows(eng, db.prices_daily, _one_stock(120), [db.prices_daily.c.symbol == "S"])
    panel = evidence.attach(eng, build_panel(eng))
    day = panel[panel["date"] == pd.Timestamp("2024-05-02")].iloc[0]
    assert day["a_inst_buy"] and day["a_inst_net_cr"] == pytest.approx(10.0) and day["e_other_deal"]


def test_scores_need_passed_components(eng):
    demo.seed(eng, demo.make(n_symbols=20, days=300))
    panel = evidence.attach(eng, build_panel(eng))
    snap = latest_snapshot(panel)
    gated = evidence.scores(snap, panel, passed=set())
    assert gated["accumulation_score"].isna().all(), "no component has passed -> no score"
    assert set(gated["accumulation_confidence"].round(2)) <= {0.85, 1.0}, "A+B+D always; F only for F&O stocks"
    only_d = evidence.scores(snap, panel, passed={"possible_accumulation"})
    np.testing.assert_allclose(only_d["accumulation_score"], snap["d_score"] / 5 * 100)


def test_every_signal_has_an_evidence_class_and_runs(eng):
    assert set(SIGNALS) == set(EVIDENCE_CLASS)
    demo.seed(eng, demo.make(n_symbols=20, days=300))
    panel = evidence.attach(eng, build_panel(eng))
    for name, (fn, _, _) in SIGNALS.items():
        m = fn(panel)
        assert m.index.equals(panel.index) and m.dtype == bool, name
    assert evidence.store(eng, panel, days=3) >= 0


def test_phase2_api(eng):
    demo.seed(eng, demo.make(n_symbols=20, days=300))
    c = TestClient(__import__("app.main", fromlist=["app"]).app)
    r = c.get("/api/intel/institutional", params={"days": 365}).json()
    assert r["demo"] and r["flows"] and "say nothing" in r["flows_note"]
    assert {d["evidence_class"] for d in r["deals"]} <= {"A", "E"} and r["accumulation"]
    sym = r["accumulation"][0]["symbol"]
    e = c.get(f"/api/intel/stock/{sym}/evidence").json()
    assert set(e["subsignals"]) == {"accumulation", "distribution"} and e["ownership"]
    assert all(i["class"] in "ABCDEF" for i in e["items"])
    ents = c.get("/api/intel/entities").json()["entities"]
    assert any(x["entity_class"] == "mutual_fund" for x in ents)
    assert c.put("/api/intel/entities", json={"name": "X", "entity_class": "whale"}).status_code == 422
    assert c.put("/api/intel/entities", json={"name": "DEMO TRADING LLP", "entity_class": "fpi"}).json()["ok"]
    s = c.get("/api/intel/screener", params={"liquid_only": False}).json()
    assert "accumulation_confidence" in s["rows"][0]
