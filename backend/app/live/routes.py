"""Browser-facing live endpoints.

``WS /ws/live?symbols=RELIANCE,TCS&source=upstox|mock``

The browser never sees the Upstox token: the backend holds the single upstream
connection and relays JSON messages:

* ``{"type": "hello", "source": .., "symbols": [{symbol, EQ, FUT, fut_symbol, prev_oi}]}``
* ``{"type": "tick", "symbol": .., "leg": "EQ"|"FUT", "tick": {...}, "live": {ltp, change_pct, oi, oi_change_pct, regime}}``
* ``{"type": "feed_status", "status": "connecting"|"connected"|"reconnecting"|"auth_error"|.., "error": ..}``
* ``{"type": "market_status", "market_status": {"NSE_EQ": "NORMAL_OPEN", ..}}``
* ``{"type": "error", "message": ..}`` (then the socket closes)

``GET /api/live/status`` shows the upstream connections.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool

from app import upstox_data
from app.config import clean_token

from .feeds import MockFeed, UpstoxFeed
from .hub import LiveHub
from .session import LiveSession, mock_legs

MAX_SYMBOLS = 10
router = APIRouter()
_hubs: dict[str, LiveHub] = {}


def _token():
    return clean_token(os.getenv("UPSTOX_ACCESS_TOKEN"))


def get_hub(source: str) -> LiveHub:
    if source not in _hubs:
        if source == "mock":
            interval = float(os.getenv("MOCK_FEED_INTERVAL", "0.5"))
            _hubs[source] = LiveHub(lambda on_msg: MockFeed(on_msg, interval=interval))
        else:
            _hubs[source] = LiveHub(lambda on_msg: UpstoxFeed(_token(), on_msg, mode="full"))
    return _hubs[source]


@asynccontextmanager
async def lifespan(app):
    yield
    for hub in list(_hubs.values()):
        await hub.close()
    _hubs.clear()


async def _fail(ws: WebSocket, message: str) -> None:
    await ws.send_json({"type": "error", "message": message})
    await ws.close(code=1008)


@router.get("/api/live/status")
def live_status():
    return {"token_configured": bool(_token()), "feeds": {s: h.info() for s, h in _hubs.items()}}


@router.websocket("/ws/live")
async def live_feed(ws: WebSocket, symbols: str = "", source: str = "upstox", base: float | None = None):
    """``base`` (mock only) starts the random walk at a given price, e.g. the chart's last close."""
    await ws.accept()
    syms = list(dict.fromkeys(s.strip().upper() for s in symbols.split(",") if s.strip()))
    if not syms:
        return await _fail(ws, "Pass ?symbols=RELIANCE,TCS")
    if len(syms) > MAX_SYMBOLS:
        return await _fail(ws, f"At most {MAX_SYMBOLS} symbols per connection")
    if source not in ("upstox", "mock"):
        return await _fail(ws, "source must be 'upstox' or 'mock'")
    if source == "upstox" and not _token():
        return await _fail(ws, "The live Upstox feed needs UPSTOX_ACCESS_TOKEN on the backend "
                               "(or use source=mock for demo ticks).")
    try:
        if source == "mock":
            legs = [mock_legs(s, MockFeed.PREV_OI, base) for s in syms]
        else:
            legs = await run_in_threadpool(lambda: [upstox_data.client().live_legs(s) for s in syms])
    except Exception as exc:
        return await _fail(ws, f"Could not resolve {', '.join(syms)}: {exc}")

    session = LiveSession(legs)
    hub = get_hub(source)
    viewer = await hub.attach(session.keys)
    await ws.send_json({"type": "hello", "source": source, "symbols": legs})

    async def pump():
        last_status = None
        try:
            while True:
                try:
                    item = await asyncio.wait_for(viewer.queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    item = None
                info = hub.info()
                status = (info["status"], info["error"])
                if status != last_status:
                    last_status = status
                    await ws.send_json({"type": "feed_status", "status": status[0], "error": status[1]})
                if item is None:
                    continue
                if item["type"] == "tick":
                    out = session.on_tick(item["tick"])
                    if out:
                        await ws.send_json(out)
                else:
                    await ws.send_json(item)
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            pass

    pump_task = asyncio.create_task(pump())
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    finally:
        pump_task.cancel()
        await hub.detach(viewer)
