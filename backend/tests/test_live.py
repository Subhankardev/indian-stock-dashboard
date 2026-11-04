import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from app.live import feed_proto
from app.live.feeds import FEED_URL, FeedAuthError, MockFeed, UpstoxFeed
from app.live.hub import LiveHub
from app.live.session import LiveSession
from app.main import app
from app.wave_oi.oi import classify_regime


def frame(key="NSE_FO|1", ltp=101.0, cp=100.0, oi=5000.0, vtt=77) -> bytes:
    r = feed_proto.FeedResponse(type=1, currentTs=1700000000000)
    m = r.feeds[key].fullFeed.marketFF
    m.ltpc.ltp, m.ltpc.cp, m.ltpc.ltt, m.oi, m.vtt = ltp, cp, 1700000000000, oi, vtt
    q = m.marketLevel.bidAskQuote.add()
    q.bidP, q.bidQ, q.askP, q.askQ = ltp - 0.1, 10, ltp + 0.1, 20
    o = m.marketOHLC.ohlc.add()
    o.interval, o.open, o.high, o.low, o.close, o.vol = "1d", 99, 102, 98, ltp, 1234
    o2 = m.marketOHLC.ohlc.add()
    o2.interval, o2.close = "I1", 55
    r.marketInfo.segmentStatus["NSE_FO"] = 2
    return r.SerializeToString()


# ------------------------------------------------------------- decoding --- #
def test_decode_full_feed():
    msg = feed_proto.decode(frame())
    assert msg["type"] == "live_feed" and msg["market_status"] == {"NSE_FO": "NORMAL_OPEN"}
    t = msg["ticks"][0]
    assert t["key"] == "NSE_FO|1" and t["ltp"] == 101 and t["prev_close"] == 100
    assert t["oi"] == 5000 and t["volume"] == 77
    assert t["bid"] == pytest.approx(100.9) and t["ask_qty"] == 20
    assert t["day"] == {"open": 99, "high": 102, "low": 98, "close": 101, "volume": 1234}
    assert t["change_pct"] == pytest.approx(1.0)


def test_decode_ltpc_and_index():
    r = feed_proto.FeedResponse(type=0)
    r.feeds["NSE_EQ|A"].ltpc.ltp = 50
    r.feeds["NSE_EQ|A"].ltpc.cp = 40
    r.feeds["NSE_INDEX|Nifty 50"].fullFeed.indexFF.ltpc.ltp = 25000
    ticks = {t["key"]: t for t in feed_proto.decode(r.SerializeToString())["ticks"]}
    assert ticks["NSE_EQ|A"]["change_pct"] == pytest.approx(25.0)
    assert ticks["NSE_INDEX|Nifty 50"]["ltp"] == 25000


# --------------------------------------------------------- upstream feed --- #
class FakeUpstox:
    """Stands in for Upstox: records connections and requests, serves queued frames."""

    def __init__(self, sessions):
        self.sessions = list(sessions)  # one list of frames per connection; "AUTH" = reject
        self.connections = []
        self.sent = []

    def __call__(self, url, headers):
        fake = self

        @asynccontextmanager
        async def cm():
            fake.connections.append((url, headers))
            frames = fake.sessions.pop(0) if fake.sessions else None
            if frames == "AUTH":
                raise FeedAuthError("HTTP 401")

            class WS:
                async def send(self, data):
                    fake.sent.append((len(fake.connections), json.loads(data)))

                def __aiter__(self):
                    return self

                async def __anext__(self):
                    await asyncio.sleep(0.01)
                    if frames:
                        return frames.pop(0)
                    if frames is None:  # last session: stay open until cancelled
                        await asyncio.sleep(3600)
                    raise StopAsyncIteration  # connection dropped

            yield WS()
        return cm()


def test_upstox_feed_subscribes_decodes_and_reconnects():
    async def scenario():
        got = []

        async def on_message(m):
            got.append(m)

        fake = FakeUpstox([[frame(ltp=101)], [frame(ltp=102)]])
        feed = UpstoxFeed("tok", on_message, connector=fake, max_backoff=0.01)
        await feed.subscribe(["NSE_FO|1"])
        task = asyncio.create_task(feed.run())
        for _ in range(200):
            await asyncio.sleep(0.01)
            if len(got) >= 2 and len(fake.connections) >= 3:
                break
        await feed.unsubscribe(["NSE_FO|1"])
        await asyncio.sleep(0.02)
        feed.stop()
        task.cancel()
        return got, fake, feed

    got, fake, feed = asyncio.run(scenario())
    url, headers = fake.connections[0]
    assert url == FEED_URL and headers["Authorization"] == "Bearer tok"
    assert [m["ticks"][0]["ltp"] for m in got[:2]] == [101, 102]
    subs = [(conn, r) for conn, r in fake.sent if r["method"] == "sub"]
    assert [c for c, _ in subs][:2] == [1, 2], "re-subscribes after every reconnect"
    req = subs[0][1]
    assert req["data"] == {"instrumentKeys": ["NSE_FO|1"], "mode": "full"} and len(req["guid"]) == 32
    assert any(r["method"] == "unsub" and "mode" not in r["data"] for _, r in fake.sent)


def test_upstox_feed_stops_on_auth_error():
    async def scenario():
        async def on_message(m):
            pass
        feed = UpstoxFeed("expired", on_message, connector=FakeUpstox(["AUTH"]), max_backoff=0.01)
        await asyncio.wait_for(feed.run(), timeout=2)
        return feed

    feed = asyncio.run(scenario())
    assert feed.status == "auth_error" and "401" in feed.error


