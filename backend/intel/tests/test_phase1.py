from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from intel import config, db, demo, ingest, indicators, parsers
from intel.panel import build_panel, latest_snapshot, load_index
from intel.relative_strength import rs_vs_benchmark
from intel.research import event_study
from intel.research.signals import SIGNALS
from intel.research.walkforward import walk_forward
from intel.sources import DATASETS, Fetcher

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def eng(tmp_path):
    db.reset_engines()
    e = db.engine(f"sqlite:///{tmp_path / 't.db'}")
    yield e
    db.reset_engines()


# ------------------------------------------------------------------ parsers --- #
def test_cm_delivery_real_file():
    df = parsers.cm_delivery((FIX / "sec_bhavdata_full_04092024.csv").read_bytes())
    r = df[df["symbol"] == "20MICRONS"].iloc[0]
    assert r["date"] == date(2024, 9, 4) and r["close"] == 327.2 and r["deliv_qty"] == 249664
    assert r["deliv_pct"] == 33.05
    assert df[df["symbol"] == "21STCENMGM"]["deliv_qty"].isna().all(), "'-' means no delivery data"
    assert set(df["series"]) <= {"EQ", "BE", "BZ"} and "1018GS2026" not in set(df["symbol"])


def test_cm_delivery_rejects_garbage():
    with pytest.raises(parsers.ParseError, match="missing columns"):
        parsers.cm_delivery(b"A,B\n1,2\n")


def test_index_close_dedupes_and_parses_dates():
    df = parsers.index_close((FIX / "ind_close_all_02012023.csv").read_bytes())
    n50 = df[df["index_name"] == "Nifty 50"]
    assert len(n50) == 1 and n50.iloc[0]["close"] == 18197.45 and n50.iloc[0]["date"] == date(2023, 1, 2)


def test_other_formats():
    fo = parsers.fo_udiff((FIX / "BhavCopy_NSE_FO_sample.csv").read_bytes())
    assert set(fo["instrument"]) <= {"STF", "IDF", "STO", "IDO"} and fo["lot_size"].iloc[0] == 2325
    poi = parsers.participant_oi((FIX / "fao_participant_oi_19082022.csv").read_bytes())
    fii = poi[poi["client_type"] == "FII"].iloc[0]
    assert poi["date"].iloc[0] == date(2022, 8, 19) and fii["fut_idx_long"] == 93581 and fii["total_short"] == 1653206
    bulk = parsers.deals((FIX / "bulk_11072023.csv").read_bytes(), "bulk")
    assert len(bulk) > 50 and bulk.iloc[0]["client"] == "VEENA RAJESH SHAH" and bulk.iloc[0]["side"] == "BUY"
    assert parsers.deals((FIX / "block_11072023.csv").read_bytes(), "block").empty, "NO RECORDS day"
    members = parsers.index_list((FIX / "ind_nifty500list.csv").read_bytes())
    assert members.iloc[0].to_dict()["symbol"] == "360ONE"


# ------------------------------------------------------------------- ingest --- #
class FakeSession:
    def __init__(self, files):
        self.files, self.calls = files, []

    def get(self, url, **kw):
        self.calls.append(url)

        class R:
            pass
        r = R()
        body = self.files.get(url)
        r.status_code, r.content = (200, body) if body is not None else (404, b"")
        return r


def test_ingest_day_holiday_cache_and_provenance(eng, tmp_path):
    day = date(2024, 9, 4)
    url = DATASETS["cm"].url(day)
    s = FakeSession({url: (FIX / "sec_bhavdata_full_04092024.csv").read_bytes()})
    f = Fetcher(session=s, cache_dir=tmp_path / "raw", min_interval=0, retries=0)
    assert ingest.load_day(eng, f, "cm", day) == "ok"
    assert ingest.load_day(eng, f, "cm", day) == "skipped", "already loaded"
    assert ingest.load_day(eng, f, "cm", date(2024, 9, 5)) == "holiday", "404 → holiday"
    assert ingest.load_day(eng, f, "cm", day, force=True) == "ok" and s.calls.count(url) == 1, "raw cache reused"
    rows = db.read_sql(eng, select(db.prices_daily))
    assert len(rows) == 17 and rows["source_id"].notna().all()
    src = db.read_sql(eng, select(db.source_files))
    assert set(src["status"]) == {"ok", "holiday"} and src[src.status == "ok"]["sha256"].str.len().iloc[0] == 64
    assert list(ingest.weekdays(date(2026, 11, 20), date(2026, 11, 23))) == [date(2026, 11, 20), date(2026, 11, 23)]


