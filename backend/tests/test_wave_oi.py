import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.nse_data import parse_futures_oi_rows, parse_price_rows
from app.wave_oi import (
    Pivot, StrategyConfig, analyze, find_impulses, make_synthetic, run_backtest,
    setup_from_pivots, zigzag,
)
from app.wave_oi.gti import compute_gti
from app.wave_oi.oi import oi_regime


def _pivots(prices, start_kind="L"):
    kinds = ["L", "H"] if start_kind == "L" else ["H", "L"]
    return [Pivot(i * 10, float(p), kinds[i % 2], i * 10 + 3) for i, p in enumerate(prices)]


@pytest.fixture(scope="module")
def synth():
    return make_synthetic(600, seed=7)


# ---------------------------------------------------------------- waves --- #
def test_zigzag_alternates_and_is_causal(synth):
    piv = zigzag(synth)
    assert len(piv) > 10
    assert all(a.kind != b.kind for a, b in zip(piv, piv[1:]))
    assert all(p.confirmed_at > p.idx for p in piv)
    assert all(a.confirmed_at <= b.confirmed_at for a, b in zip(piv, piv[1:]))


def test_textbook_up_impulse_is_found():
    # w1=100, w2 retraces 61.8%, w3=161.8, w4 retraces 38.2%, w5=100
    p = [100, 200, 138.2, 300, 238.1, 338.1]
    imps = find_impulses(_pivots(p))
    assert len(imps) == 1 and imps[0]["direction"] == 1
    assert imps[0]["score"] > 0.95


def test_wave4_overlap_breaks_impulse():
    p = [100, 200, 138.2, 300, 190, 320]   # wave 4 low (190) below wave 1 high (200)
    assert find_impulses(_pivots(p)) == []


def test_wave3_shortest_breaks_impulse():
    p = [100, 200, 150, 240, 215, 330]     # w1=100, w3=90, w5=115 -> w3 shortest
    assert find_impulses(_pivots(p)) == []


def test_down_impulse_mirrors():
    p = [338.1, 238.1, 300, 138.2, 200, 100]
    imps = find_impulses(_pivots(p, start_kind="H"))
    assert len(imps) == 1 and imps[0]["direction"] == -1


def test_wave3_setup_levels():
    s = setup_from_pivots(_pivots([100, 200, 138.2]))
    assert s.label == "W3" and s.direction == 1
    assert s.target == pytest.approx(138.2 + 161.8)
    assert s.invalidation == 100 and s.stop == pytest.approx(138.2)


def test_wave5_setup_levels():
    s = setup_from_pivots(_pivots([100, 200, 138.2, 300, 238.1]))
    assert s.label == "W5" and s.direction == 1
    assert s.target == pytest.approx(338.1)
    assert s.invalidation == 200


def test_completed_impulse_expects_correction():
    s = setup_from_pivots(_pivots([100, 200, 138.2, 300, 238.1, 338.1]))
    assert s.label == "ABC" and s.direction == -1
    assert s.invalidation == pytest.approx(338.1)


# ------------------------------------------------------------- OI / GTI --- #
def test_oi_regime_quadrants():
    idx = pd.RangeIndex(4)
    df = pd.DataFrame({"close": [100, 110, 100, 90], "open_interest": [100, 120, 100, 120]}, index=idx)
    reg = oi_regime(df, lookback=1)["oi_regime"].tolist()
    assert reg[1:] == ["new_longs", "long_liquidation", "new_shorts"]
    df["close"] = [100, 110, 120, 110]
    df["open_interest"] = [100, 90, 80, 70]
    assert oi_regime(df, lookback=1)["oi_regime"].tolist()[1:] == ["short_covering", "short_covering", "long_liquidation"]


def test_gti_flips_with_trend():
    up = np.linspace(100, 200, 60)
    down = np.linspace(200, 100, 60)
    close = np.concatenate([up, down])
    df = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close})
    g = compute_gti(df)
    assert g["gti_dir"].iloc[50] == 1
    assert g["gti_dir"].iloc[-1] == -1
    assert (g["gti_flip"] == -1).sum() >= 1


# ------------------------------------------------------- strategy / bt --- #
def test_signals_have_no_lookahead(synth):
    full, _ = analyze(synth)
    for cut in (200, 333, 450, 599):
        part, _ = analyze(synth.iloc[:cut])
        cols = ["signal", "wave_label", "gti_dir", "oi_regime"]
        pd.testing.assert_frame_equal(part[cols], full[cols].iloc[:cut])


def test_signal_levels_are_on_the_right_side(synth):
    an, _ = analyze(synth)
    sig = an[an["signal"] != 0]
    assert len(sig) > 0
    for _, r in sig.iterrows():
        s = r["signal"]
        assert s * (r["close"] - r["stop"]) > 0
        assert s * (r["target"] - r["close"]) > 0


def test_runs_without_open_interest(synth):
    an, _ = analyze(synth.drop(columns="open_interest"))
    assert (an["oi_regime"] == "n/a").all()


def test_backtest_accounting(synth):
    an, _ = analyze(synth, StrategyConfig(min_rr=1.0))
    bt = run_backtest(an)
    m = bt["metrics"]
    assert m["trades"] == len(bt["trades"]) > 0
    assert len(bt["equity"]) == len(an)
    total_pnl = sum(t["pnl"] for t in bt["trades"])
    assert m["final_equity"] == pytest.approx(100_000 + total_pnl)
    for t in bt["trades"]:
        assert t["entry_date"] <= t["exit_date"]


# ------------------------------------------------------------ NSE parse --- #
def test_parse_nse_rows():
    price = parse_price_rows([
        {"CH_TIMESTAMP": "2024-01-02", "CH_OPENING_PRICE": 10, "CH_TRADE_HIGH_PRICE": 12,
         "CH_TRADE_LOW_PRICE": 9, "CH_CLOSING_PRICE": 11, "CH_TOT_TRADED_QTY": 1000},
        {"CH_TIMESTAMP": "2024-01-01", "CH_OPENING_PRICE": 9, "CH_TRADE_HIGH_PRICE": 10,
         "CH_TRADE_LOW_PRICE": 8, "CH_CLOSING_PRICE": 10, "CH_TOT_TRADED_QTY": 900},
    ])
    assert list(price["close"]) == [10, 11]
    oi = parse_futures_oi_rows([
        {"FH_TIMESTAMP": "01-Jan-2024", "FH_EXPIRY_DT": "25-Jan-2024", "FH_OPEN_INT": "100"},
        {"FH_TIMESTAMP": "01-Jan-2024", "FH_EXPIRY_DT": "29-Feb-2024", "FH_OPEN_INT": "50"},
        {"FH_TIMESTAMP": "02-Jan-2024", "FH_EXPIRY_DT": "25-Jan-2024", "FH_OPEN_INT": "120"},
    ])
    assert list(oi) == [150, 120]


# ------------------------------------------------------------------ API --- #
def test_api_synthetic_report():
    client = TestClient(app)
    r = client.get("/api/wave-oi/DEMO", params={"source": "synthetic", "days": 900})
    assert r.status_code == 200
    body = r.json()
    assert body["oi_available"] is True
    assert set(body["latest"]) >= {"wave", "gti", "oi", "signal_today"}
    assert len(body["chart"]["dates"]) == len(body["chart"]["close"])
    assert "metrics" in body["backtest"]


def test_api_rejects_bad_params():
    client = TestClient(app)
    assert client.get("/api/wave-oi/DEMO", params={"source": "bogus"}).status_code == 422
