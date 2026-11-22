from datetime import date

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from intel import db, demo, fundamentals as fa, imports
from intel.panel import build_panel
from intel.research.signals import FILTER_BASE, SIGNALS

ROWS = """symbol,period_end,period_type,metric,value,published_on
{rows}"""


def _write(tmp_path, rows):
    p = tmp_path / "fa.csv"
    p.write_text("symbol,period_end,period_type,metric,value,published_on\n" + "\n".join(rows) + "\n")
    return p


def _quarters(sym, start_rev, growth, pat_margin, shares=10.0, equity=1000.0, debt=200.0, n=8, lag=40):
    rows = []
    qs = pd.date_range("2023-03-31", periods=n, freq="QE")
    rev = start_rev
    for q in qs:
        pub = (q + pd.Timedelta(days=lag)).date()
        pat = rev * pat_margin
        for m, v in (("revenue", rev), ("pat", pat), ("eps", pat / shares), ("total_equity", equity),
                     ("total_debt", debt), ("shares", shares), ("cfo", pat), ("ebitda", rev * 0.2), ("interest", 5)):
            rows.append(f"{sym},{q.date()},Q,{m},{v},{pub}")
        rev *= 1 + growth
        equity += pat
    return rows


def test_import_validates_and_defaults_lag(tmp_path):
    eng = db.engine()
    p = _write(tmp_path, ["ABC,2024-03-31,X,revenue,10,"])
    with pytest.raises(imports.ImportErrorCSV, match="period_type"):
        imports.import_fundamentals(eng, p)
    p.write_text("symbol,period_end,period_type,metric,value\nABC,2024-03-31,Q,revenue,10\nABC,2024-03-31,A,pat,5\n")
    imports.import_fundamentals(eng, p)
    f = fa.load_long(eng).set_index("metric")
    assert f.loc["Q:revenue", "published_on"] == date(2024, 5, 15) and f.loc["A:pat", "published_on"] == date(2024, 5, 30)


def test_point_in_time_ttm_growth_and_ratios(tmp_path):
    eng = db.engine()
    imports.import_fundamentals(eng, _write(tmp_path, _quarters("ABC", 100, 0.05, 0.1)))
    pit = fa.point_in_time(fa.load_long(eng))
    first = pit.iloc[0]
    assert first["effective"] == pd.Timestamp("2023-12-31") + pd.Timedelta(days=40), "needs 4 quarters published"
    last = pit.iloc[-1]
    revs = [100 * 1.05 ** i for i in range(8)]
    assert last["ttm_revenue"] == pytest.approx(sum(revs[4:]))
    assert last["rev_growth_pct"] == pytest.approx((sum(revs[4:]) / sum(revs[:4]) - 1) * 100)
    assert last["de_ratio"] == pytest.approx(200 / last["total_equity"])
    assert 0 < last["roe_pct"] < 10


def test_no_lookahead_on_the_panel(tmp_path):
    eng = db.engine()
    dates = pd.bdate_range("2023-06-01", "2025-06-30")
    px = np.full(len(dates), 100.0)
    db.replace_rows(eng, db.prices_daily, pd.DataFrame({
        "symbol": "ABC", "series": "EQ", "date": dates.date, "open": px, "high": px, "low": px, "close": px,
        "volume": 1e6, "turnover_lacs": 1e3, "deliv_qty": 5e5, "deliv_pct": 50.0}), [db.prices_daily.c.symbol == "ABC"])
    imports.import_fundamentals(eng, _write(tmp_path, _quarters("ABC", 100, 0.05, 0.1)))
    panel = build_panel(eng)
    f = fa.features(eng, panel)
    eff = pd.Timestamp("2024-02-09")  # 31-Dec-2023 + 40 days: first date with 4 published quarters
    assert f.loc[panel["date"] < eff, "pe"].isna().all()
    assert f.loc[panel["date"] >= eff, "pe"].notna().iloc[0]
    row = f[panel["date"] == pd.Timestamp("2024-02-09")].iloc[0]
    eps_ttm = sum(100 * 1.05 ** i * 0.1 / 10 for i in range(4))
    assert row["pe"] == pytest.approx(100 / eps_ttm) and row["mcap_cr"] == pytest.approx(100 * 10)
    # stale after max_age_days without a new filing (last one published 9-Feb-2025)
    short = fa.features(eng, panel, {"fundamentals": {"min_peers": 5, "max_age_days": 100}})
    assert short.loc[panel["date"] > pd.Timestamp("2025-05-20"), "pe"].isna().all()
    assert short.loc[panel["date"] == pd.Timestamp("2025-05-19"), "pe"].notna().all()


def test_financials_skip_leverage_and_use_asset_quality():
    assert fa.is_financial("Financial Services") and not fa.is_financial("FMCG") and not fa.is_financial(None)
    eng = db.engine()
    demo.seed(eng, demo.make(n_symbols=36, days=300))  # 6 per sector ≥ min_peers 5
    panel = build_panel(eng)
    f = fa.features(eng, panel)
    fin = f["fa_financial"].astype(bool)
    assert fin.any() and f.loc[fin, "de_ratio"].isna().all() and f.loc[~fin, "de_ratio"].notna().any()
    assert f.loc[fin, "gnpa_pct"].notna().any()
    q = f["quality_score"].dropna().astype(float)
    assert q.between(0, 100).all() and f["fa_sector_relative"].fillna(False).astype(bool).any()


def test_sector_relative_ranking():
    df = pd.DataFrame({"date": ["d"] * 6, "x": [1, 2, 3, 10, 20, 30]})
    grp = pd.Series(["A"] * 3 + ["B"] * 3)
    pr, used = fa._sector_pct(df, "x", True, grp, min_peers=3)
    assert list(pr) == pytest.approx([100 / 3, 200 / 3, 100, 100 / 3, 200 / 3, 100]) and used.all()
    pr2, used2 = fa._sector_pct(df, "x", True, grp, min_peers=4)
    assert not used2.any() and pr2.iloc[-1] == 100, "too few peers -> universe rank"


def test_filter_signals_are_subsets_of_their_base():
    from intel import evidence
    eng = db.engine()
    demo.seed(eng, demo.make(n_symbols=24, days=300))
    panel = evidence.attach(eng, build_panel(eng))
    for filt, base in FILTER_BASE.items():
        f, b = SIGNALS[filt][0](panel), SIGNALS[base][0](panel)
        assert not (f & ~b).any(), filt


def test_phase5_api():
    eng = db.engine()
    demo.seed(eng, demo.make(n_symbols=24, days=300))
    c = TestClient(__import__("app.main", fromlist=["app"]).app)
    r = c.get("/api/intel/stock/DEMO02/fundamentals").json()
    assert r["latest"]["pe"] > 0 and r["history"] and r["peers"] and r["latest"]["fa_basis"] == "ttm"
    s = c.get("/api/intel/screener", params={"liquid_only": False}).json()["rows"][0]
    assert {"pe", "quality_score", "value_score", "mcap_cr"} <= set(s)
    res = c.post("/api/intel/research/run", params={"signal": ["rs_leaders", "rs_leaders_quality"]}).json()
    assert res["filters"]["rs_leaders_quality"] == "rs_leaders"
