"""Settings from ``backend/.env``.

``load_env_file()`` runs once at startup (top of ``app.main``). Rules:

* A variable already set in the shell wins over ``.env`` (handy for one-off
  overrides like ``DATA_SOURCE=nse uvicorn ...``); ``GET /api/config`` shows when
  that happens, so a stale ``export`` can't silently shadow the file.
* Empty values (``UPSTOX_ACCESS_TOKEN=``) count as "not set".
* Edits to ``.env`` take effect after restarting the backend.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"

# name -> (secret?, what it is for)
SETTINGS: dict[str, tuple[bool, str]] = {
    "DATA_SOURCE": (False, "upstox (default) or nse"),
    "UPSTOX_ACCESS_TOKEN": (True, "Upstox token: live feed, option chain, longer OI history (expires daily)"),
    "UPSTOX_MAX_EXPIRED": (False, "expired futures contracts to pull for OI history (default 24)"),
    "TELEGRAM_BOT_TOKEN": (True, "Telegram bot token from @BotFather"),
    "TELEGRAM_CHAT_ID": (False, "Telegram chat to send alerts to"),
    "TELEGRAM_ALERT_KINDS": (False, "alert kinds sent to Telegram (default forming,confirmed,faded,test)"),
    "ALERTS_ENABLED": (False, "0 turns the alert scanner off (default 1)"),
    "ALERTS_INTERVAL": (False, "seconds between scans while the market is open (default 60)"),
    "ALERTS_FILE": (False, "where the watchlist and alerts are stored (default backend/.alerts.json)"),
}

_sources: dict[str, str] = {}
_loaded_from: Optional[Path] = None


def load_env_file(path: Path | str = ENV_FILE) -> dict[str, str]:
    """Copy non-empty values from the file into ``os.environ`` unless already set."""
    global _loaded_from
    path = Path(path)
    if os.environ.get("APP_SKIP_DOTENV") == "1" or not path.is_file():  # tests set APP_SKIP_DOTENV
        return {}
    _loaded_from = path
    applied = {}
    for key, value in dotenv_values(path).items():
        if value is None or value.strip() == "":
            continue
        if key in os.environ and os.environ[key] != "":
            _sources[key] = "shell (overrides .env)" if os.environ[key] != value else ".env"
            continue
        os.environ[key] = value
        _sources[key] = ".env"
        applied[key] = value
    return applied


def _mask(value: str) -> str:
    return value[:4] + "…" + value[-4:] if len(value) > 10 else "…"


def describe() -> dict:
    """What the backend sees, with secrets masked."""
    out = {}
    for name, (secret, help_text) in SETTINGS.items():
        value = os.environ.get(name) or None
        out[name] = {
            "set": value is not None,
            "value": (_mask(value) if secret else value) if value else None,
            "source": (_sources.get(name) or "shell") if value else "not set",
            "help": help_text,
        }
    return {"env_file": str(_loaded_from or ENV_FILE), "env_file_found": (_loaded_from or ENV_FILE).is_file(),
            "settings": out}
