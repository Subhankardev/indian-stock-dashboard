import gzip
import json
from datetime import datetime, timedelta
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from app import upstox_data
from app.main import app
from app.option_analytics import analyze_chain, max_pain, normalise_nse_chain, synthetic_chain
from app.upstox_data import IST, UpstoxClient, UpstoxError, parse_option_chain


def side(ltp, oi, prev_oi=None, close=None, iv=None):
    return {"ltp": ltp, "close": close or ltp, "oi": oi, "prev_oi": prev_oi if prev_oi is not None else oi,
            "volume": oi, "iv": iv, "delta": None, "bid": None, "ask": None, "key": None}


def chain(rows, spot=100.0):
    return {"symbol": "X", "expiry": "2026-11-26", "expiries": ["2026-11-26"], "spot": spot,
            "source": "test", "rows": rows}


# ------------------------------------------------------------- analytics -- #
def test_max_pain_hand_calculated():
    rows = [
        {"strike": 90.0, "CE": side(11, 100), "PE": side(1, 500)},
        {"strike": 100.0, "CE": side(4, 300), "PE": side(4, 300)},
        {"strike": 110.0, "CE": side(1, 500), "PE": side(11, 100)},
    ]
    # payout(90)=0+300*10+100*20=5000, payout(100)=100*10+100*10=2000, payout(110)=100*20+300*10=5000
    assert max_pain(rows) == 100.0


def test_analyze_chain_levels_and_buildups():
    rows = [
        {"strike": 90.0, "CE": side(12, 50), "PE": side(1.0, 900, prev_oi=600, close=1.5, iv=24)},  # PE short build-up
        {"strike": 100.0, "CE": side(4.0, 300, iv=18), "PE": side(3.5, 400, iv=20)},
        {"strike": 105.0, "CE": side(2.0, 700, prev_oi=500, close=1.6), "PE": side(7, 100)},     # CE long build-up
        {"strike": 110.0, "CE": side(1.0, 1200), "PE": side(11, 50)},
    ]
    a = analyze_chain(chain(rows, spot=101))
    assert a["PCR"] == pytest.approx(1450 / 2250)
    assert a["max_CE_OI_strike"] == 110 and a["max_PE_OI_strike"] == 90
    assert a["resistance"] == 110 and a["support"] == 90
    assert a["atm_strike"] == 100 and a["atm_iv"] == pytest.approx(19)
    assert a["atm_straddle"] == pytest.approx(7.5)
    assert a["expected_move_pct"] == pytest.approx(7.5 / 101 * 100)
    by_strike = {r["strike"]: r for r in a["rows"]}
    assert by_strike[90]["PE"]["buildup"] == "Short build-up" and by_strike[90]["PE"]["oi_change"] == 300
    assert by_strike[105]["CE"]["buildup"] == "Long build-up"
    assert a["top_oi_change"]["PE"][0] == {"strike": 90.0, "oi_change": 300}


def test_window_limits_rows_around_atm():
    a = analyze_chain(synthetic_chain("DEMO", 1000, n=60), window=5)
    assert len(a["rows"]) == 11 and a["rows"][5]["strike"] == a["atm_strike"]


def test_synthetic_chain_has_walls_on_the_right_side():
    a = analyze_chain(synthetic_chain("DEMO", 1000))
    assert a["resistance"] > 1000 > a["support"]


def test_nse_payload_normalised():
    raw = {"records": {"expiryDates": ["28-Nov-2026", "26-Dec-2026"], "underlyingValue": 101, "data": [
        {"strikePrice": 100, "expiryDate": "28-Nov-2026",
         "CE": {"openInterest": 300, "changeinOpenInterest": 50, "lastPrice": 4, "change": 1, "impliedVolatility": 18},
         "PE": {"openInterest": 400, "changeinOpenInterest": -20, "lastPrice": 3, "change": -0.5}},
        {"strikePrice": 100, "expiryDate": "26-Dec-2026", "CE": {"openInterest": 9999}},
    ]}}
    c = normalise_nse_chain(raw, "x")
    assert c["expiry"] == "28-Nov-2026" and len(c["rows"]) == 1
    ce = c["rows"][0]["CE"]
    assert ce["prev_oi"] == 250 and ce["close"] == 3
    assert normalise_nse_chain(raw, "x", "26-Dec-2026")["rows"][0]["CE"]["oi"] == 9999


# ----------------------------------------------------------------- Upstox -- #
UPSTOX_ROW = {
    "expiry": "2026-11-26", "pcr": 1.2, "strike_price": 25000.0,
    "underlying_key": "NSE_INDEX|Nifty 50", "underlying_spot_price": 25012.4,
    "call_options": {"instrument_key": "NSE_FO|1", "market_data": {
        "ltp": 120.5, "volume": 1000, "oi": 5000.0, "close_price": 110.0, "bid_price": 120.0, "bid_qty": 75,
        "ask_price": 121.0, "ask_qty": 50, "prev_oi": 4000.0},
        "option_greeks": {"vega": 1, "theta": -2, "gamma": 0.001, "delta": 0.52, "iv": 13.1, "pop": 48}},
    "put_options": {"instrument_key": "NSE_FO|2", "market_data": {
        "ltp": 98.0, "volume": 800, "oi": 6000.0, "close_price": 105.0, "bid_price": 97.5,
        "ask_price": 98.5, "prev_oi": 6500.0},
        "option_greeks": {"delta": -0.48, "iv": 13.9}},
}


