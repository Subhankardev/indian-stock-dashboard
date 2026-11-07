import math
from datetime import date

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import options_suggest as os_
from app.alerts.engine import AlertScanner, AlertStore
from app.alerts.telegram import format_message
from app.main import app

TODAY = date(2026, 11, 9)            # a Monday
EXPIRY = "2026-11-19"                # 8 trading days later


def chain(spot=100.0, iv=20.0, step=2.5, n=8, expiry=EXPIRY, oi=None, spread=0.01):
    years = os_._years(TODAY, expiry)
    rows = []
    for i in range(-n, n + 1):
        k = spot + i * step
        row = {"strike": k}
        for kind in ("CE", "PE"):
            p = os_.bs_price(spot, k, years, iv / 100, kind)
            o = (oi or {}).get((k, kind), 1000)
            row[kind] = {"ltp": round(p, 2), "bid": round(p * (1 - spread / 2), 2), "ask": round(p * (1 + spread / 2), 2),
                         "iv": iv, "delta": None, "theta": None, "oi": o, "key": f"K|{k}{kind}"}
        rows.append(row)
    return {"symbol": "X", "expiry": expiry, "expiries": [expiry], "spot": spot, "source": "test", "rows": rows}


# ------------------------------------------------------------------ pricing -- #
def test_put_call_parity_and_delta():
    s, k, t, v, r = 100, 105, 30 / 252, 0.25, os_.RISK_FREE
    c, p = os_.bs_price(s, k, t, v, "CE"), os_.bs_price(s, k, t, v, "PE")
    assert c - p == pytest.approx(s - k * math.exp(-r * t), abs=1e-9)
    assert 0 < os_.bs_delta(s, k, t, v, "CE") < 0.5 < os_.bs_delta(s, 95, t, v, "CE") < 1
    assert -1 < os_.bs_delta(s, k, t, v, "PE") < -0.5
    assert os_.bs_price(s, 90, 0, v, "CE") == 10  # expired: intrinsic


def test_implied_vol_roundtrip():
    price = os_.bs_price(100, 102, 0.05, 0.31, "CE")
    assert os_.implied_vol(price, 100, 102, 0.05, "CE") == pytest.approx(0.31, abs=1e-4)
    assert os_.implied_vol(0, 100, 102, 0.05, "CE") is None


def test_pick_expiry_skips_expiries_too_close():
    ex = ["2026-11-10", "2026-11-12", "2026-11-19", "2026-11-26"]
    assert os_.pick_expiry(ex, TODAY, min_trading_days=4) == "2026-11-19"
    assert os_.pick_expiry(ex, TODAY, min_trading_days=1) == "2026-11-10"
    assert os_.pick_expiry(["2026-11-10"], TODAY, min_trading_days=4) == "2026-11-10", "falls back to the last one"
    assert os_.pick_expiry(["2026-10-01", "demo"], TODAY) is None


def test_realized_vol():
    rng = np.random.default_rng(1)
    closes = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 60))))
    assert os_.realized_vol(closes) == pytest.approx(0.01 * math.sqrt(252), rel=0.35)
    assert os_.realized_vol(closes.head(5)) is None


# --------------------------------------------------------------- suggestion -- #
def test_buy_signal_suggests_a_call_near_060_delta():
    s = os_.suggest(chain(), "BUY", target=106, stop=97, today=TODAY, lot_size=75)
    main = s["ideas"][0]
    assert s["type"] == main["type"] == "CE" and main["role"] == "recommended"
    assert 0.5 <= main["delta"] <= 0.7 and main["strike"] <= 100
    assert main["target_premium"] > main["premium"] > main["stop_premium"] > 0
    assert main["cost_per_lot"] == pytest.approx(main["premium"] * 75)
    assert main["max_loss_per_lot"] == pytest.approx((main["premium"] - main["stop_premium"]) * 75, abs=1)
    roles = [i["role"] for i in s["ideas"]]
    assert any(r.startswith("aggressive") for r in roles) and any(r.startswith("conservative") for r in roles)
    aggressive = next(i for i in s["ideas"] if i["role"].startswith("aggressive"))
    assert aggressive["strike"] > main["strike"] and aggressive["premium"] < main["premium"]
    assert s["trading_days_to_expiry"] == 8


def test_sell_signal_suggests_a_put():
    s = os_.suggest(chain(), "SELL", target=94, stop=103, today=TODAY)
    main = s["ideas"][0]
    assert main["type"] == "PE" and main["strike"] >= 100
    assert main["target_premium"] > main["premium"] > main["stop_premium"]


def test_warnings():
    calm = pd.Series(100 * np.exp(np.cumsum(np.full(40, 0.0005) + np.tile([0.002, -0.002], 20))))
    walls = {(105.0, "CE"): 50_000}
    s = os_.suggest(chain(iv=40, oi=walls, spread=0.08), "BUY", target=108, stop=97, today=TODAY, closes=calm)
    text = " ".join(s["warnings"])
    assert "expensive" in text and "OI wall at 105" in text and "Thin liquidity" in text
    clean = os_.suggest(chain(), "BUY", target=106, stop=97, today=TODAY)
    assert not any("expensive" in w or "wall" in w for w in clean["warnings"])


