import base64
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from fastapi.testclient import TestClient

from app import config, upstox_auth, upstox_data
from app.main import app


def make_jwt(**claims) -> str:
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{b64({'alg': 'HS256'})}.{b64(claims)}.c2ln"


class Resp:
    def __init__(self, status, body=None):
        self.status_code, self._b = status, body or {}

    def json(self):
        return self._b


class FakeUpstox:
    """LTP check + token exchange."""

    def __init__(self, ltp_status=200, token_reply=(200, None)):
        self.ltp_status, self.token_reply, self.calls = ltp_status, token_reply, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("GET", url, params, headers))
        if isinstance(self.ltp_status, Exception):
            raise self.ltp_status
        return Resp(self.ltp_status, {"status": "success"})

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append(("POST", url, data, headers))
        status, body = self.token_reply
        return Resp(status, body)


@pytest.fixture
def env(tmp_path, monkeypatch):
    p = tmp_path / ".env"
    p.write_text("# my settings\nTELEGRAM_CHAT_ID=7844852392\nUPSTOX_ACCESS_TOKEN=old\n")
    monkeypatch.setattr(config, "ENV_FILE", p)
    for k in ("UPSTOX_ACCESS_TOKEN", "UPSTOX_API_KEY", "UPSTOX_API_SECRET", "UPSTOX_REDIRECT_URI"):
        monkeypatch.delenv(k, raising=False)
    upstox_auth._status_cache.clear()
    upstox_auth._pending_states.clear()
    return p


# -------------------------------------------------------------- inspection -- #
def test_jwt_claims():
    assert make_jwt and upstox_auth.jwt_claims(make_jwt(isExtended=True, exp=5)) == {"isExtended": True, "exp": 5}
    assert upstox_auth.jwt_claims("not-a-jwt") == {} and upstox_auth.jwt_claims(None) == {}


@pytest.mark.parametrize("token,ltp,state", [
    (None, 200, "missing"),
    ("abc•••def", 200, "invalid"),
    (make_jwt(exp=time.time() + 3600), 200, "ok"),
    (make_jwt(isExtended=True, exp=time.time() + 3e7), 401, "limited"),
    (make_jwt(exp=time.time() - 60), 401, "expired"),
    (make_jwt(exp=time.time() + 3600), 401, "expired"),
    (make_jwt(exp=time.time() + 3600), requests.ConnectionError("down"), "unknown"),
    (make_jwt(exp=time.time() + 3600), 500, "unknown"),
])
def test_check_token_states(token, ltp, state):
    s = FakeUpstox(ltp_status=ltp)
    out = upstox_auth.check_token(token, session=s)
    assert out["state"] == state
    if state == "invalid":
        assert "masking dots" in out["message"] and s.calls == []
    if state == "ok":
        _, url, params, headers = s.calls[0]
        assert url.endswith("/v2/market-quote/ltp") and params == {"instrument_key": "NSE_INDEX|Nifty 50"}
        assert headers["Authorization"] == f"Bearer {token}"
    if state == "limited":
        assert "extended token" in out["message"] and out["is_extended"]


def test_status_is_cached_until_token_changes(env, monkeypatch):
    s = FakeUpstox()
    monkeypatch.setattr(upstox_auth, "_session", s)
    monkeypatch.setattr(upstox_auth, "check_token", lambda t, session=s: (s.calls.append(t), {"state": "ok"})[1])
    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "t1")
    upstox_auth.token_status(); upstox_auth.token_status()
    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "t2")
    st = upstox_auth.token_status()
    assert s.calls == ["t1", "t2"] and st["login_available"] is False


# ------------------------------------------------------------------- .env -- #
def test_set_env_value_replaces_and_keeps_other_lines(tmp_path, monkeypatch):
    p = tmp_path / ".env"
    p.write_text("A=1\nexport UPSTOX_ACCESS_TOKEN='old'\nB=2\n")
    monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
    config.set_env_value("UPSTOX_ACCESS_TOKEN", "new-token", p)
    config.set_env_value("NEW_KEY", "x", p)
    assert p.read_text() == "A=1\nUPSTOX_ACCESS_TOKEN=new-token\nB=2\nNEW_KEY=x\n"
    import os
    assert os.environ["UPSTOX_ACCESS_TOKEN"] == "new-token"
    monkeypatch.delenv("NEW_KEY")


