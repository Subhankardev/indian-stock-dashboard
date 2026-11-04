"""Upstream market-data feeds.

``UpstoxFeed`` speaks Upstox Market Data Feed V3:

* connect to ``wss://api.upstox.com/v3/feed/market-data-feed`` with
  ``Authorization: Bearer <token>`` (TLS certificates are verified),
* send subscription requests as binary JSON
  ``{"guid": .., "method": "sub"|"unsub", "data": {"mode": .., "instrumentKeys": [..]}}``,
* receive binary protobuf ``FeedResponse`` frames (decoded by ``feed_proto``).

It reconnects with exponential backoff and re-subscribes after a drop; an
HTTP 401/403 on connect stops it (the token is expired or missing).

``MockFeed`` has the same interface and emits random-walk ticks, for offline
development and demos.
"""
from __future__ import annotations

import asyncio
import json
import random
import time
import uuid
from contextlib import asynccontextmanager
from typing import Awaitable, Callable, Iterable, Optional

from . import feed_proto

FEED_URL = "wss://api.upstox.com/v3/feed/market-data-feed"
OnMessage = Callable[[dict], Awaitable[None]]


class FeedAuthError(RuntimeError):
    pass


@asynccontextmanager
async def websocket_connect(url: str, headers: dict):
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidStatus

    try:
        ws = await connect(url, additional_headers=headers, max_size=2 ** 22, ping_interval=20)
    except InvalidStatus as exc:
        code = exc.response.status_code
        if code in (401, 403):
            raise FeedAuthError(
                f"Upstox refused the live feed (HTTP {code}). The access token is missing or expired - "
                "Upstox tokens are valid for one trading day."
            ) from exc
        raise
    try:
        yield ws
    finally:
        await ws.close()


class _BaseFeed:
    source = "base"

    def __init__(self, on_message: OnMessage, mode: str = "full"):
        self.on_message = on_message
        self.mode = mode
        self.keys: set[str] = set()
        self.status = "idle"
        self.error: Optional[str] = None
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True

    def info(self) -> dict:
        return {"source": self.source, "status": self.status, "error": self.error, "keys": sorted(self.keys)}


class UpstoxFeed(_BaseFeed):
    source = "upstox"

    def __init__(self, token: str, on_message: OnMessage, mode: str = "full",
                 connector=websocket_connect, max_backoff: float = 30.0):
        super().__init__(on_message, mode)
        self.token = token
        self._connect = connector
        self._ws = None
        self.max_backoff = max_backoff

    @staticmethod
    def build_request(method: str, keys: Iterable[str], mode: Optional[str] = None) -> bytes:
        data: dict = {"instrumentKeys": sorted(keys)}
        if mode is not None:
            data["mode"] = mode
        return json.dumps({"guid": uuid.uuid4().hex, "method": method, "data": data}).encode()

    async def _send(self, method: str, keys: Iterable[str], mode: Optional[str] = None) -> None:
        if self._ws is not None:
            await self._ws.send(self.build_request(method, keys, mode))

    async def subscribe(self, keys: Iterable[str]) -> None:
        new = set(keys) - self.keys
        self.keys |= new
        if new:
            await self._send("sub", new, self.mode)

    async def unsubscribe(self, keys: Iterable[str]) -> None:
        gone = set(keys) & self.keys
        self.keys -= gone
        if gone:
            await self._send("unsub", gone)

    async def run(self) -> None:
        backoff = 1.0
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "*/*"}
        while not self._stopped:
            self.status = "connecting"
            try:
                async with self._connect(FEED_URL, headers) as ws:
                    self._ws = ws
                    self.status, self.error, backoff = "connected", None, 1.0
                    if self.keys:
                        await self._send("sub", self.keys, self.mode)
                    async for frame in ws:
                        if isinstance(frame, (bytes, bytearray)):
                            await self.on_message(feed_proto.decode(bytes(frame)))
            except FeedAuthError as exc:
                self.status, self.error = "auth_error", str(exc)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # network drop, decode error, ...
                self.error = f"{type(exc).__name__}: {exc}"
            finally:
                self._ws = None
            if self._stopped:
                break
            self.status = "reconnecting"
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self.max_backoff)
        self.status = "stopped"


class MockFeed(_BaseFeed):
    """Random-walk ticks in the same normalised shape as the Upstox feed."""

    source = "mock"
    PREV_OI = 1_000_000.0

    def __init__(self, on_message: OnMessage, mode: str = "full", interval: float = 0.5, seed: Optional[int] = None):
        super().__init__(on_message, mode)
        self.interval = interval
        self.rng = random.Random(seed)
        self.state: dict[str, dict] = {}

    def _init_state(self, key: str) -> dict:
        # Keys look like "MOCK_EQ|RELIANCE" or "MOCK_EQ|RELIANCE|2950.5" (start price).
        parts = key.split("|")
        try:
            prev = float(parts[2])
        except (IndexError, ValueError):
            prev = round(self.rng.uniform(200, 3000), 1)
        return {"prev_close": prev, "ltp": prev, "oi": self.PREV_OI, "volume": 0,
                "day": {"open": prev, "high": prev, "low": prev, "close": prev, "volume": 0}}

    async def subscribe(self, keys: Iterable[str]) -> None:
        for k in keys:
            if k not in self.keys:
                self.keys.add(k)
                self.state[k] = self._init_state(k)

    async def unsubscribe(self, keys: Iterable[str]) -> None:
        for k in keys:
            self.keys.discard(k)
            self.state.pop(k, None)

    def _step(self, key: str) -> dict:
        s = self.state[key]
        s["ltp"] = round(s["ltp"] * (1 + self.rng.gauss(0.0001, 0.0015)), 2)
        qty = self.rng.randint(1, 500)
        s["volume"] += qty
        d = s["day"]
        d.update(high=max(d["high"], s["ltp"]), low=min(d["low"], s["ltp"]), close=s["ltp"], volume=s["volume"])
        tick = {"key": key, "ltp": s["ltp"], "ltt": int(time.time() * 1000), "ltq": qty,
                "prev_close": s["prev_close"], "volume": s["volume"], "day": dict(d),
                "bid": round(s["ltp"] - 0.05, 2), "ask": round(s["ltp"] + 0.05, 2),
                "change_pct": (s["ltp"] / s["prev_close"] - 1) * 100}
        if "FUT" in key:
            s["oi"] = max(0.0, s["oi"] * (1 + self.rng.gauss(0.0002, 0.001)))
            tick["oi"] = round(s["oi"])
        return tick

    async def run(self) -> None:
        self.status = "connected"
        while not self._stopped:
            if self.keys:
                ticks = [self._step(k) for k in sorted(self.keys)]
                await self.on_message({"type": "live_feed", "ts": int(time.time() * 1000), "ticks": ticks,
                                       "market_status": {"NSE_EQ": "NORMAL_OPEN", "NSE_FO": "NORMAL_OPEN"}})
            await asyncio.sleep(self.interval)
        self.status = "stopped"
