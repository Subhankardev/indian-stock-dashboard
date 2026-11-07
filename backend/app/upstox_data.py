"""Upstox market data: daily candles for equities and futures open interest.

Endpoints used (https://upstox.com/developer/api-documentation/api-overview):

* Instrument master (public, refreshed daily):
  ``https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz``
* Historical candles V3 (public):
  ``GET /v3/historical-candle/{instrument_key}/{unit}/{interval}/{to_date}/{from_date}``
* Intraday candles V3 (today's bar, public):
  ``GET /v3/historical-candle/intraday/{instrument_key}/{unit}/{interval}``
* Expired instruments (needs an access token):
  ``GET /v2/expired-instruments/expiries?instrument_key=``
  ``GET /v2/expired-instruments/future/contract?instrument_key=&expiry_date=``
  ``GET /v2/expired-instruments/historical-candle/{expired_key}/{interval}/{to_date}/{from_date}``

Every candle row is ``[timestamp, open, high, low, close, volume, open_interest]``.

Configuration (environment variables):

* ``UPSTOX_ACCESS_TOKEN`` - OAuth access token (optional). Without it only the
  live futures contracts are used, so OI history covers roughly the last
  three months. With it, expired contracts extend OI back further.
* ``UPSTOX_MAX_EXPIRED`` - how many expired monthly contracts to pull (default 24).
"""
from __future__ import annotations

import difflib
import gzip
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional
from urllib.parse import quote

import pandas as pd
import requests

from app.config import clean_token, token_problem

API_BASE = "https://api.upstox.com"
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
_INSTRUMENT_TTL = 12 * 3600
_CANDLE_TTL = 15 * 60
_TODAY_TTL = 60  # seconds; the forming daily bar
_CHUNK_DAYS = 365  # stay well inside the per-request range limit for daily candles


class UpstoxError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Pure parsing helpers (unit-tested without network)
# --------------------------------------------------------------------------- #
def parse_candles(rows: Iterable[list]) -> pd.DataFrame:
    """Upstox candle rows -> daily OHLCV(+OI) frame indexed by date, oldest first."""
    rows = list(rows)
    cols = ["timestamp", "open", "high", "low", "close", "volume", "open_interest"]
    if not rows:
        return pd.DataFrame(columns=cols[1:], index=pd.DatetimeIndex([], name="date"))
    df = pd.DataFrame([list(r) + [None] * (7 - len(r)) for r in rows], columns=cols)
    ts = pd.to_datetime(df.pop("timestamp"), utc=True).dt.tz_convert("Asia/Kolkata")
    df.index = pd.DatetimeIndex(ts.dt.tz_localize(None).dt.normalize(), name="date")
    df = df.apply(pd.to_numeric, errors="coerce")
    return df[~df.index.duplicated(keep="last")].sort_index()


def find_equity(instruments: list[dict], symbol: str) -> dict:
    """The tradable underlying for a symbol: an NSE stock, or an index such as NIFTY /
    BANKNIFTY (resolved through its F&O contracts' ``underlying_key``)."""
    sym = symbol.upper()
    for ins in instruments:
        if ins.get("segment") == "NSE_EQ" and ins.get("instrument_type") == "EQ" \
                and str(ins.get("trading_symbol", "")).upper() == sym:
            return ins
    for ins in instruments:
        if ins.get("segment") == "NSE_FO" and str(ins.get("underlying_symbol", "")).upper() == sym \
                and str(ins.get("underlying_key", "")).startswith("NSE_INDEX|"):
            return {"instrument_key": ins["underlying_key"], "trading_symbol": sym, "instrument_type": "INDEX"}
    names = {str(i.get("trading_symbol", "")).upper() for i in instruments if i.get("segment") == "NSE_EQ"}
    names |= {str(i.get("underlying_symbol", "")).upper() for i in instruments if i.get("segment") == "NSE_FO"}
    close = difflib.get_close_matches(sym, sorted(n for n in names if n), n=3, cutoff=0.75)
    hint = f" Did you mean {' or '.join(close)}?" if close else ""
    raise UpstoxError(f"{sym} is not an NSE stock or index on Upstox.{hint}")


