"""Per-browser live session: map instrument ticks back to symbols and add a live
price/OI regime (today vs. the previous close / previous-day OI)."""
from __future__ import annotations

from typing import Optional

from app.wave_oi.oi import classify_regime

LIVE_MIN_OI_CHANGE = 0.005  # 0.5% intraday OI move counts as a build-up / unwind


def mock_legs(symbol: str, prev_oi: float, base: float | None = None) -> dict:
    s = symbol.upper()
    suffix = f"|{base:g}" if base else ""
    return {"symbol": s, "EQ": f"MOCK_EQ|{s}{suffix}", "FUT": f"MOCK_FUT|{s}{suffix}",
            "fut_symbol": f"{s} FUT (mock)", "prev_oi": prev_oi}


class LiveSession:
    def __init__(self, legs: list[dict]):
        self.legs = {l["symbol"]: l for l in legs}
        self.by_key: dict[str, tuple[str, str]] = {}
        for l in legs:
            for leg in ("EQ", "FUT"):
                if l.get(leg):
                    self.by_key[l[leg]] = (l["symbol"], leg)
        self.state: dict[str, dict[str, dict]] = {s: {} for s in self.legs}

    @property
    def keys(self) -> list[str]:
        return list(self.by_key)

    def summary(self, symbol: str) -> dict:
        st, legs = self.state[symbol], self.legs[symbol]
        px = st.get("EQ") or st.get("FUT") or {}
        fut = st.get("FUT") or {}
        price_chg = px.get("change_pct")
        oi, prev_oi = fut.get("oi"), legs.get("prev_oi")
        oi_chg: Optional[float] = (oi / prev_oi - 1) * 100 if oi and prev_oi else None
        return {
            "ltp": px.get("ltp"),
            "change_pct": price_chg,
            "oi": oi,
            "oi_change_pct": oi_chg,
            "regime": classify_regime(
                None if price_chg is None else price_chg / 100,
                None if oi_chg is None else oi_chg / 100,
                LIVE_MIN_OI_CHANGE,
            ) if oi_chg is not None else "n/a",
        }

    def on_tick(self, tick: dict) -> Optional[dict]:
        hit = self.by_key.get(tick["key"])
        if hit is None:
            return None
        symbol, leg = hit
        self.state[symbol][leg] = tick
        return {"type": "tick", "symbol": symbol, "leg": leg, "tick": tick, "live": self.summary(symbol)}
