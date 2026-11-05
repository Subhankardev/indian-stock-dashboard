import gzip
import json
from datetime import date, timedelta
from urllib.parse import unquote

import pandas as pd
import pytest

from app import upstox_data
from app.upstox_data import (
    UpstoxClient, UpstoxError, find_equity, find_futures, parse_candles, sum_open_interest,
)

EQ_KEY = "NSE_EQ|INE002A01018"
INSTRUMENTS = [
    {"segment": "NSE_EQ", "instrument_type": "EQ", "trading_symbol": "RELIANCE", "instrument_key": EQ_KEY},
    {"segment": "NSE_FO", "instrument_type": "FUT", "underlying_symbol": "RELIANCE",
     "instrument_key": "NSE_FO|222", "expiry": 2_000_000_000_000},
    {"segment": "NSE_FO", "instrument_type": "FUT", "underlying_symbol": "RELIANCE",
     "instrument_key": "NSE_FO|111", "expiry": 1_900_000_000_000},
    {"segment": "NSE_FO", "instrument_type": "CE", "underlying_symbol": "RELIANCE", "instrument_key": "NSE_FO|999"},
    {"segment": "NSE_EQ", "instrument_type": "EQ", "trading_symbol": "IRCTCX", "instrument_key": "NSE_EQ|X"},
]


def _row(d: date, close: float, oi: float = 0):
    return [f"{d:%Y-%m-%d}T00:00:00+05:30", close - 1, close + 2, close - 2, close, 1000, oi]


def _days(n: int):
    end = date.today() - timedelta(days=1)
    return [end - timedelta(days=i) for i in range(n)][::-1]