def test_bad_input():
    with pytest.raises(ValueError):
        os_.suggest(chain(), "HOLD", 1, 2, today=TODAY)
    empty = chain()
    for r in empty["rows"]:
        r["CE"] = None
    with pytest.raises(ValueError, match="No priced CE"):
        os_.suggest(empty, "BUY", 106, 97, today=TODAY)


# ------------------------------------------------------------------- API ---- #
def test_suggest_endpoint():
    c = TestClient(app)
    r = c.get("/api/options/NIFTY/suggest", params={"side": "BUY", "target": 1040, "stop": 985,
                                                     "source": "synthetic", "spot": 1000})
    assert r.status_code == 200 and r.json()["ideas"][0]["type"] == "CE"
    bad = c.get("/api/options/NIFTY/suggest", params={"side": "BUY", "target": 900, "stop": 985, "source": "synthetic"})
    assert bad.status_code == 422


# --------------------------------------------------------- alerts/telegram -- #
def test_alerts_carry_option_ideas(tmp_path):
    from tests.test_alerts import WED, at, dated
    from app.wave_oi import make_synthetic
    full = make_synthetic(600, seed=7)
    an, _ = __import__("app.wave_oi", fromlist=["analyze"]).analyze(full)
    k = int((an["signal"] != 0).to_numpy().argmax())
    df = dated(full.iloc[: k + 1], WED)
    calls = []

    def suggester(sym, side, target, stop):
        calls.append((sym, side))
        return {"ideas": [{"type": "CE" if side == "BUY" else "PE", "strike": 100.0, "expiry": EXPIRY,
                           "premium": 3.1, "target_premium": 6.0, "stop_premium": 1.5, "lot_size": 75}],
                "warnings": ["w1", "w2", "w3", "w4"]}

    store = AlertStore(tmp_path / "a.json")
    store.set_watchlist(["ABC"])
    alert = AlertScanner(store, lambda s: (df, None), now=lambda: at(WED, 20), option_suggester=suggester).scan_all()[0]
    assert alert["kind"] == "confirmed" and alert["option"]["strike"] == 100.0
    assert alert["option"]["warnings"] == ["w1", "w2", "w3"] and calls == [("ABC", alert["side"])]

    store2 = AlertStore(tmp_path / "b.json")
    store2.set_watchlist(["ABC"])
    boom = AlertScanner(store2, lambda s: (df, None), now=lambda: at(WED, 20),
                        option_suggester=lambda *a: 1 / 0).scan_all()[0]
    assert "option" not in boom, "a failed option lookup never blocks the alert"


def test_telegram_includes_option_line():
    msg = format_message({"kind": "confirmed", "side": "BUY", "symbol": "NIFTY", "price": 25030.35,
                          "stop": 24890, "target": 25480, "reason": "W3 up", "bar_date": "2026-11-07",
                          "option": {"type": "CE", "strike": 25000.0, "expiry": "2026-11-13", "premium": 222.3,
                                     "target_premium": 468.1, "stop_premium": 140.2, "lot_size": 75,
                                     "cost_per_lot": 16672.5, "max_loss_per_lot": 6157.5, "warnings": ["Wall <x>"]}})
    assert "Option: buy 25000 CE" in msg and "Lot 75" in msg and "Wall &lt;x&gt;" in msg


# ------------------------------------------------- no trade when levels are stale -- #
@pytest.mark.parametrize("side,spot,target,stop,msg", [
    ("BUY", 752.90, 750.81, 634.15, "already reached the target"),   # the DLF case
    ("SELL", 95.0, 96.0, 103.0, "already reached the target"),
    ("BUY", 100.0, 106.0, 101.0, "beyond the stop"),
    ("SELL", 100.0, 94.0, 99.0, "beyond the stop"),
    ("BUY", 100.0, 100.3, 97.0, "too little room"),
])
def test_no_idea_when_levels_are_stale(side, spot, target, stop, msg):
    with pytest.raises(os_.NoTrade, match=msg):
        os_.suggest(chain(spot=spot), side, target=target, stop=stop, today=TODAY)


def test_endpoint_explains_no_trade():
    r = TestClient(app).get("/api/options/DLF/suggest", params={
        "side": "BUY", "target": 750.81, "stop": 634.15, "source": "synthetic", "spot": 752.90})
    assert r.status_code == 422 and "already reached the target 750.81" in r.json()["detail"]


def test_alert_skips_option_when_target_already_reached(tmp_path):
    from app.alerts.engine import AlertScanner, AlertStore
    sc = AlertScanner(AlertStore(tmp_path / "x.json"), lambda s: (None, None),
                      option_suggester=lambda sym, side, t, st: os_.suggest(
                          chain(spot=752.9), side, target=t, stop=st, today=TODAY))
    assert sc._option_idea("DLF", "BUY", 750.81, 634.15) is None
