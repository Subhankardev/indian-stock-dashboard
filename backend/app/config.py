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
import re
import unicodedata
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
_cleaned: dict[str, int] = {}

# Values that must be a single unbroken token: copy/paste junk is stripped from these.
TOKEN_SETTINGS = ("UPSTOX_ACCESS_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
_QUOTES = "\"'\u2018\u2019\u201c\u201d`"


def clean_token(value: Optional[str]) -> Optional[str]:
    """Remove what copy/paste adds to long tokens: invisible format characters
    (zero-width spaces, soft hyphens, BOM, ...), whitespace/line breaks and
    surrounding quotes (straight or curly). Returns None for empty input."""
    if value is None:
        return None
    kept = "".join(ch for ch in value if not unicodedata.category(ch).startswith(("C", "Z")))
    kept = kept.strip(_QUOTES)
    return kept or None


def token_problem(value: Optional[str]) -> Optional[str]:
    if value and not value.isascii():
        bad = sorted({f"U+{ord(c):04X}" for c in value if not c.isascii()})[:5]
        return ("contains non-ASCII characters (" + ", ".join(bad) + "); "
                "re-copy it from the source and type it in without formatting")
    return None


def sanitize_environ() -> None:
    """Clean token settings in os.environ, whichever way they were set."""
    for key in TOKEN_SETTINGS:
        raw = os.environ.get(key)
        if raw is None:
            continue
        cleaned = clean_token(raw)
        if cleaned != raw:
            _cleaned[key] = len(raw) - len(cleaned or "")
            if cleaned:
                os.environ[key] = cleaned
            else:
                del os.environ[key]


def load_env_file(path: Path | str = ENV_FILE) -> dict[str, str]:
    """Copy non-empty values from the file into ``os.environ`` unless already set."""
    global _loaded_from
    path = Path(path)
    if os.environ.get("APP_SKIP_DOTENV") == "1" or not path.is_file():  # tests set APP_SKIP_DOTENV
        return {}
    _loaded_from = path
    applied = {}
    values = dict(dotenv_values(path))
    # python-dotenv silently drops lines it can't parse, e.g. an unbalanced quote
    # (KEY='abc). For the settings we know, fall back to the raw text after "=".
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=(.*)$", line)
        if m and m.group(1) in SETTINGS and m.group(1) not in values:
            raw = m.group(2).split(" #")[0].strip()
            values[m.group(1)] = raw.strip(_QUOTES) if m.group(1) not in TOKEN_SETTINGS else raw
    for key, value in values.items():
        if value is None or value.strip() == "":
            continue
        if key in os.environ and os.environ[key] != "":
            _sources[key] = "shell (overrides .env)" if os.environ[key] != value else ".env"
            continue
        os.environ[key] = value
        _sources[key] = ".env"
        applied[key] = value
    return applied


def load_settings(path: Path | str = ENV_FILE) -> None:
    """Startup entry point: read .env, then clean the token settings."""
    load_env_file(path)
    sanitize_environ()


def _mask(value: str) -> str:
    return value[:4] + "…" + value[-4:] if len(value) > 10 else "…"


def describe() -> dict:
    """What the backend sees, with secrets masked."""
    out = {}
    for name, (secret, help_text) in SETTINGS.items():
        value = os.environ.get(name) or None
        note = None
        if name in _cleaned:
            note = f"removed {_cleaned[name]} hidden/extra characters (copy-paste) - fixed automatically"
        problem = token_problem(value) if name in TOKEN_SETTINGS else None
        out[name] = {
            "set": value is not None,
            "value": (_mask(value) if secret else value) if value else None,
            "source": (_sources.get(name) or "shell") if value else "not set",
            "help": help_text,
            **({"note": note} if note else {}),
            **({"problem": problem} if problem else {}),
        }
    return {"env_file": str(_loaded_from or ENV_FILE), "env_file_found": (_loaded_from or ENV_FILE).is_file(),
            "settings": out}
