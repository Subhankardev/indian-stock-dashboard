"""Alert API.

* ``GET    /api/alerts?since=<id>``          new alerts (newest first) + watchlist + scanner status
* ``PUT    /api/alerts/watchlist``           replace the watchlist ``{"symbols": [..]}``
* ``POST   /api/alerts/watchlist/{symbol}``  add one symbol
* ``DELETE /api/alerts/watchlist/{symbol}``  remove one symbol
* ``POST   /api/alerts/scan``                scan now
* ``POST   /api/alerts/test``                emit a test alert (to check notifications)
* ``POST   /api/alerts/pause`` / ``resume``  stop / restart scanning (paused by default)
* ``DELETE /api/alerts?kind=test|all``       clear test alerts / the whole history
* ``DELETE /api/alerts/{id}``                dismiss one alert
* ``GET    /api/alerts/telegram/chats``      chats that messaged your bot (to find TELEGRAM_CHAT_ID)
* ``POST   /api/alerts/telegram/test``       send a test message to Telegram now

Configuration: ``ALERTS_FILE`` (default ``backend/.alerts.json``), ``ALERTS_ENABLED``
(default 1), ``ALERTS_INTERVAL`` seconds while the market is open (default 60).
Telegram: see ``telegram.py`` (``TELEGRAM_BOT_TOKEN``, ``TELEGRAM_CHAT_ID``, ``TELEGRAM_ALERT_KINDS``).
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
from .telegram import TelegramNotifier

router = APIRouter()
DEFAULT_FILE = Path(__file__).resolve().parents[2] / ".alerts.json"
_scanner: Optional[AlertScanner] = None
_telegram: Optional[TelegramNotifier] = None


def _default_loader(symbol: str):
    from app import nse_data, upstox_data  # late import: avoid cycles with app.main
    provider = upstox_data if os.getenv("DATA_SOURCE", "upstox").lower() == "upstox" else nse_data
    return provider.get_price_oi_history(symbol, 730)


def _default_option_suggester(symbol: str, side: str, target: float, stop: float) -> dict:
    """Option idea from the live Upstox chain (needs the day's token); errors mean 'no idea'."""
    from app import options_suggest
    from app.config import clean_token
    if os.getenv("DATA_SOURCE", "upstox").lower() != "upstox":
        raise RuntimeError("option ideas need the Upstox data source")
    if not clean_token(os.getenv("UPSTOX_ACCESS_TOKEN")):
        raise RuntimeError("option ideas need the day's Upstox token")
    return options_suggest.suggest_for_symbol(symbol, side, target, stop, source="upstox")


def get_telegram() -> TelegramNotifier:
    global _telegram
    if _telegram is None:
        _telegram = TelegramNotifier.from_env()
    return _telegram


def get_scanner() -> AlertScanner:
    global _scanner
    if _scanner is None:
        store = AlertStore(os.getenv("ALERTS_FILE") or DEFAULT_FILE)
        store.listeners.append(get_telegram())
        from app.wave_oi.tuning import load_rules, split_params
        strategy, _ = split_params(load_rules()["params"])  # same rules as the dashboard
        _scanner = AlertScanner(store, _default_loader, config=strategy,
                                open_interval=float(os.getenv("ALERTS_INTERVAL", "60")),
                                option_suggester=_default_option_suggester)
    return _scanner


def reset_scanner(scanner: Optional[AlertScanner] = None, telegram: Optional[TelegramNotifier] = None) -> None:
    """For tests: swap in a scanner / Telegram notifier (or drop the cached ones)."""
    global _scanner, _telegram
    _scanner, _telegram = scanner, telegram
    if scanner is not None and telegram is not None:
        scanner.store.listeners.append(telegram)


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
        "telegram": get_telegram().info(),
        "paused": scanner.store.paused,
    }


@router.get("/api/alerts")
def list_alerts(since: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=500)):
    return _payload(get_scanner(), since, limit)


@router.delete("/api/alerts")
def clear_alerts(kind: str = Query(..., pattern="^(test|forming|faded|confirmed|all)$")):
    return {"removed": get_scanner().store.remove(kind=kind)}


@router.delete("/api/alerts/{alert_id}")
def dismiss_alert(alert_id: int):
    removed = get_scanner().store.remove(ids={alert_id})
    if not removed:
        raise HTTPException(status_code=404, detail="No such alert")
    return {"removed": removed}


@router.post("/api/alerts/pause")
def pause_alerts():
    get_scanner().store.set_paused(True)
    return {"paused": True}


@router.post("/api/alerts/resume")
def resume_alerts():
    get_scanner().store.set_paused(False)
    return {"paused": False}


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


@router.get("/api/alerts/telegram/chats")
def telegram_chats():
    try:
        chats = get_telegram().find_chats()
    except RuntimeError as exc:
        raise HTTPException(status_code=400 if "Set TELEGRAM" in str(exc) else 502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach Telegram: {type(exc).__name__}")
    hint = ("Send any message to your bot in Telegram, then reload this." if not chats
            else "Set TELEGRAM_CHAT_ID to the chat_id you want alerts in, then restart the backend.")
    return {"chats": chats, "hint": hint}


@router.post("/api/alerts/telegram/test")
def telegram_test():
    tg = get_telegram()
    if not tg.configured:
        raise HTTPException(status_code=400, detail="Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID on the backend")
    try:
        tg.send_now({"kind": "test", "side": "BUY", "symbol": "TEST"})
    except Exception:
        raise HTTPException(status_code=502, detail=tg.status["last_error"] or "Telegram send failed")
    return tg.info()
