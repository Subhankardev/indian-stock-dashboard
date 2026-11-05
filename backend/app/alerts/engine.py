"""BUY/SELL alerts from the Wave · GTI · OI strategy.

The scanner re-runs ``wave_oi.analyze`` for every symbol on the watchlist. The
last daily bar is today's *forming* bar while the market is open, so:

* ``forming``   - during market hours the signal would fire if the day closed now
* ``faded``     - a forming signal disappeared again (intraday, or it did not hold into the close)
* ``confirmed`` - the signal is on a completed daily bar (after 15:30 IST)

Each confirmed signal is alerted once (deduplicated by symbol, bar date and side).
The watchlist, per-symbol state and the latest alerts are persisted to a JSON
file so a restart doesn't re-alert or forget the watchlist.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from app.wave_oi import StrategyConfig, analyze

IST = timezone(timedelta(hours=5, minutes=30))
MARKET_OPEN, MARKET_CLOSE = time(9, 15), time(15, 30)
MAX_ALERTS = 500
MAX_WATCHLIST = 25
SYMBOL_RE = re.compile(r"^[A-Z0-9&\-]{1,20}$")


def market_phase(now: datetime) -> str:
    """'open' during the NSE session (Mon-Fri 09:15-15:30 IST), otherwise 'closed'.
    Exchange holidays read as 'open' by the clock; no bar for today exists then,
    so nothing is treated as forming."""
    ist = now.astimezone(IST)
    if ist.weekday() < 5 and MARKET_OPEN <= ist.time() < MARKET_CLOSE:
        return "open"
    return "closed"


class AlertStore:
    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.data = {"watchlist": [], "alerts": [], "state": {}, "next_id": 1}
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text()))
            except (OSError, ValueError):
                pass  # corrupt file: start fresh rather than crash the API

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, default=str))
        os.replace(tmp, self.path)

    # -- watchlist ----------------------------------------------------------- #
    @property
    def watchlist(self) -> list[str]:
        return list(self.data["watchlist"])

    def set_watchlist(self, symbols: list[str]) -> list[str]:
        clean = list(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
        bad = [s for s in clean if not SYMBOL_RE.match(s)]
        if bad:
            raise ValueError(f"Invalid symbol(s): {', '.join(bad)}")
        if len(clean) > MAX_WATCHLIST:
            raise ValueError(f"At most {MAX_WATCHLIST} symbols")
        with self._lock:
            self.data["watchlist"] = clean
            self.data["state"] = {k: v for k, v in self.data["state"].items() if k in clean}
            self._save()
        return clean

    # -- alerts -------------------------------------------------------------- #
    def emit(self, alert: dict) -> dict:
        with self._lock:
            alert = {"id": self.data["next_id"], "ts": datetime.now(timezone.utc).isoformat(), **alert}
            self.data["next_id"] += 1
            self.data["alerts"] = (self.data["alerts"] + [alert])[-MAX_ALERTS:]
            self._save()
        return alert

    def since(self, last_id: int = 0, limit: int = 50) -> list[dict]:
        newer = [a for a in self.data["alerts"] if a["id"] > last_id]
        return list(reversed(newer[-limit:]))

    def has(self, symbol: str, bar_date: str, side: str, kind: str) -> bool:
        return any(a["symbol"] == symbol and a.get("bar_date") == bar_date and a["side"] == side
                   and a["kind"] == kind for a in self.data["alerts"])

    def get_state(self, symbol: str) -> dict:
        return dict(self.data["state"].get(symbol, {}))

    def put_state(self, symbol: str, state: dict) -> None:
        with self._lock:
            self.data["state"][symbol] = state
            self._save()


def _num(v) -> Optional[float]:
    return None if v is None or pd.isna(v) else float(v)


class AlertScanner:
    def __init__(
        self,
        store: AlertStore,
        load: Callable[[str], tuple[pd.DataFrame, Optional[str]]],
        config: Optional[StrategyConfig] = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        open_interval: float = 60.0,
        closed_interval: float = 600.0,
    ):
        self.store, self.load, self.config, self.now = store, load, config or StrategyConfig(), now
        self.open_interval, self.closed_interval = open_interval, closed_interval
        self.status = {"last_run": None, "phase": None, "errors": {}, "next_run": None}

    def _alert(self, symbol, kind, side, row, bar_date, reason=None):
        return self.store.emit({
            "symbol": symbol, "kind": kind, "side": "BUY" if side == 1 else "SELL",
            "bar_date": bar_date, "price": _num(row["close"]),
            "stop": _num(row["stop"]) if kind != "faded" else None,
            "target": _num(row["target"]) if kind != "faded" else None,
            "wave": row.get("wave_label") or None,
            "reason": reason or row.get("reason") or "",
        })

    def scan_symbol(self, symbol: str) -> list[dict]:
        df, _warning = self.load(symbol)
        an, _ = analyze(df, self.config)
        row = an.iloc[-1]
        bar_date = an.index[-1].date()
        now = self.now()
        forming = market_phase(now) == "open" and bar_date == now.astimezone(IST).date()
        sig = int(row["signal"])
        bd = bar_date.isoformat()

        st = self.store.get_state(symbol)
        if st.get("bar_date") != bd:
            st = {"bar_date": bd, "active": 0}
        out = []
        if forming:
            if sig and st["active"] != sig:
                if st["active"]:
                    out.append(self._alert(symbol, "faded", st["active"], row, bd, "Opposite signal now forming"))
                out.append(self._alert(symbol, "forming", sig, row, bd))
            elif not sig and st["active"]:
                out.append(self._alert(symbol, "faded", st["active"], row, bd, "Signal conditions no longer met"))
            st["active"] = sig
        else:  # the last bar is complete
            if st["active"] and st["active"] != sig:
                out.append(self._alert(symbol, "faded", st["active"], row, bd, "Did not hold into the close"))
            side = "BUY" if sig == 1 else "SELL"
            if sig and not self.store.has(symbol, bd, side, "confirmed"):
                out.append(self._alert(symbol, "confirmed", sig, row, bd))
            st["active"] = sig
        self.store.put_state(symbol, st)
        return out

    def scan_all(self) -> list[dict]:
        out, errors = [], {}
        for symbol in self.store.watchlist:
            try:
                out += self.scan_symbol(symbol)
            except Exception as exc:  # one bad symbol must not stop the others
                errors[symbol] = f"{type(exc).__name__}: {exc}"
        now = self.now()
        self.status.update(last_run=now.isoformat(), phase=market_phase(now), errors=errors)
        return out

    def interval(self) -> float:
        return self.open_interval if market_phase(self.now()) == "open" else self.closed_interval

    async def run_forever(self) -> None:
        while True:
            await asyncio.to_thread(self.scan_all)
            wait = self.interval()
            self.status["next_run"] = (self.now() + timedelta(seconds=wait)).isoformat()
            await asyncio.sleep(wait)
