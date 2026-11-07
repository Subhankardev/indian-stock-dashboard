"""Upstox token health check and the "Log in with Upstox" flow.

Status (``GET /api/upstox/status``) answers "will market-data calls work?" by
making one cheap authenticated call (``/v2/market-quote/ltp`` for NIFTY 50):

* ``ok``       - the token works (shows whose token and when it expires)
* ``limited``  - an *extended* token: Upstox only accepts these for read-only
                 account data (positions, holdings, orders), not market data
* ``expired``  - Upstox rejects the token (daily tokens expire ~03:30 IST)
* ``invalid``  - the token isn't usable as text (e.g. copied with masking dots)
* ``missing``  - no token set
* ``unknown``  - Upstox couldn't be reached to check

Login (needs ``UPSTOX_API_KEY`` + ``UPSTOX_API_SECRET`` from your Upstox app):

1. ``GET /api/upstox/login`` redirects to Upstox's login page.
2. Upstox redirects back to ``UPSTOX_REDIRECT_URI``
   (default ``http://localhost:8000/api/upstox/callback``) with a one-time code.
3. The backend exchanges the code for the day's ``access_token``, saves it to
   ``backend/.env`` and starts using it immediately - no restart needed.

The redirect URL must be registered, exactly, in your Upstox app's settings.
"""
from __future__ import annotations

import base64
import html
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app import config, upstox_data

API = "https://api.upstox.com"
DEFAULT_REDIRECT = "http://localhost:8000/api/upstox/callback"
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:3000")
IST = timezone(timedelta(hours=5, minutes=30))
CHECK_KEY = "NSE_INDEX|Nifty 50"
STATUS_TTL = 300  # seconds between real checks against Upstox
STATE_TTL = 600   # seconds a login attempt stays valid

router = APIRouter()
_session = requests.Session()
_status_cache: dict = {}
_pending_states: dict[str, float] = {}


# --------------------------------------------------------------------------- #
# Token inspection
# --------------------------------------------------------------------------- #
def jwt_claims(token: Optional[str]) -> dict:
    """Public claims of a JWT (no signature check - only used for display)."""
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except Exception:
        return {}


def _current_token() -> Optional[str]:
    return config.clean_token(os.getenv("UPSTOX_ACCESS_TOKEN"))


def _app_credentials() -> tuple[Optional[str], Optional[str], str]:
    return (config.clean_token(os.getenv("UPSTOX_API_KEY")),
            config.clean_token(os.getenv("UPSTOX_API_SECRET")),
            (os.getenv("UPSTOX_REDIRECT_URI") or DEFAULT_REDIRECT).strip())


def check_token(token: Optional[str], session: Optional[requests.Session] = None) -> dict:
    session = session or _session  # looked up at call time (tests swap it)
    claims = jwt_claims(token) if token else {}
    exp = claims.get("exp")
    out = {
        "expires_at": datetime.fromtimestamp(exp, IST).isoformat() if exp else None,
        "is_extended": bool(claims.get("isExtended")),
    }
    if not token:
        return {**out, "state": "missing", "message": "No Upstox access token set."}
    problem = config.token_problem(token)
    if problem:
        if "U+2022" in problem:
            problem = "was copied with masking dots (•••) - the real characters are missing; copy the full token"
        return {**out, "state": "invalid", "message": f"The token {problem}."}
    try:
        r = session.get(f"{API}/v2/market-quote/ltp", params={"instrument_key": CHECK_KEY},
                        headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
                        timeout=10)
    except requests.RequestException as exc:
        return {**out, "state": "unknown", "message": f"Couldn't reach Upstox to check the token ({type(exc).__name__})."}
    if r.status_code == 200:
        return {**out, "state": "ok", "message": "Token works for market data."}
    if r.status_code in (401, 403):
        if out["is_extended"]:
            return {**out, "state": "limited", "message": (
                "This is an Upstox extended token: it only works for read-only account data, "
                "not market data, the live feed or the option chain. Log in with Upstox to get the day's token.")}
        if exp and exp < time.time():
            return {**out, "state": "expired", "message": "The token has expired. Log in with Upstox to get today's token."}
        return {**out, "state": "expired", "message": (
            "Upstox rejected the token (expired or revoked). Log in with Upstox to get today's token.")}
    return {**out, "state": "unknown", "message": f"Upstox answered HTTP {r.status_code} to the token check."}


def token_status(force: bool = False) -> dict:
    token = _current_token()
    key, secret, redirect = _app_credentials()
    cached = _status_cache.get("value")
    if force or not cached or _status_cache.get("token") != token or time.time() - _status_cache["at"] > STATUS_TTL:
        cached = check_token(token)
        _status_cache.update(value=cached, token=token, at=time.time())
    return {**cached, "login_available": bool(key and secret), "redirect_uri": redirect,
            "checked_at": datetime.fromtimestamp(_status_cache["at"], IST).isoformat()}


