"""Data-source adapters and the source registry.

NSE archive files are fetched politely (paced, retried, browser-like headers) and every raw
file is cached under backend/data/raw/<dataset>/ so re-runs cost nothing. A 404 for a
weekday file means "no trading that day" (holiday) and is reported as such.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Optional

import requests

from .config import DATA_DIR, load

ARCHIVE = "https://nsearchives.nseindia.com"
WWW = "https://www.nseindia.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/csv,application/json,application/zip,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


@dataclass(frozen=True)
class Dataset:
    name: str
    description: str
    url: Callable[[date], str]
    filename: Callable[[date], str]
    cost: str = "Free (NSE official)"
    history: str = "Daily files, many years"
    update: str = "After market close"
    notes: str = ""


def _ddmmyyyy(d: date) -> str:
    return d.strftime("%d%m%Y")


DATASETS: dict[str, Dataset] = {
    "cm": Dataset(
        "cm", "Cash-market OHLCV + deliverable quantity for every NSE security",
        lambda d: f"{ARCHIVE}/products/content/sec_bhavdata_full_{_ddmmyyyy(d)}.csv",
        lambda d: f"sec_bhavdata_full_{_ddmmyyyy(d)}.csv",
        update="~6 pm IST", notes="Includes delisted symbols (survivorship-bias-free)."),
    "indices": Dataset(
        "indices", "Daily OHLC, P/E, P/B for all NSE indices",
        lambda d: f"{ARCHIVE}/content/indices/ind_close_all_{_ddmmyyyy(d)}.csv",
        lambda d: f"ind_close_all_{_ddmmyyyy(d)}.csv",
        update="~6 pm IST", notes="NSE sometimes repeats a row; de-duplicated on load."),
    "fo": Dataset(
        "fo", "F&O bhavcopy (UDiFF): futures/options prices, OI, change in OI",
        lambda d: f"{ARCHIVE}/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip",
        lambda d: f"BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip",
        history="UDiFF format since July 2024", update="~6 pm IST"),
    "participant_oi": Dataset(
        "participant_oi", "Participant-wise OI (Client/DII/FII/Pro) — market-wide, not stock-specific",
        lambda d: f"{ARCHIVE}/content/nsccl/fao_participant_oi_{_ddmmyyyy(d)}.csv",
        lambda d: f"fao_participant_oi_{_ddmmyyyy(d)}.csv",
        update="~7 pm IST"),
}

INDEX_LISTS = {
    "nifty50": f"{ARCHIVE}/content/indices/ind_nifty50list.csv",
    "nifty100": f"{ARCHIVE}/content/indices/ind_nifty100list.csv",
    "nifty200": f"{ARCHIVE}/content/indices/ind_nifty200list.csv",
    "nifty500": f"{ARCHIVE}/content/indices/ind_nifty500list.csv",
}
DEALS_TODAY = {"bulk": f"{ARCHIVE}/content/equities/bulk.csv", "block": f"{ARCHIVE}/content/equities/block.csv"}
FII_DII_API = f"{WWW}/api/fiidiiTradeReact"

# Everything the platform can use, free or paid (shown in the UI / docs).
REGISTRY = [
    {"source": "NSE archives (cm, indices, fo, participant_oi, index lists, bulk/block)",
     "auth": "none", "cost": "free", "rate_limit": "unpublished; paced ≥0.5 s", "status": "integrated"},
    {"source": "NSE JSON API (FII/DII, historical deals, shareholding)", "auth": "session cookie from nseindia.com",
     "cost": "free", "rate_limit": "unpublished; paced", "status": "integrated (FII/DII, deals); shareholding in Phase 2"},
    {"source": "Upstox API", "auth": "daily OAuth token", "cost": "free for data",
     "rate_limit": "per-second/minute limits; paced",
     "status": "integrated: daily prices + indices + futures OI (python -m intel.upstox_ingest), live feed, "
               "intraday, option chain — no delivery data; current listings only (survivorship bias)"},
    {"source": "NSE tick-by-tick / order book history", "auth": "licence", "cost": "paid",
     "rate_limit": "n/a", "status": "not integrated — needed for Phase 4 order flow"},
    {"source": "Licensed fundamentals API", "auth": "API key", "cost": "paid", "rate_limit": "vendor",
     "status": "optional; filings + CSV import used instead"},
]


class Fetcher:
    """Paced, cached, retrying HTTP fetcher for NSE files."""

    def __init__(self, session: Optional[requests.Session] = None, cache_dir: Optional[Path] = None,
                 min_interval: Optional[float] = None, retries: Optional[int] = None):
        cfg = load()["nse"]
        self.session = session or requests.Session()
        self.cache_dir = Path(cache_dir or DATA_DIR / "raw")
        self.min_interval = cfg["min_interval_s"] if min_interval is None else min_interval
        self.retries = cfg["retries"] if retries is None else retries
        self.timeout = cfg["timeout_s"]
        self._last = 0.0
        self._warmed = False

    def _pace(self) -> None:
        wait = self._last + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def _warm(self) -> None:
        """nseindia.com JSON endpoints need the cookies set by the home page."""
        if not self._warmed:
            self._pace()
            try:
                self.session.get(WWW, headers=HEADERS, timeout=self.timeout)
            except requests.RequestException:
                pass
            self._warmed = True

    def get(self, url: str, cache_as: Optional[str] = None, use_cache: bool = True) -> Optional[bytes]:
        """Bytes of the file, or None if it doesn't exist (404)."""
        path = self.cache_dir / cache_as if cache_as else None
        if path and use_cache and path.is_file():
            return path.read_bytes()
        if url.startswith(WWW):
            self._warm()
        last_err = None
        for attempt in range(self.retries + 1):
            self._pace()
            try:
                r = self.session.get(url, headers=HEADERS, timeout=self.timeout)
            except requests.RequestException as exc:
                last_err = exc
            else:
                if r.status_code == 404:
                    return None
                if r.status_code == 200 and r.content:
                    if path:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(r.content)
                    return r.content
                last_err = RuntimeError(f"HTTP {r.status_code}")
            time.sleep(min(2 ** attempt, 8) if self.min_interval else 0)
        raise RuntimeError(f"Could not fetch {url}: {last_err}")

    def get_json(self, url: str, params: Optional[dict] = None):
        self._warm()
        self._pace()
        r = self.session.get(url, headers={**HEADERS, "Accept": "application/json"}, params=params,
                             timeout=self.timeout)
        r.raise_for_status()
        return r.json()


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