def test_ingest_records_parse_errors(eng, tmp_path):
    day = date(2024, 9, 4)
    s = FakeSession({DATASETS["cm"].url(day): b"not,a,bhavcopy\n1,2,3\n"})
    f = Fetcher(session=s, cache_dir=tmp_path / "raw", min_interval=0, retries=0)
    assert ingest.load_day(eng, f, "cm", day) == "error"
    err = db.read_sql(eng, select(db.source_files)).iloc[0]
    assert err["status"] == "error" and "missing columns" in err["error"]


# --------------------------------------------------------------- indicators --- #
def bars(n=300, seed=1):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.015, n)))
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame({"open": c * 0.998, "high": c * 1.01, "low": c * 0.99, "close": c,
                         "volume": rng.uniform(1e5, 2e5, n), "deliv_qty": 5e4, "deliv_pct": 40.0}, index=idx)


def test_indicator_values():
    up = pd.Series(np.arange(1, 60, dtype=float))
    assert indicators.rsi(up).iloc[-1] == 100.0
    df = bars()
    out = indicators.compute_all(df)
    assert out["rsi14"].between(0, 100).all() or out["rsi14"].dropna().between(0, 100).all()
    assert (out["bb_upper"].dropna() >= out["bb_lower"].dropna()).all()
    assert out["dma200"].iloc[198] != out["dma200"].iloc[198] and not np.isnan(out["dma200"].iloc[199])
    assert set(out["supertrend_dir"].unique()) <= {-1, 0, 1}
    # Donchian uses PREVIOUS sessions -> today's close can exceed it (a breakout)
    assert out["dc_upper"].iloc[25] == df["high"].iloc[5:25].max()
    # RVOL uses the previous 20 sessions
    assert out["rvol20"].iloc[30] == pytest.approx(df["volume"].iloc[30] / df["volume"].iloc[10:30].mean())


def test_indicators_have_no_lookahead():
    df = bars(260)
    full = indicators.compute_all(df)
    part = indicators.compute_all(df.iloc[:200])
    pd.testing.assert_frame_equal(full.iloc[:200], part, check_exact=False, rtol=1e-9)


def test_relative_strength():
    idx = pd.bdate_range("2024-01-01", periods=30)
    bench = pd.Series(np.linspace(100, 110, 30), index=idx)
    stock = bench * 1.5 * np.linspace(1, 1.2, 30)  # outperforms by 20%
    rs = rs_vs_benchmark(stock, bench, {"20d": 20})
    assert rs["rs_ratio"].iloc[0] == 100 and rs["rs_ratio"].iloc[-1] == pytest.approx(120)
    assert rs["rs_20d"].iloc[-1] > 0 and rs["rs_mom_20"].iloc[-1] > 0


# ------------------------------------------------------------------ panel ---- #
def test_panel_and_snapshot_on_demo(eng):
    demo.seed(eng, demo.make(n_symbols=24, days=320))
    cfg = config.load()
    panel = build_panel(eng, cfg)
    assert {"rs_rank", "liquid", "rsi14", "rs_6m", "industry"} <= set(panel.columns)
    assert panel.groupby("date")["rs_rank"].max().dropna().le(100).all()
    snap = latest_snapshot(panel)
    assert snap["date"].nunique() == 1 and len(snap) == 24
    assert snap["rs_rank"].dropna().is_monotonic_decreasing


def test_universe_selection(eng):
    demo.seed(eng, demo.make(n_symbols=60, days=260))
    cfg = config.load()
    cfg["universe"]["name"] = "nifty50"
    assert build_panel(eng, cfg)["symbol"].nunique() == 50
    cfg["universe"]["name"], cfg["universe"]["custom"] = "custom", ["DEMO01", "DEMO02"]
    assert set(build_panel(eng, cfg)["symbol"]) == {"DEMO01", "DEMO02"}


def test_config_override_without_code(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"liquidity": {"min_avg_traded_value_cr": 25}}')
    c = config.load(p)
    assert c["liquidity"]["min_avg_traded_value_cr"] == 25 and c["liquidity"]["min_history_sessions"] == 200