def find_futures(instruments: list[dict], symbol: str) -> list[dict]:
    """Live futures contracts on the underlying, nearest expiry first."""
    sym = symbol.upper()
    futs = [
        ins for ins in instruments
        if ins.get("segment") == "NSE_FO" and ins.get("instrument_type") == "FUT"
        and str(ins.get("underlying_symbol", "")).upper() == sym
    ]
    return sorted(futs, key=lambda i: i.get("expiry") or 0)


def sum_open_interest(frames: Iterable[pd.DataFrame]) -> pd.Series:
    """Total OI per day across contracts (smooths the month-end rollover)."""
    parts = [f["open_interest"].rename(i) for i, f in enumerate(frames) if len(f)]
    if not parts:
        return pd.Series(dtype=float, name="open_interest")
    oi = pd.concat(parts, axis=1, sort=True)
    total = oi.sum(axis=1, min_count=1)
    return total[total > 0].rename("open_interest")


def _date_chunks(start: date, end: date, chunk: int = _CHUNK_DAYS):
    cur = start
    while cur <= end:
        nxt = min(cur + timedelta(days=chunk - 1), end)
        yield cur, nxt
        cur = nxt + timedelta(days=1)


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class UpstoxClient:
    def __init__(self, access_token: Optional[str] = None, session: Optional[requests.Session] = None):
        raw = access_token if access_token is not None else os.getenv("UPSTOX_ACCESS_TOKEN")
        self.token = clean_token(raw)
        self.session = session or requests.Session()
        self._cache: dict = {}
        self._last_call = 0.0

    # -- plumbing -------------------------------------------------------- #
    def _headers(self) -> dict:
        h = {"Accept": "application/json"}
        problem = token_problem(self.token)
        if problem:
            raise UpstoxError(f"UPSTOX_ACCESS_TOKEN {problem}")
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _pace(self) -> None:
        """Keep real API calls under Upstox's rate limits (about 8 per second by default)."""
        if not isinstance(self.session, requests.Session):
            return  # test doubles aren't throttled
        gap = float(os.getenv("UPSTOX_MIN_INTERVAL", "0.12"))
        wait = self._last_call + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        self._pace()
        resp = self.session.get(f"{API_BASE}{path}", headers=self._headers(), params=params, timeout=20)
        if resp.status_code == 401:
            raise UpstoxError("Upstox rejected the access token (expired? tokens last one trading day)")
        for backoff in (1.0, 2.0, 4.0):  # rate limited: back off and retry
            if resp.status_code != 429:
                break
            time.sleep(backoff)
            self._pace()
            resp = self.session.get(f"{API_BASE}{path}", headers=self._headers(), params=params, timeout=20)
        if not resp.ok:
            raise UpstoxError(f"Upstox {path} -> HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        if body.get("status") not in (None, "success"):
            raise UpstoxError(f"Upstox {path} -> {body.get('errors') or body}")
        return body

    def _cached(self, key, ttl, loader):
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        value = loader()
        self._cache[key] = (time.time(), value)
        return value

    # -- instruments ----------------------------------------------------- #
    def instruments(self) -> list[dict]:
        def load():
            resp = self.session.get(INSTRUMENTS_URL, timeout=60)
            resp.raise_for_status()
            raw = resp.content
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            return json.loads(raw)
        return self._cached("instruments", _INSTRUMENT_TTL, load)

    # -- candles --------------------------------------------------------- #
    def daily_candles(self, instrument_key: str, start: date, end: date, include_today: bool = True) -> pd.DataFrame:
        key = quote(instrument_key, safe="")

        def load_history():
            frames = []
            for a, b in _date_chunks(start, end):
                body = self._get(f"/v3/historical-candle/{key}/days/1/{b:%Y-%m-%d}/{a:%Y-%m-%d}")
                frames.append(parse_candles(body.get("data", {}).get("candles", [])))
            return pd.concat(frames) if frames else parse_candles([])

        def load_today():
            try:
                body = self._get(f"/v3/historical-candle/intraday/{key}/days/1")
                return parse_candles(body.get("data", {}).get("candles", []))
            except UpstoxError:
                return parse_candles([])  # market closed / holiday: history is enough

        # Completed days change rarely; today's forming bar is refreshed every minute.
        frames = [self._cached(("candles", instrument_key, start, end), _CANDLE_TTL, load_history)]
        if include_today and end >= date.today():
            frames.append(self._cached(("today", instrument_key), _TODAY_TTL, load_today))
        df = pd.concat([f for f in frames if len(f)]) if any(len(f) for f in frames) else parse_candles([])
        return df[~df.index.duplicated(keep="last")].sort_index().copy()

    def expired_futures_candles(self, underlying_key: str, start: date, max_contracts: int) -> list[pd.DataFrame]:
        if not self.token:
            return []
        expiries = self._get("/v2/expired-instruments/expiries", {"instrument_key": underlying_key}).get("data") or []
        expiries = sorted((e for e in expiries if e >= f"{start:%Y-%m-%d}"), reverse=True)[:max_contracts]
        frames = []
        for exp in expiries:
            contracts = self._get(
                "/v2/expired-instruments/future/contract",
                {"instrument_key": underlying_key, "expiry_date": exp},
            ).get("data") or []
            for c in contracts:
                ek = quote(c["instrument_key"], safe="")
                exp_day = date.fromisoformat(exp)
                frm = max(start, exp_day - timedelta(days=100))  # monthly contracts live ~3 months
                body = self._get(f"/v2/expired-instruments/historical-candle/{ek}/day/{exp_day:%Y-%m-%d}/{frm:%Y-%m-%d}")
                frames.append(parse_candles(body.get("data", {}).get("candles", [])))
        return frames

    # -- high level ------------------------------------------------------ #
    def price_oi_history(self, symbol: str, days: int = 730) -> tuple[pd.DataFrame, Optional[str]]:
        end = date.today()
        start = end - timedelta(days=days)
        instruments = self.instruments()
        eq = find_equity(instruments, symbol)
        price = self.daily_candles(eq["instrument_key"], start, end)
        if price.empty:
            raise UpstoxError(f"No daily candles returned for {symbol}")
        price = price.drop(columns="open_interest")

        warnings = []
        futs = find_futures(instruments, symbol)
        if not futs:
            return price, f"{symbol.upper()} has no NSE futures, so the analysis runs without open interest."
        fut_frames = [self.daily_candles(f["instrument_key"], start, end) for f in futs]
        try:
            max_exp = int(os.getenv("UPSTOX_MAX_EXPIRED", "24"))
            fut_frames += self.expired_futures_candles(eq["instrument_key"], start, max_exp)
        except UpstoxError as exc:
            warnings.append(f"Expired futures unavailable ({exc}).")
        if not self.token:
            warnings.append("No UPSTOX_ACCESS_TOKEN set: OI covers only the live futures contracts (about 3 months).")

        oi = sum_open_interest(fut_frames)
        df = price.join(oi, how="left")
        if df["open_interest"].notna().any():
            first = df["open_interest"].first_valid_index()
            df.loc[first:, "open_interest"] = df.loc[first:, "open_interest"].ffill()
            if first > df.index[0]:
                warnings.append(f"OI history starts {first:%Y-%m-%d}; before that, signals are not OI-filtered.")
        else:
            df = df.drop(columns="open_interest")
            warnings.append("Upstox returned no futures OI.")
        return df, " ".join(warnings) or None


    def live_legs(self, symbol: str) -> dict:
        """Instrument keys to stream for a symbol: the equity and its near-month future,
        plus the future's previous-day OI (the baseline for today's OI change)."""
        instruments = self.instruments()
        eq = find_equity(instruments, symbol)
        legs = {"symbol": symbol.upper(), "EQ": eq["instrument_key"], "FUT": None,
                "fut_symbol": None, "prev_oi": None}
        futs = [f for f in find_futures(instruments, symbol)
                if (f.get("expiry") or 0) / 1000 >= time.time() - 86_400]
        if futs:
            near = futs[0]
            legs.update(FUT=near["instrument_key"], fut_symbol=near.get("trading_symbol"))
            today = date.today()
            hist = self.daily_candles(near["instrument_key"], today - timedelta(days=10), today, include_today=False)
            hist = hist[hist.index < pd.Timestamp(today)]
            if len(hist) and pd.notna(hist["open_interest"].iloc[-1]):
                legs["prev_oi"] = float(hist["open_interest"].iloc[-1])
        return legs


    # -- options ----------------------------------------------------------- #
    def option_meta(self, symbol: str) -> tuple[str, list[str]]:
        """(underlying instrument key, upcoming expiries as YYYY-MM-DD) from the instrument file.
        Works for stocks (RELIANCE) and indices (NIFTY, BANKNIFTY, ...)."""
        sym = symbol.upper()
        opts = [i for i in self.instruments()
                if i.get("segment") == "NSE_FO" and i.get("instrument_type") in ("CE", "PE")
                and str(i.get("underlying_symbol", "")).upper() == sym]
        if not opts:
            raise UpstoxError(f"{sym} has no NSE options")
        today = datetime.now(IST).date()
        expiries = sorted({
            datetime.fromtimestamp(o["expiry"] / 1000, IST).date().isoformat()
            for o in opts if o.get("expiry")
            and datetime.fromtimestamp(o["expiry"] / 1000, IST).date() >= today
        })
        key = opts[0].get("underlying_key") or find_equity(self.instruments(), sym)["instrument_key"]
        return key, expiries

    def option_chain(self, symbol: str, expiry: Optional[str] = None) -> dict:
        """Normalised option chain (see ``option_analytics``) for one expiry (default: nearest)."""
        if not self.token:
            raise UpstoxError("The Upstox option chain needs UPSTOX_ACCESS_TOKEN on the backend")
        key, expiries = self.option_meta(symbol)
        expiry = expiry or (expiries[0] if expiries else None)
        if expiry is None:
            raise UpstoxError(f"No upcoming option expiries for {symbol}")
        body = self._cached(("chain", key, expiry), 30, lambda: self._get(
            "/v2/option/chain", {"instrument_key": key, "expiry_date": expiry}))
        return parse_option_chain(body.get("data") or [], symbol, expiry, expiries)


IST = timezone(timedelta(hours=5, minutes=30))


def _opt_side(d: Optional[dict]) -> Optional[dict]:
    if not d:
        return None
    md, gk = d.get("market_data") or {}, d.get("option_greeks") or {}
    return {
        "ltp": md.get("ltp"), "close": md.get("close_price"), "oi": md.get("oi"), "prev_oi": md.get("prev_oi"),
        "volume": md.get("volume"), "bid": md.get("bid_price"), "ask": md.get("ask_price"),
        "iv": gk.get("iv"), "delta": gk.get("delta"), "gamma": gk.get("gamma"), "theta": gk.get("theta"),
        "key": d.get("instrument_key"),
    }


def parse_option_chain(data: list, symbol: str, expiry: str, expiries: list[str]) -> dict:
    """Upstox /v2/option/chain rows -> normalised chain."""
    rows = [{"strike": float(r["strike_price"]), "CE": _opt_side(r.get("call_options")),
             "PE": _opt_side(r.get("put_options"))} for r in data if r.get("strike_price") is not None]
    spot = next((r.get("underlying_spot_price") for r in data if r.get("underlying_spot_price")), None)
    return {"symbol": symbol.upper(), "expiry": expiry, "expiries": expiries, "spot": spot,
            "source": "upstox", "rows": rows}


_default_client: Optional[UpstoxClient] = None


def instrument_by_key(key: str) -> Optional[dict]:
    """Instrument-file record for an instrument key (lot size, trading symbol, ...)."""
    c = client()
    index = c._cached("by_key", 12 * 3600, lambda: {i.get("instrument_key"): i for i in c.instruments()})
    return index.get(key)


def reset_client() -> None:
    """Forget the cached client so the next call picks up a new access token."""
    global _default_client
    _default_client = None


def client() -> UpstoxClient:
    global _default_client
    if _default_client is None:
        _default_client = UpstoxClient()
    return _default_client


def get_price_oi_history(symbol: str, days: int = 730) -> tuple[pd.DataFrame, Optional[str]]:
    return client().price_oi_history(symbol, days)


def get_daily_prices(symbol: str, days: int = 120) -> pd.DataFrame:
    end = date.today()
    eq = find_equity(client().instruments(), symbol)
    df = client().daily_candles(eq["instrument_key"], end - timedelta(days=days), end)
    return df.drop(columns="open_interest")
