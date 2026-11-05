"""Alert API.

* ``GET    /api/alerts?since=<id>``          new alerts (newest first) + watchlist + scanner status
* ``PUT    /api/alerts/watchlist``           replace the watchlist ``{"symbols": [..]}``
* ``POST   /api/alerts/watchlist/{symbol}``  add one symbol
* ``DELETE /api/alerts/watchlist/{symbol}``  remove one symbol
* ``POST   /api/alerts/scan``                scan now
* ``POST   /api/alerts/test``                emit a test alert (to check notifications)

Configuration: ``ALERTS_FILE`` (default ``backend/.alerts.json``), ``ALERTS_ENABLED``
(default 1), ``ALERTS_INTERVAL`` seconds while the market is open (default 60).
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from .engine import AlertScanner, AlertStore

router = APIRouter()
DEFAULT_FILE = Path(__file__).resolve().parents[2] / ".alerts.json"
_scanner: Optional[AlertScanner] = None


def _default_loader(symbol: str):
    from app import nse_data, upstox_data  # late import: avoid cycles with app.main
    provider = upstox_data if os.getenv("DATA_SOURCE", "upstox").lower() == "upstox" else nse_data
    return provider.get_price_oi_history(symbol, 730)


def get_scanner() -> AlertScanner:
    global _scanner
    if _scanner is None:
        store = AlertStore(os.getenv("ALERTS_FILE") or DEFAULT_FILE)
        _scanner = AlertScanner(store, _default_loader,
                                open_interval=float(os.getenv("ALERTS_INTERVAL", "60")))
    return _scanner


def reset_scanner(scanner: Optional[AlertScanner] = None) -> None:
    """For tests: swap in a scanner (or drop the cached one)."""
    global _scanner
    _scanner = scanner


@asynccontextmanager
async def lifespan(app):
    task = None
    if os.getenv("ALERTS_ENABLED", "1") != "0":
        task = asyncio.create_task(get_scanner().run_forever())
    try:
        yield
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task


class Watchlist(BaseModel):
    symbols: list[str]


def _payload(scanner: AlertScanner, since: int, limit: int) -> dict:
    alerts = scanner.store.since(since, limit)
    return {
        "alerts": alerts,
        "last_id": scanner.store.data["next_id"] - 1,
        "watchlist": scanner.store.watchlist,
        "scanner": scanner.status,
    }


@router.get("/api/alerts")
def list_alerts(since: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=500)):
    return _payload(get_scanner(), since, limit)


@router.put("/api/alerts/watchlist")
def set_watchlist(body: Watchlist):
    try:
        return {"watchlist": get_scanner().store.set_watchlist(body.symbols)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/api/alerts/watchlist/{symbol}")
def add_symbol(symbol: str):
    store = get_scanner().store
    return set_watchlist(Watchlist(symbols=store.watchlist + [symbol]))


@router.delete("/api/alerts/watchlist/{symbol}")
def remove_symbol(symbol: str):
    store = get_scanner().store
    return set_watchlist(Watchlist(symbols=[s for s in store.watchlist if s != symbol.upper()]))


@router.post("/api/alerts/scan")
async def scan_now():
    scanner = get_scanner()
    new = await run_in_threadpool(scanner.scan_all)
    return {"new": new, "scanner": scanner.status}


@router.post("/api/alerts/test")
def test_alert():
    return get_scanner().store.emit({
        "symbol": "TEST", "kind": "test", "side": "BUY", "bar_date": None, "price": None,
        "stop": None, "target": None, "wave": None,
        "reason": "Test alert - if you can see this as a notification, alerts are working.",
    })