class FakeResp:
    def __init__(self, status=200, body=None, content=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._body = body
        self.content = content if content is not None else json.dumps(body or {}).encode()
        self.text = self.content.decode(errors="ignore")

    def json(self):
        return self._body

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(self.status_code)


class FakeSession:
    """Routes Upstox URLs to canned responses and records every request."""

    def __init__(self, days=150, expired=True):
        self.calls = []
        self.days = _days(days)
        self.expired = expired

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append((unquote(url), headers or {}, params or {}))
        u = unquote(url)
        if u == upstox_data.INSTRUMENTS_URL:
            return FakeResp(content=gzip.compress(json.dumps(INSTRUMENTS).encode()))
        if "/intraday/" in u:
            return FakeResp(body={"status": "success", "data": {"candles": []}})
        if f"/v3/historical-candle/{EQ_KEY}/days/1/" in u:
            return FakeResp(body={"status": "success", "data": {"candles": [
                _row(d, 100 + i) for i, d in enumerate(self.days)][::-1]}})  # API returns newest first
        if "/v3/historical-candle/NSE_FO|111/" in u:   # near month: last 60 days
            return FakeResp(body={"status": "success", "data": {"candles": [
                _row(d, 100, 1000 + i) for i, d in enumerate(self.days[-60:])]}})
        if "/v3/historical-candle/NSE_FO|222/" in u:   # next month: last 30 days
            return FakeResp(body={"status": "success", "data": {"candles": [
                _row(d, 100, 10) for d in self.days[-30:]]}})
        if u.endswith("/v2/expired-instruments/expiries"):
            if not self.expired:
                return FakeResp(status=403, body={"status": "error", "errors": ["not entitled"]})
            exp = self.days[-61]
            return FakeResp(body={"status": "success", "data": [f"{exp:%Y-%m-%d}"]})
        if u.endswith("/v2/expired-instruments/future/contract"):
            exp = params["expiry_date"]
            return FakeResp(body={"status": "success", "data": [{"instrument_key": f"NSE_FO|50|{exp}"}]})
        if "/v2/expired-instruments/historical-candle/NSE_FO|50|" in u:
            return FakeResp(body={"status": "success", "data": {"candles": [
                _row(d, 100, 500) for d in self.days[-90:-60]]}})
        return FakeResp(status=404, body={"status": "error", "errors": [u]})


# ---------------------------------------------------------------- parsing --- #
def test_parse_candles_field_order_and_dates():
    df = parse_candles([
        ["2025-01-03T00:00:00+05:30", 10, 12, 9, 11, 500, 7],
        ["2025-01-02T00:00:00+05:30", 9, 10, 8, 10, 400, 6],
    ])
    assert list(df.index.strftime("%Y-%m-%d")) == ["2025-01-02", "2025-01-03"]
    assert df.loc["2025-01-03"].tolist() == [10, 12, 9, 11, 500, 7]


def test_parse_candles_empty():
    assert parse_candles([]).empty


def test_instrument_lookup():
    assert find_equity(INSTRUMENTS, "reliance")["instrument_key"] == EQ_KEY
    assert [f["instrument_key"] for f in find_futures(INSTRUMENTS, "RELIANCE")] == ["NSE_FO|111", "NSE_FO|222"]
    with pytest.raises(UpstoxError):
        find_equity(INSTRUMENTS, "NOPE")


def test_sum_open_interest_across_contracts():
    a = parse_candles([_row(date(2025, 1, 1), 1, 100), _row(date(2025, 1, 2), 1, 90)])
    b = parse_candles([_row(date(2025, 1, 2), 1, 20)])
    assert sum_open_interest([a, b]).tolist() == [100, 110]


# ------------------------------------------------------------- client ------ #
def test_history_without_token_uses_live_contracts_only():
    s = FakeSession()
    df, warning = UpstoxClient(access_token="", session=s).price_oi_history("RELIANCE", days=200)
    assert len(df) == 150 and df["close"].is_monotonic_increasing
    oi = df["open_interest"]
    assert oi.first_valid_index() == pd.Timestamp(s.days[-60])
    assert oi.iloc[-1] == 1059 + 10
    assert "No UPSTOX_ACCESS_TOKEN" in warning and "OI history starts" in warning
    assert not any("expired" in c[0] for c in s.calls)
    assert all("Authorization" not in c[1] for c in s.calls)


def test_history_with_token_adds_expired_contracts():
    s = FakeSession()
    df, warning = UpstoxClient(access_token="tok", session=s).price_oi_history("RELIANCE", days=200)
    assert df["open_interest"].first_valid_index() == pd.Timestamp(s.days[-90])
    api_calls = [c for c in s.calls if c[0].startswith(upstox_data.API_BASE)]
    assert all(c[1].get("Authorization") == "Bearer tok" for c in api_calls)
    assert "No UPSTOX_ACCESS_TOKEN" not in (warning or "")


def test_expired_api_refusal_is_a_warning_not_an_error():
    s = FakeSession(expired=False)
    df, warning = UpstoxClient(access_token="tok", session=s).price_oi_history("RELIANCE", days=200)
    assert "Expired futures unavailable" in warning
    assert df["open_interest"].notna().sum() == 60


def test_stock_without_futures():
    s = FakeSession()
    s_get = s.get

    def get(url, **kw):
        if "NSE_EQ|X" in unquote(url):
            url = url.replace("NSE_EQ%7CX", "NSE_EQ%7CINE002A01018")
        return s_get(url, **kw)

    s.get = get
    df, warning = UpstoxClient(access_token="", session=s).price_oi_history("IRCTCX", days=200)
    assert "open_interest" not in df.columns and "no NSE futures" in warning


def test_expired_token_message():
    class Unauthorized(FakeSession):
        def get(self, url, **kw):
            if unquote(url).startswith(upstox_data.API_BASE):
                return FakeResp(status=401, body={"status": "error"})
            return super().get(url, **kw)

    with pytest.raises(UpstoxError, match="access token"):
        UpstoxClient(access_token="old", session=Unauthorized()).price_oi_history("RELIANCE", days=200)


def test_live_legs_picks_near_future_and_previous_day_oi():
    s = FakeSession()
    legs = UpstoxClient(access_token="tok", session=s).live_legs("reliance")
    assert legs["EQ"] == EQ_KEY and legs["FUT"] == "NSE_FO|111"
    assert legs["prev_oi"] == 1059  # last completed day's OI of the near-month contract


def test_index_symbols_resolve_through_their_fo_underlying():
    instruments = INSTRUMENTS + [
        {"segment": "NSE_FO", "instrument_type": "FUT", "underlying_symbol": "NIFTY",
         "underlying_key": "NSE_INDEX|Nifty 50", "instrument_key": "NSE_FO|777", "expiry": 1_900_000_000_000},
    ]
    nifty = find_equity(instruments, "nifty")
    assert nifty["instrument_key"] == "NSE_INDEX|Nifty 50" and nifty["instrument_type"] == "INDEX"
    assert [f["instrument_key"] for f in find_futures(instruments, "NIFTY")] == ["NSE_FO|777"]


def test_unknown_symbol_suggests_close_matches():
    with pytest.raises(UpstoxError, match="Did you mean RELIANCE"):
        find_equity(INSTRUMENTS, "RELIENCE")
    with pytest.raises(UpstoxError) as exc:
        find_equity(INSTRUMENTS, "ZZZZZZ")
    assert "Did you mean" not in str(exc.value)