# ---------------------------------------------------------------- research --- #
def _panel_with_effect(effect_pct: float, n_sym=80, days=500, seed=3):
    """Random stocks; on 'signal' days a stock then outperforms by effect_pct over 20 sessions."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=days)
    frames = []
    sig_rows = []
    for i in range(n_sym):
        r = rng.normal(0.0003, 0.012, days)
        flag = rng.random(days) < 0.01
        for t in np.where(flag)[0]:
            r[t + 1:t + 21] += effect_pct / 100 / 20
        c = 100 * np.exp(np.cumsum(r))
        frames.append(pd.DataFrame({"date": dates, "symbol": f"S{i}", "open": c, "close": c}))
        sig_rows.append(flag)
    panel = pd.concat(frames, ignore_index=True)
    mask = pd.Series(np.concatenate(sig_rows), index=panel.index)
    bench = pd.Series(100 * np.exp(np.cumsum(np.full(days, 0.0003))), index=dates)
    return panel, mask, bench


def test_event_study_finds_a_real_effect():
    panel, mask, bench = _panel_with_effect(4.0)
    rules = {**config.DEFAULTS["research"], "pass_min_events": 100}
    res = event_study.run(panel, mask, bench, rules, "bullish")
    s = res["horizons"][20]
    assert s["mean_excess_pct"] > 2 and s["ci95"][0] > 0 and res["passed"], res["verdict"]


def test_event_study_rejects_noise():
    panel, mask, bench = _panel_with_effect(0.0)
    rules = {**config.DEFAULTS["research"], "pass_min_events": 100}
    res = event_study.run(panel, mask, bench, rules, "bullish")
    assert not res["passed"], "pure noise must not pass"


def test_bearish_signals_are_measured_as_shorts():
    panel, mask, bench = _panel_with_effect(-4.0)
    rules = {**config.DEFAULTS["research"], "pass_min_events": 100}
    assert event_study.run(panel, mask, bench, rules, "bearish")["horizons"][20]["mean_excess_pct"] > 2


def test_entry_is_next_open_and_costs_apply():
    dates = pd.bdate_range("2024-01-01", periods=30)
    panel = pd.DataFrame({"date": dates, "symbol": "X", "open": 100.0, "close": 100.0})
    panel.loc[1:, "open"] = 110.0
    panel.loc[1:, "close"] = 110.0  # jump happens overnight -> not capturable
    bench = pd.Series(100.0, index=dates)
    mask = pd.Series(False, index=panel.index)
    mask.iloc[0] = True
    rules = {**config.DEFAULTS["research"], "horizons": [5], "pass_horizon": 5, "pass_min_events": 1}
    res = event_study.run(panel, mask, bench, rules)
    assert res["horizons"][5]["mean_excess_pct"] == pytest.approx(-0.30), "0% move minus 30 bps costs"


def test_first_in_run_and_cooldown():
    panel = pd.DataFrame({"symbol": ["A"] * 10, "date": pd.bdate_range("2024-01-01", periods=10)})
    m = pd.Series([1, 1, 0, 1, 0, 0, 0, 1, 1, 0], dtype=bool)
    assert list(event_study.first_in_run(m, panel, 0)[lambda s: s].index) == [0, 3, 7]
    assert list(event_study.first_in_run(m, panel, 4)[lambda s: s].index) == [0, 7]


def test_walk_forward_picks_the_working_variant():
    panel, good, bench = _panel_with_effect(4.0, days=700)
    rng = np.random.default_rng(9)
    bad = pd.Series(rng.random(len(panel)) < 0.01, index=panel.index)
    rules = {**config.DEFAULTS["research"], "pass_min_events": 50}
    res = walk_forward(panel, {"good": good, "bad": bad}, bench, rules, train_days=250, test_days=100)
    assert res["windows"] and all(w["variant"] == "good" for w in res["windows"])
    assert res["horizons"][20]["mean_excess_pct"] > 1


def test_all_phase1_signals_run_on_demo(eng):
    demo.seed(eng, demo.make(n_symbols=30, days=400))
    cfg = config.load()
    panel = build_panel(eng, cfg)
    bench = load_index(eng, "Nifty 500")
    for name, (fn, direction, _) in SIGNALS.items():
        res = event_study.run(panel, fn(panel), bench, cfg["research"], direction)
        assert "passed" in res and res["events"] >= 0, name