# --------------------------------------------------------------------------- #
# Login flow
# --------------------------------------------------------------------------- #
def login_url(state: str) -> str:
    key, _, redirect = _app_credentials()
    return f"{API}/v2/login/authorization/dialog?" + urlencode(
        {"response_type": "code", "client_id": key, "redirect_uri": redirect, "state": state})


def exchange_code(code: str, session: Optional[requests.Session] = None) -> dict:
    session = session or _session
    key, secret, redirect = _app_credentials()
    r = session.post(
        f"{API}/v2/login/authorization/token",
        data={"code": code, "client_id": key, "client_secret": secret,
              "redirect_uri": redirect, "grant_type": "authorization_code"},
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code != 200 or not body.get("access_token"):
        errs = body.get("errors") or [{}]
        detail = errs[0].get("message") if isinstance(errs, list) and errs else None
        raise RuntimeError(detail or f"Upstox token exchange failed (HTTP {r.status_code})")
    return body


def apply_new_token(token: str) -> None:
    """Save the token to backend/.env and switch every part of the app over to it."""
    config.set_env_value("UPSTOX_ACCESS_TOKEN", token)
    upstox_data.reset_client()
    _status_cache.clear()


def _page(title: str, body: str, ok: bool) -> HTMLResponse:
    colour = "#047857" if ok else "#b91c1c"
    return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title></head>
<body style="font-family:-apple-system,Segoe UI,sans-serif;background:#f4f6fa;margin:0;display:grid;place-items:center;min-height:100vh">
<div style="background:#fff;border:1px solid #e5e8ef;border-radius:12px;padding:28px 32px;max-width:520px;box-shadow:0 1px 3px rgba(0,0,0,.06)">
<h2 style="margin:0 0 10px;color:{colour}">{html.escape(title)}</h2><p style="color:#334155;line-height:1.5">{body}</p>
<p><a href="{html.escape(DASHBOARD_URL)}" style="color:#4f46e5;font-weight:600">Back to the dashboard</a></p></div>
</body></html>""", status_code=200 if ok else 400)


@router.get("/api/upstox/status")
def upstox_status(refresh: bool = False):
    return token_status(force=refresh)


@router.get("/api/upstox/login")
def upstox_login():
    key, secret, redirect = _app_credentials()
    if not (key and secret):
        return JSONResponse(status_code=400, content={"detail": (
            "Set UPSTOX_API_KEY and UPSTOX_API_SECRET (from your app in the Upstox developer console) "
            f"in backend/.env, register {redirect} as the app's redirect URL, then restart the backend.")})
    now = time.time()
    for s, t in list(_pending_states.items()):
        if now - t > STATE_TTL:
            del _pending_states[s]
    state = secrets.token_urlsafe(16)
    _pending_states[state] = now
    return RedirectResponse(login_url(state), status_code=302)


@router.get("/api/upstox/callback", response_class=HTMLResponse)
async def upstox_callback(code: Optional[str] = Query(None), state: Optional[str] = Query(None),
                    error: Optional[str] = Query(None)):
    if error:
        return _page("Upstox login cancelled", html.escape(error), ok=False)
    issued = _pending_states.pop(state or "", None)
    if issued is None or time.time() - issued > STATE_TTL:
        return _page("Login link expired", "This login wasn't started from the dashboard, or took longer than "
                     "10 minutes. Click <b>Log in with Upstox</b> in the dashboard again.", ok=False)
    if not code:
        return _page("Upstox login failed", "Upstox didn't send a login code.", ok=False)
    try:
        body = await run_in_threadpool(exchange_code, code)
    except Exception as exc:
        return _page("Upstox login failed", html.escape(str(exc)), ok=False)
    apply_new_token(config.clean_token(body["access_token"]))
    from app.live import routes as live_routes  # late import: avoid an import cycle
    hub = live_routes._hubs.get("upstox")
    if hub is not None:
        await hub.restart_feed()  # open live feeds switch to the new token
    status = await run_in_threadpool(token_status, True)
    who = html.escape(body.get("user_name") or body.get("user_id") or "your account")
    until = status.get("expires_at")
    until_txt = f" It's valid until {datetime.fromisoformat(until):%d %b %H:%M} IST." if until else ""
    return _page("Logged in to Upstox", f"Saved today's access token for <b>{who}</b> to backend/.env "
                 f"and switched the dashboard to it - no restart needed.{until_txt}", ok=True)