# -------------------------------------------------------------------- hub --- #
class RecordingFeed:
    def __init__(self, on_message):
        self.on_message, self.subs, self.unsubs, self.stopped = on_message, [], [], False

    async def subscribe(self, keys):
        self.subs.append(sorted(keys))

    async def unsubscribe(self, keys):
        self.unsubs.append(sorted(keys))

    async def run(self):
        await asyncio.sleep(3600)

    def stop(self):
        self.stopped = True

    def info(self):
        return {"status": "connected", "error": None, "keys": []}


def test_hub_refcounts_and_fans_out():
    async def scenario():
        feeds = []
        hub = LiveHub(lambda cb: feeds.append(RecordingFeed(cb)) or feeds[-1])
        a = await hub.attach(["K1", "K2"])
        b = await hub.attach(["K2", "K3"])
        feed = feeds[0]
        await feed.on_message({"ticks": [{"key": "K2", "ltp": 1}, {"key": "K1", "ltp": 2}]})
        a_items = [a.queue.get_nowait() for _ in range(a.queue.qsize())]
        b_items = [b.queue.get_nowait() for _ in range(b.queue.qsize())]
        c = await hub.attach(["K1"])  # late joiner gets the cached snapshot
        c_items = [c.queue.get_nowait() for _ in range(c.queue.qsize())]
        await hub.detach(a)
        await hub.detach(c)
        unsub_after_a_c = list(feed.unsubs)
        await hub.detach(b)
        return feeds, a_items, b_items, c_items, unsub_after_a_c, hub

    feeds, a_items, b_items, c_items, unsubs, hub = asyncio.run(scenario())
    assert len(feeds) == 1, "one upstream connection shared by all viewers"
    assert feeds[0].subs == [["K1", "K2"], ["K3"]]
    assert {i["tick"]["key"] for i in a_items} == {"K1", "K2"}
    assert {i["tick"]["key"] for i in b_items} == {"K2"}
    assert c_items[0]["tick"] == {"key": "K1", "ltp": 2}
    assert unsubs == [["K1"]], "K2 is still watched by b"
    assert feeds[0].stopped and hub.feed is None, "feed stops when the last viewer leaves"


# ------------------------------------------------------- live regime ------ #
def test_classify_regime():
    assert classify_regime(0.01, 0.02) == "new_longs"
    assert classify_regime(0.01, -0.02) == "short_covering"
    assert classify_regime(-0.01, 0.02) == "new_shorts"
    assert classify_regime(-0.01, -0.02) == "long_liquidation"
    assert classify_regime(0.01, 0.001) == "neutral"


def test_session_combines_equity_price_with_futures_oi():
    s = LiveSession([{"symbol": "RELIANCE", "EQ": "E", "FUT": "F", "prev_oi": 1000.0}])
    s.on_tick({"key": "E", "ltp": 101, "change_pct": 1.0})
    out = s.on_tick({"key": "F", "ltp": 101.5, "change_pct": 1.2, "oi": 1020.0})
    assert out["symbol"] == "RELIANCE" and out["leg"] == "FUT"
    assert out["live"]["ltp"] == 101 and out["live"]["change_pct"] == 1.0
    assert out["live"]["oi_change_pct"] == pytest.approx(2.0)
    assert out["live"]["regime"] == "new_longs"
    assert s.on_tick({"key": "OTHER", "ltp": 1}) is None


# --------------------------------------------------- browser websocket --- #
def test_ws_live_mock_end_to_end(monkeypatch):
    monkeypatch.setenv("MOCK_FEED_INTERVAL", "0.02")
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live?symbols=reliance,TCS&source=mock") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello" and [s["symbol"] for s in hello["symbols"]] == ["RELIANCE", "TCS"]
            seen = set()
            for _ in range(60):
                m = ws.receive_json()
                if m["type"] == "tick":
                    seen.add((m["symbol"], m["leg"]))
                    if m["leg"] == "FUT":
                        assert m["live"]["oi_change_pct"] is not None
                if len(seen) == 4:
                    break
            assert seen == {("RELIANCE", "EQ"), ("RELIANCE", "FUT"), ("TCS", "EQ"), ("TCS", "FUT")}
            assert client.get("/api/live/status").json()["feeds"]["mock"]["viewers"] == 1


def test_ws_live_requires_token_for_upstox(monkeypatch):
    monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live?symbols=RELIANCE") as ws:
            m = ws.receive_json()
            assert m["type"] == "error" and "UPSTOX_ACCESS_TOKEN" in m["message"]


def test_ws_live_rejects_bad_input():
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live?symbols=&source=mock") as ws:
            assert ws.receive_json()["type"] == "error"
        with client.websocket_connect("/ws/live?symbols=A&source=bogus") as ws:
            assert ws.receive_json()["type"] == "error"


def test_mock_feed_starts_at_base_price():
    async def scenario():
        got = []

        async def on_message(m):
            got.append(m)
        feed = MockFeed(on_message, seed=1)
        await feed.subscribe(["MOCK_EQ|X|500.5", "MOCK_FUT|X|500.5"])
        tick = feed._step("MOCK_EQ|X|500.5")
        fut = feed._step("MOCK_FUT|X|500.5")
        return tick, fut

    tick, fut = asyncio.run(scenario())
    assert tick["prev_close"] == 500.5 and abs(tick["ltp"] / 500.5 - 1) < 0.02
    assert "oi" in fut and "oi" not in tick
