"""Send alerts to a Telegram chat through a bot.

Setup (about 5 minutes):

1. In Telegram, message **@BotFather**, send ``/newbot`` and follow the prompts.
   It replies with a bot token like ``123456:ABC-DEF...``.
2. Open a chat with your new bot and send it any message (e.g. "hi").
3. Start the backend with ``TELEGRAM_BOT_TOKEN`` set and open
   ``GET /api/alerts/telegram/chats``: it lists the chats that messaged the bot,
   with their ``chat_id``.
4. Restart with ``TELEGRAM_CHAT_ID`` set as well. ``POST /api/alerts/telegram/test``
   (or "Send test alert" in the dashboard) should reach your phone.

Environment:

* ``TELEGRAM_BOT_TOKEN`` / ``TELEGRAM_CHAT_ID`` - both required to send.
* ``TELEGRAM_ALERT_KINDS`` - comma list of alert kinds to send
  (default ``forming,confirmed,faded,test``).

Messages are queued and sent from a background thread, so a slow or unreachable
Telegram never delays the market scan. Failures are recorded in ``status`` and
never affect the alert itself.
"""
from __future__ import annotations

import html
import os
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import requests

API = "https://api.telegram.org"
DEFAULT_KINDS = ("forming", "confirmed", "faded", "test")


def _fmt(v, d: int = 2) -> str:
    return "–" if v is None else f"{float(v):,.{d}f}"


def format_message(alert: dict) -> str:
    """Telegram HTML message for one alert."""
    e = html.escape
    kind, side, sym = alert.get("kind"), alert.get("side"), alert.get("symbol", "")
    if kind == "test":
        return "🔔 <b>Test alert</b>\nTelegram alerts from your stock dashboard are working."
    icon = "⚪" if kind == "faded" else ("🟢" if side == "BUY" else "🔴")
    head = f"{icon} <b>{e(side or '')} {e(kind or '')}</b> — <b>{e(sym)}</b>"
    lines = [head]
    if kind == "faded":
        lines.append(e(alert.get("reason") or "Signal no longer valid"))
    else:
        price = f"@ ₹{_fmt(alert.get('price'))}"
        if kind == "forming":
            price += " <i>(if the day closes here)</i>"
        lines.append(price)
        if alert.get("stop") is not None and alert.get("target") is not None:
            lines.append(f"Stop ₹{_fmt(alert['stop'])} · Target ₹{_fmt(alert['target'])}")
        if alert.get("reason"):
            lines.append(f"<i>{e(alert['reason'])}</i>")
    if alert.get("bar_date"):
        lines.append(f"Bar {e(str(alert['bar_date']))}")
    return "\n".join(lines)


def _mask(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    return token.split(":")[0] + ":…" + token[-4:] if ":" in token else "…" + token[-4:]


class TelegramNotifier:
    def __init__(self, token: Optional[str], chat_id: Optional[str], kinds=DEFAULT_KINDS,
                 session: Optional[requests.Session] = None, background: bool = True):
        self.token = token or None
        self.chat_id = str(chat_id) if chat_id else None
        self.kinds = set(kinds)
        self.session = session or requests.Session()
        self.background = background
        self._q: "queue.Queue[dict]" = queue.Queue(maxsize=200)
        self._worker: Optional[threading.Thread] = None
        self.status = {"sent": 0, "failed": 0, "last_sent": None, "last_error": None}

    @classmethod
    def from_env(cls) -> "TelegramNotifier":
        kinds = [k.strip() for k in os.getenv("TELEGRAM_ALERT_KINDS", ",".join(DEFAULT_KINDS)).split(",") if k.strip()]
        return cls(os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID"), kinds)

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def info(self) -> dict:
        return {
            "configured": self.configured,
            "token": _mask(self.token),
            "chat_id": self.chat_id,
            "kinds": sorted(self.kinds),
            **self.status,
        }

    # -- sending ------------------------------------------------------------- #
    def _post(self, method: str, payload: dict) -> dict:
        url = f"{API}/bot{self.token}/{method}"
        for attempt in range(2):
            resp = self.session.post(url, json=payload, timeout=10)
            try:
                body = resp.json()
            except ValueError:
                body = {"ok": False, "description": resp.text[:200]}
            if resp.status_code == 429 and attempt == 0:  # rate limited: wait as told, retry once
                time.sleep(min(float((body.get("parameters") or {}).get("retry_after", 1)), 10))
                continue
            if not body.get("ok"):
                raise RuntimeError(f"Telegram {method} failed (HTTP {resp.status_code}): "
                                   f"{body.get('description', 'unknown error')}")
            return body
        raise RuntimeError(f"Telegram {method} rate limited")

    def send_now(self, alert: dict) -> None:
        """Send synchronously; raises on failure."""
        if not self.configured:
            raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID on the backend")
        try:
            self._post("sendMessage", {
                "chat_id": self.chat_id, "text": format_message(alert),
                "parse_mode": "HTML", "disable_web_page_preview": True,
            })
        except Exception as exc:
            self.status["failed"] += 1
            # never echo the URL (it contains the token)
            self.status["last_error"] = str(exc).replace(self.token or "\0", "<token>")
            raise
        self.status["sent"] += 1
        self.status["last_sent"] = datetime.now(timezone.utc).isoformat()
        self.status["last_error"] = None

    def __call__(self, alert: dict) -> None:
        """Alert-store listener: queue the alert if it should go to Telegram."""
        if not self.configured or alert.get("kind") not in self.kinds:
            return
        if not self.background:
            try:
                self.send_now(alert)
            except Exception:
                pass
            return
        try:
            self._q.put_nowait(alert)
        except queue.Full:
            self.status["failed"] += 1
            self.status["last_error"] = "Send queue full (Telegram unreachable?)"
            return
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._drain, name="telegram-alerts", daemon=True)
            self._worker.start()

    def _drain(self) -> None:
        while True:
            try:
                alert = self._q.get(timeout=30)
            except queue.Empty:
                return  # idle: let the thread end; the next alert starts a new one
            try:
                self.send_now(alert)
            except Exception:
                pass  # recorded in status

    # -- setup helper -------------------------------------------------------- #
    def find_chats(self) -> list[dict]:
        """Chats that recently messaged the bot (to find TELEGRAM_CHAT_ID)."""
        if not self.token:
            raise RuntimeError("Set TELEGRAM_BOT_TOKEN on the backend first")
        updates = self._post("getUpdates", {"timeout": 0}).get("result", [])
        chats = {}
        for u in updates:
            msg = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
            chat = msg.get("chat")
            if chat:
                chats[chat["id"]] = {
                    "chat_id": chat["id"], "type": chat.get("type"),
                    "name": chat.get("title") or " ".join(
                        p for p in (chat.get("first_name"), chat.get("last_name")) if p) or chat.get("username"),
                }
        return list(chats.values())