# ------------------------------------------------------------------ login -- #
def test_login_needs_app_credentials(env):
    r = TestClient(app).get("/api/upstox/login", follow_redirects=False)
    assert r.status_code == 400 and "UPSTOX_API_KEY" in r.json()["detail"]
    assert "http://localhost:8000/api/upstox/callback" in r.json()["detail"]


def test_full_login_flow_saves_token(env, monkeypatch):
    monkeypatch.setenv("UPSTOX_API_KEY", "my-key")
    monkeypatch.setenv("UPSTOX_API_SECRET", "my-secret")
    fresh = make_jwt(sub="4MBJWN", exp=time.time() + 20 * 3600)
    s = FakeUpstox(token_reply=(200, {"access_token": fresh, "user_name": "Subhankar G", "extended_token": "ext"}))
    monkeypatch.setattr(upstox_auth, "_session", s)
    upstox_data.client()  # an old cached client...
    c = TestClient(app)

    r = c.get("/api/upstox/login", follow_redirects=False)
    assert r.status_code == 302
    loc = urlparse(r.headers["location"])
    q = parse_qs(loc.query)
    assert loc.netloc == "api.upstox.com" and loc.path == "/v2/login/authorization/dialog"
    assert q["client_id"] == ["my-key"] and q["response_type"] == ["code"]
    assert q["redirect_uri"] == ["http://localhost:8000/api/upstox/callback"]

    page = c.get("/api/upstox/callback", params={"code": "abc123", "state": q["state"][0]})
    assert page.status_code == 200 and "Logged in to Upstox" in page.text and "Subhankar G" in page.text
    _, url, data, headers = [x for x in s.calls if x[0] == "POST"][0]
    assert url.endswith("/v2/login/authorization/token")
    assert data == {"code": "abc123", "client_id": "my-key", "client_secret": "my-secret",
                    "redirect_uri": "http://localhost:8000/api/upstox/callback", "grant_type": "authorization_code"}
    saved = env.read_text()
    assert f"UPSTOX_ACCESS_TOKEN={fresh}" in saved and "TELEGRAM_CHAT_ID=7844852392" in saved
    assert "# my settings" in saved and "UPSTOX_ACCESS_TOKEN=old" not in saved
    assert upstox_data.client().token == fresh, "...is replaced by one with the new token"
    assert c.get("/api/upstox/status").json()["state"] == "ok"

    again = c.get("/api/upstox/callback", params={"code": "abc123", "state": q["state"][0]})
    assert again.status_code == 400 and "expired" in again.text.lower(), "a state works only once"


def test_callback_rejects_unknown_state_and_reports_upstox_errors(env, monkeypatch):
    monkeypatch.setenv("UPSTOX_API_KEY", "k")
    monkeypatch.setenv("UPSTOX_API_SECRET", "s")
    c = TestClient(app)
    assert c.get("/api/upstox/callback", params={"code": "x", "state": "forged"}).status_code == 400
    assert "cancelled" in c.get("/api/upstox/callback", params={"error": "access_denied"}).text
    s = FakeUpstox(token_reply=(400, {"status": "error", "errors": [{"message": "Invalid Auth code"}]}))
    monkeypatch.setattr(upstox_auth, "_session", s)
    state = parse_qs(urlparse(c.get("/api/upstox/login", follow_redirects=False).headers["location"]).query)["state"][0]
    page = c.get("/api/upstox/callback", params={"code": "bad", "state": state})
    assert page.status_code == 400 and "Invalid Auth code" in page.text
    assert "UPSTOX_ACCESS_TOKEN=old" in env.read_text(), "nothing saved on failure"


# -------------------------------------------------------------- live feed -- #
def test_hub_restart_feed_keeps_subscriptions():
    import asyncio
    from app.live.hub import LiveHub

    class Feed:
        def __init__(self, cb):
            self.subs, self.stopped = [], False

        async def subscribe(self, keys):
            self.subs.append(sorted(keys))

        async def unsubscribe(self, keys):
            pass

        async def run(self):
            await asyncio.sleep(3600)

        def stop(self):
            self.stopped = True

        def info(self):
            return {"status": "connected", "error": None, "keys": []}

    async def scenario():
        feeds = []
        hub = LiveHub(lambda cb: feeds.append(Feed(cb)) or feeds[-1])
        await hub.restart_feed()  # nobody watching: no-op
        v = await hub.attach(["A", "B"])
        await hub.restart_feed()
        await hub.close()
        return feeds, v

    feeds, v = asyncio.run(scenario())
    assert len(feeds) == 2 and feeds[0].stopped
    assert feeds[1].subs == [["A", "B"]]
