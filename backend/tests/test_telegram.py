import time

import pytest
from fastapi.testclient import TestClient

from app.alerts import routes as alert_routes
from app.alerts.engine import AlertScanner, AlertStore
from app.alerts.telegram import TelegramNotifier, format_message
from app.main import app

TOKEN = "123456:SECRET-TOKEN-abcd"
CONFIRMED = {"kind": "confirmed", "side": "BUY", "symbol": "RELIANCE", "price": 2950.5, "stop": 2890.0,
             "target": 3080.25, "reason": "W3 up (score 0.71) | GTI up | OI new_longs", "bar_date": "2026-11-04"}


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


class FakeTelegram:
    """Records requests; replies with queued (status, body) pairs, else ok."""

    def __init__(self, replies=()):
        self.calls, self.replies = [], list(replies)

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        if self.replies:
            return FakeResp(*self.replies.pop(0))
        return FakeResp(200, {"ok": True, "result": {"message_id": len(self.calls)}})


def notifier(session=None, **kw):
    return TelegramNotifier(TOKEN, "42", session=session or FakeTelegram(), background=False, **kw)


# --------------------------------------------------------------- format ---- #
def test_message_format_for_each_kind():
    m = format_message(CONFIRMED)
    assert m.startswith("🟢 <b>BUY confirmed</b> — <b>RELIANCE</b>")
    assert "@ ₹2,950.50" in m and "Stop ₹2,890.00 · Target ₹3,080.25" in m and "Bar 2026-11-04" in m
    forming = format_message({**CONFIRMED, "kind": "forming", "side": "SELL"})
    assert forming.startswith("🔴 <b>SELL forming</b>") and "if the day closes here" in forming
    faded = format_message({**CONFIRMED, "kind": "faded", "stop": None, "reason": "Did not hold into the close"})
    assert faded.startswith("⚪") and "Did not hold" in faded and "Stop" not in faded
    assert "Test alert" in format_message({"kind": "test"})


def test_message_escapes_html():
    m = format_message({**CONFIRMED, "reason": "a < b & <script>"})
    assert "a &lt; b &amp; &lt;script&gt;" in m and "<script>" not in m


# ----------------------------------------------------------------- send ---- #
def test_sends_html_message_to_the_chat():
    s = FakeTelegram()
    tg = notifier(s)
    tg(CONFIRMED)
    url, payload = s.calls[0]
    assert url == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert payload["chat_id"] == "42" and payload["parse_mode"] == "HTML" and "RELIANCE" in payload["text"]
    assert tg.status["sent"] == 1 and tg.status["last_error"] is None


def test_rate_limit_is_retried_once():
    s = FakeTelegram([(429, {"ok": False, "description": "Too Many Requests",
                             "parameters": {"retry_after": 0}})])
    tg = notifier(s)
    tg(CONFIRMED)
    assert len(s.calls) == 2 and tg.status["sent"] == 1


def test_failure_is_recorded_without_leaking_the_token():
    s = FakeTelegram([(401, {"ok": False, "description": f"Unauthorized for bot{TOKEN}"})])
    tg = notifier(s)
    tg(CONFIRMED)  # listener must not raise
    assert tg.status["failed"] == 1
    assert "Unauthorized" in tg.status["last_error"] and TOKEN not in tg.status["last_error"]
    assert TOKEN not in str(tg.info())


def test_kind_filter_and_unconfigured_noop():
    s = FakeTelegram()
    tg = notifier(s, kinds=["confirmed"])
    tg({**CONFIRMED, "kind": "forming"})
    tg(CONFIRMED)
    assert len(s.calls) == 1
    off = TelegramNotifier(None, None, session=FakeTelegram(), background=False)
    off(CONFIRMED)
    assert off.session.calls == [] and off.info()["configured"] is False


def test_background_worker_delivers():
    s = FakeTelegram()
    tg = TelegramNotifier(TOKEN, "42", session=s, background=True)
    tg(CONFIRMED)
    tg({**CONFIRMED, "kind": "forming"})
    for _ in range(100):
        if tg.status["sent"] == 2:
            break
        time.sleep(0.01)
    assert tg.status["sent"] == 2


def test_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123")
    monkeypatch.setenv("TELEGRAM_ALERT_KINDS", "confirmed, test")
    tg = TelegramNotifier.from_env()
    assert tg.configured and tg.chat_id == "-100123" and tg.kinds == {"confirmed", "test"}
    assert tg.info()["token"] == "123456:…abcd"


def test_find_chats_from_updates():
    s = FakeTelegram([(200, {"ok": True, "result": [
        {"update_id": 1, "message": {"chat": {"id": 42, "type": "private", "first_name": "Subhankar"}, "text": "hi"}},
        {"update_id": 2, "message": {"chat": {"id": 42, "type": "private", "first_name": "Subhankar"}, "text": "x"}},
        {"update_id": 3, "channel_post": {"chat": {"id": -100123, "type": "channel", "title": "Signals"}}},
    ]})])
    chats = notifier(s).find_chats()
    assert chats == [{"chat_id": 42, "type": "private", "name": "Subhankar"},
                     {"chat_id": -100123, "type": "channel", "name": "Signals"}]
    assert s.calls[0][0].endswith("/getUpdates")


def test_store_listener_errors_never_break_alerting(tmp_path):
    store = AlertStore(tmp_path / "a.json")
    store.listeners.append(lambda a: 1 / 0)
    assert store.emit({"symbol": "X", "kind": "test", "side": "BUY"})["id"] == 1


# ------------------------------------------------------------------ API ---- #
def _api(tmp_path, tg):
    store = AlertStore(tmp_path / "api.json")
    alert_routes.reset_scanner(AlertScanner(store, lambda s: (None, None)), telegram=tg)
    return TestClient(app)


def test_api_reports_masked_status_and_relays_alerts(tmp_path):
    s = FakeTelegram()
    c = _api(tmp_path, notifier(s))
    info = c.get("/api/alerts").json()["telegram"]
    assert info["configured"] and info["token"] == "123456:…abcd" and TOKEN not in str(info)
    c.post("/api/alerts/test")  # dashboard test alert also goes to Telegram
    assert len(s.calls) == 1 and "Test alert" in s.calls[0][1]["text"]
    r = c.post("/api/alerts/telegram/test")
    assert r.status_code == 200 and r.json()["sent"] == 2


def test_api_telegram_errors(tmp_path):
    c = _api(tmp_path, TelegramNotifier(None, None, background=False))
    assert c.post("/api/alerts/telegram/test").status_code == 400
    assert c.get("/api/alerts/telegram/chats").status_code == 400
    s = FakeTelegram([(401, {"ok": False, "description": "Unauthorized"})])
    c = _api(tmp_path, notifier(s))
    r = c.post("/api/alerts/telegram/test")
    assert r.status_code == 502 and "Unauthorized" in r.json()["detail"]


def test_api_lists_chats(tmp_path):
    s = FakeTelegram([(200, {"ok": True, "result": []})])
    body = _api(tmp_path, notifier(s)).get("/api/alerts/telegram/chats").json()
    assert body["chats"] == [] and "Send any message" in body["hint"]
