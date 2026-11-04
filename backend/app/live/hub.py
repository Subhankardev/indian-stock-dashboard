"""Fan one upstream feed out to many browser connections.

* Instrument subscriptions are reference-counted: the upstream feed is only
  subscribed to keys some browser is watching, and the feed is stopped when the
  last browser leaves (freeing the Upstox connection).
* The latest tick per instrument is cached, so a new viewer gets an immediate
  snapshot instead of waiting for the next trade.
* Each viewer has a bounded queue; a slow viewer drops its oldest ticks rather
  than holding up everyone else.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from typing import Callable, Iterable, Optional


class Viewer:
    def __init__(self, keys: Iterable[str], maxsize: int = 500):
        self.keys = set(keys)
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)

    def push(self, item: dict) -> None:
        if self.queue.full():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self.queue.put_nowait(item)


class LiveHub:
    def __init__(self, feed_factory: Callable):
        self._factory = feed_factory
        self.feed = None
        self._task: Optional[asyncio.Task] = None
        self.refs: Counter = Counter()
        self.latest: dict[str, dict] = {}
        self.viewers: set[Viewer] = set()
        self.market_status: dict = {}
        self._lock = asyncio.Lock()

    async def _on_message(self, msg: dict) -> None:
        if msg.get("market_status"):
            self.market_status.update(msg["market_status"])
            for v in self.viewers:
                v.push({"type": "market_status", "market_status": dict(self.market_status)})
        for tick in msg.get("ticks", []):
            key = tick["key"]
            merged = {**self.latest.get(key, {}), **{k: v for k, v in tick.items() if v is not None}}
            self.latest[key] = merged
            for v in self.viewers:
                if key in v.keys:
                    v.push({"type": "tick", "tick": merged})

    async def attach(self, keys: Iterable[str]) -> Viewer:
        viewer = Viewer(keys)
        async with self._lock:
            if self._task is not None and self._task.done():  # previous feed died (e.g. bad token)
                self.feed, self._task = None, None
                self.refs.clear()
            if self.feed is None:
                self.feed = self._factory(self._on_message)
                self._task = asyncio.create_task(self.feed.run())
            new = [k for k in viewer.keys if self.refs[k] == 0]
            for k in viewer.keys:
                self.refs[k] += 1
            self.viewers.add(viewer)
            if new:
                await self.feed.subscribe(new)
        for k in viewer.keys:
            if k in self.latest:
                viewer.push({"type": "tick", "tick": self.latest[k]})
        return viewer

    async def detach(self, viewer: Viewer) -> None:
        async with self._lock:
            self.viewers.discard(viewer)
            gone = []
            for k in viewer.keys:
                self.refs[k] -= 1
                if self.refs[k] <= 0:
                    del self.refs[k]
                    self.latest.pop(k, None)
                    gone.append(k)
            if self.feed is not None and gone:
                await self.feed.unsubscribe(gone)
            if not self.viewers:
                await self._stop_feed()

    async def _stop_feed(self) -> None:
        if self.feed is not None:
            self.feed.stop()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self.feed, self._task = None, None

    async def close(self) -> None:
        async with self._lock:
            self.viewers.clear()
            self.refs.clear()
            await self._stop_feed()

    def info(self) -> dict:
        base = self.feed.info() if self.feed else {"status": "idle", "error": None, "keys": []}
        return {**base, "viewers": len(self.viewers), "market_status": self.market_status}
