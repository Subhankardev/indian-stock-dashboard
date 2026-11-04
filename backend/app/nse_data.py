import time
from datetime import date, timedelta
from typing import Optional

import requests
import pandas as pd

NIFTY_50 = [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "HINDUNILVR",
    "SBIN", "KOTAKBANK", "ITC", "BHARTIARTL", "LT", "AXISBANK", "ASIANPAINT",
    "BAJFINANCE", "HCLTECH", "MARUTI", "SUNPHARMA", "TITAN", "ULTRACEMCO", "WIPRO"
]

HEADERS = {'User-Agent': 'Mozilla/5.0'}

def get_stock_data(symbol: str):
    url = f"https://www.nseindia.com/api/quote-equity?symbol={symbol.upper()}"
    session = requests.Session()
    session.get('https://www.nseindia.com', headers=HEADERS)
    resp = session.get(url, headers=HEADERS)
    data = resp.json()["priceInfo"]["history"]
    df = pd.DataFrame(data)
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    for col in ["open", "dayHigh", "dayLow", "lastPrice", "totalTradedVolume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.rename(columns={
        "dayHigh": "high",
        "dayLow": "low",
        "lastPrice": "close",
        "totalTradedVolume": "volume"
    }, inplace=True)
    return df[["open", "high", "low", "close", "volume"]]

def get_option_chain(symbol: str):
    url = f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol.upper()}"
    session = requests.Session()
    session.get('https://www.nseindia.com', headers=HEADERS)
    resp = session.get(url, headers=HEADERS)
    data = resp.json()
    return data


# --------------------------------------------------------------------------- #
# Daily price + futures open-interest history (for the wave / GTI / OI module)
# --------------------------------------------------------------------------- #
NSE_BASE = "https://www.nseindia.com"
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}
_CHUNK_DAYS = 90            # NSE historical endpoints reject long date ranges
_CACHE_TTL = 15 * 60        # seconds
_cache: dict = {}


def _nse_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    s.get(NSE_BASE, timeout=10)  # sets the cookies the API requires
    return s


def _cached(key, loader):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1].copy()
    value = loader()
    _cache[key] = (time.time(), value)
    return value.copy()


def _date_chunks(days: int):
    end = date.today()
    cur = end - timedelta(days=days)
    while cur <= end:
        nxt = min(cur + timedelta(days=_CHUNK_DAYS - 1), end)
        yield cur.strftime("%d-%m-%Y"), nxt.strftime("%d-%m-%Y")
        cur = nxt + timedelta(days=1)


def _pick(row: dict, *keys):
    for k in keys:
        if row.get(k) not in (None, "", "-"):
            return row[k]
    return None


def _fetch_rows(path: str, params: dict, days: int) -> list:
    s = _nse_session()
    rows = []
    for frm, to in _date_chunks(days):
        resp = s.get(f"{NSE_BASE}{path}", params={**params, "from": frm, "to": to}, timeout=15)
        resp.raise_for_status()
        rows.extend(resp.json().get("data", []))
    return rows


def parse_price_rows(rows: list) -> pd.DataFrame:
    df = pd.DataFrame({
        "date": [_pick(r, "CH_TIMESTAMP", "mTIMESTAMP") for r in rows],
        "open": [_pick(r, "CH_OPENING_PRICE") for r in rows],
        "high": [_pick(r, "CH_TRADE_HIGH_PRICE") for r in rows],
        "low": [_pick(r, "CH_TRADE_LOW_PRICE") for r in rows],
        "close": [_pick(r, "CH_CLOSING_PRICE", "CH_LAST_TRADED_PRICE") for r in rows],
        "volume": [_pick(r, "CH_TOT_TRADED_QTY") for r in rows],
    })
    df["date"] = pd.to_datetime(df["date"], errors="coerce", format="mixed")
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["date", "close"]).drop_duplicates("date").set_index("date").sort_index()


def parse_futures_oi_rows(rows: list) -> pd.Series:
    """Total OI per day, summed over every live expiry (smooths month-end rollover)."""
    df = pd.DataFrame({
        "date": [_pick(r, "FH_TIMESTAMP", "TIMESTAMP") for r in rows],
        "expiry": [_pick(r, "FH_EXPIRY_DT", "EXPIRY_DT") for r in rows],
        "oi": [_pick(r, "FH_OPEN_INT", "OPEN_INT") for r in rows],
    })
    df["date"] = pd.to_datetime(df["date"], errors="coerce", format="mixed")
    df["oi"] = pd.to_numeric(df["oi"], errors="coerce")
    df = df.dropna(subset=["date", "oi"]).drop_duplicates(["date", "expiry"])
    return df.groupby("date")["oi"].sum().rename("open_interest").sort_index()


def get_price_history(symbol: str, days: int = 365) -> pd.DataFrame:
    """Daily OHLCV for an NSE equity (EQ series), oldest first."""
    def load():
        rows = _fetch_rows("/api/historical/cm/equity", {"symbol": symbol.upper(), "series": '["EQ"]'}, days)
        if not rows:
            raise ValueError(f"No price history returned for {symbol}")
        return parse_price_rows(rows)
    return _cached(("price", symbol.upper(), days), load)


def get_futures_oi_history(symbol: str, days: int = 365) -> pd.Series:
    """Daily stock-futures open interest for an F&O stock."""
    def load():
        rows = _fetch_rows(
            "/api/historical/fo/derivatives", {"instrumentType": "FUTSTK", "symbol": symbol.upper()}, days
        )
        if not rows:
            raise ValueError(f"No futures OI returned for {symbol} (is it an F&O stock?)")
        return parse_futures_oi_rows(rows)
    return _cached(("oi", symbol.upper(), days), load)


def get_price_oi_history(symbol: str, days: int = 365) -> tuple[pd.DataFrame, Optional[str]]:
    """Price history joined with futures OI.

    Returns (frame, warning). If OI can't be fetched (non-F&O stock, NSE
    blocking, ...) the frame has no ``open_interest`` column and the warning
    says why; the analysis then runs without OI confirmation.
    """
    price = get_price_history(symbol, days)
    try:
        oi = get_futures_oi_history(symbol, days)
    except Exception as exc:  # surface any NSE failure as a warning, not an error
        return price, f"Open interest unavailable: {exc}"
    df = price.join(oi, how="left")
    df["open_interest"] = df["open_interest"].ffill()
    return df, None