def test_parse_upstox_option_chain():
    c = parse_option_chain([UPSTOX_ROW], "nifty", "2026-11-26", ["2026-11-26"])
    assert c["spot"] == 25012.4 and c["symbol"] == "NIFTY"
    r = c["rows"][0]
    assert r["CE"] == {"ltp": 120.5, "close": 110.0, "oi": 5000.0, "prev_oi": 4000.0, "volume": 1000,
                       "bid": 120.0, "ask": 121.0, "iv": 13.1, "delta": 0.52, "key": "NSE_FO|1"}
    a = analyze_chain(c)
    assert a["rows"][0]["CE"]["buildup"] == "Long build-up"
    assert a["rows"][0]["PE"]["buildup"] == "Long unwinding"


def _ms(days):
    d = datetime.now(IST).replace(hour=15, minute=30, second=0, microsecond=0) + timedelta(days=days)
    return int(d.timestamp() * 1000), d.date().isoformat()


class OptionSession:
    def __init__(self):
        self.calls = []
        past, _ = _ms(-7)
        self.e1_ms, self.e1 = _ms(3)
        self.e2_ms, self.e2 = _ms(10)
        self.instruments = [
            {"segment": "NSE_FO", "instrument_type": "CE", "underlying_symbol": "NIFTY",
             "underlying_key": "NSE_INDEX|Nifty 50", "expiry": self.e2_ms},
            {"segment": "NSE_FO", "instrument_type": "PE", "underlying_symbol": "NIFTY",
             "underlying_key": "NSE_INDEX|Nifty 50", "expiry": self.e1_ms},
            {"segment": "NSE_FO", "instrument_type": "CE", "underlying_symbol": "NIFTY",
             "underlying_key": "NSE_INDEX|Nifty 50", "expiry": past},
        ]

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append((unquote(url), headers or {}, params or {}))

        class R:
            status_code, ok = 200, True

            def __init__(self, body=None, content=None):
                self._b, self.content = body, content
                self.text = ""

            def json(self):
                return self._b

            def raise_for_status(self):
                pass

        if url == upstox_data.INSTRUMENTS_URL:
            return R(content=gzip.compress(json.dumps(self.instruments).encode()))
        if url.endswith("/v2/option/chain"):
            return R(body={"status": "success", "data": [UPSTOX_ROW]})
        raise AssertionError(url)


def test_option_meta_maps_index_and_drops_past_expiries():
    s = OptionSession()
    key, expiries = UpstoxClient(access_token="t", session=s).option_meta("nifty")
    assert key == "NSE_INDEX|Nifty 50" and expiries == [s.e1, s.e2]


def test_option_chain_requests_nearest_expiry_with_token():
    s = OptionSession()
    c = UpstoxClient(access_token="t", session=s).option_chain("NIFTY")
    url, headers, params = s.calls[-1]
    assert url.endswith("/v2/option/chain") and headers["Authorization"] == "Bearer t"
    assert params == {"instrument_key": "NSE_INDEX|Nifty 50", "expiry_date": s.e1}
    assert c["expiry"] == s.e1 and c["expiries"] == [s.e1, s.e2] and c["source"] == "upstox"


def test_option_chain_needs_token():
    with pytest.raises(UpstoxError, match="UPSTOX_ACCESS_TOKEN"):
        UpstoxClient(access_token="", session=OptionSession()).option_chain("NIFTY")


# -------------------------------------------------------------------- API -- #
def test_api_options_synthetic():
    r = TestClient(app).get("/api/options/DEMO", params={"source": "synthetic", "spot": 2500})
    assert r.status_code == 200
    body = r.json()
    assert body["spot"] == 2500 and body["max_pain"] and len(body["rows"]) == 25
    assert {"PCR", "max_CE_OI_strike", "max_PE_OI_strike"} <= set(body)


def test_api_options_upstox_without_token(monkeypatch):
    monkeypatch.setattr(upstox_data.client(), "token", None)
    r = TestClient(app).get("/api/options/NIFTY", params={"source": "upstox"})
    assert r.status_code == 502 and "UPSTOX_ACCESS_TOKEN" in r.json()["detail"]


def test_api_options_rejects_bad_expiry():
    assert TestClient(app).get("/api/options/NIFTY", params={"expiry": "26-11-2026"}).status_code == 422


def test_api_stock_fetch_failure_is_502(monkeypatch):
    import app.main as main

    def boom(symbol):
        raise RuntimeError("network down")
    monkeypatch.setattr(main, "load_daily_prices", boom)
    r = TestClient(app).get("/api/stock/RELIANCE", headers={"Origin": "http://localhost:3000"})
    assert r.status_code == 502 and "network down" in r.json()["detail"]
    assert r.headers.get("access-control-allow-origin") == "*"
