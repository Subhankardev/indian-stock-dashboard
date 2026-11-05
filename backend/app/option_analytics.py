"""Option-chain analytics on a source-independent chain format.

Normalised chain::

    {"symbol", "expiry", "spot", "source", "expiries": [..],
     "rows": [{"strike": 2900.0,
               "CE": {"ltp", "close", "oi", "prev_oi", "volume", "iv", "delta", "bid", "ask", "key"} | None,
               "PE": {...} | None}, ...]}

Upstox (``upstox_data.option_chain``), NSE (``normalise_nse_chain``) and the
offline demo (``synthetic_chain``) all produce this shape.
"""
from __future__ import annotations

import math
from typing import Optional

from app.wave_oi.oi import classify_regime

# Price/OI quadrant in option-desk language.
BUILDUP = {
    "new_longs": "Long build-up",
    "short_covering": "Short covering",
    "new_shorts": "Short build-up",
    "long_liquidation": "Long unwinding",
    "neutral": "",
}


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _buildup(side: dict) -> str:
    ltp, close, oi, prev = side.get("ltp"), side.get("close"), side.get("oi"), side.get("prev_oi")
    if not (ltp and close and oi is not None and prev):
        return ""
    return BUILDUP[classify_regime(ltp / close - 1, oi / prev - 1, 0.01)]


def max_pain(rows: list[dict]) -> Optional[float]:
    """Expiry price at which option writers pay out the least."""
    strikes = [r["strike"] for r in rows]
    if not strikes:
        return None

    def payout(k: float) -> float:
        total = 0.0
        for r in rows:
            s = r["strike"]
            total += ((r.get("CE") or {}).get("oi") or 0) * max(0.0, k - s)
            total += ((r.get("PE") or {}).get("oi") or 0) * max(0.0, s - k)
        return total

    return min(strikes, key=payout)


def analyze_chain(chain: dict, window: int = 12) -> dict:
    rows = sorted((r for r in chain["rows"] if r.get("CE") or r.get("PE")), key=lambda r: r["strike"])
    spot = _num(chain.get("spot"))

    def side_val(r, side, field):
        return ((r.get(side) or {}).get(field)) or 0

    ce_oi = sum(side_val(r, "CE", "oi") for r in rows)
    pe_oi = sum(side_val(r, "PE", "oi") for r in rows)
    ce_vol = sum(side_val(r, "CE", "volume") for r in rows)
    pe_vol = sum(side_val(r, "PE", "volume") for r in rows)

    def best(side, cond=lambda r: True):
        cands = [r for r in rows if cond(r) and side_val(r, side, "oi")]
        return max(cands, key=lambda r: side_val(r, side, "oi"))["strike"] if cands else None

    atm = min(rows, key=lambda r: abs(r["strike"] - spot)) if rows and spot else None
    atm_iv = straddle = None
    if atm:
        ivs = [v for v in (side_val(atm, "CE", "iv"), side_val(atm, "PE", "iv")) if v]
        atm_iv = sum(ivs) / len(ivs) if ivs else None
        if side_val(atm, "CE", "ltp") and side_val(atm, "PE", "ltp"):
            straddle = side_val(atm, "CE", "ltp") + side_val(atm, "PE", "ltp")

    def oi_change(r, side):
        s = r.get(side) or {}
        return (s["oi"] - s["prev_oi"]) if s.get("oi") is not None and s.get("prev_oi") is not None else None

    def top_changes(side, n=3):
        ch = [(r["strike"], oi_change(r, side)) for r in rows if oi_change(r, side)]
        return [{"strike": k, "oi_change": c} for k, c in sorted(ch, key=lambda x: -abs(x[1]))[:n]]

    table_rows = rows
    if atm and window:
        i = rows.index(atm)
        table_rows = rows[max(0, i - window): i + window + 1]

    def side_out(r, side):
        s = r.get(side)
        if not s:
            return None
        return {**s, "oi_change": oi_change(r, side), "buildup": _buildup(s)}

    pcr = pe_oi / ce_oi if ce_oi else None
    max_ce, max_pe = best("CE"), best("PE")
    return {
        # --- original keys (kept for existing clients) -------------------
        "PCR": pcr,
        "max_CE_OI_strike": max_ce,
        "max_PE_OI_strike": max_pe,
        "total_CE_OI": ce_oi,
        "total_PE_OI": pe_oi,
        "OI_data": [(r["strike"], side_val(r, "CE", "oi"), side_val(r, "PE", "oi")) for r in rows],
        # --- new ---------------------------------------------------------
        "symbol": chain.get("symbol"),
        "source": chain.get("source"),
        "expiry": chain.get("expiry"),
        "expiries": chain.get("expiries", []),
        "spot": spot,
        "pcr_volume": pe_vol / ce_vol if ce_vol else None,
        "max_pain": max_pain(rows),
        # nearest walls on either side of spot: where writers are defending
        "resistance": best("CE", lambda r: spot is None or r["strike"] >= spot) or max_ce,
        "support": best("PE", lambda r: spot is None or r["strike"] <= spot) or max_pe,
        "atm_strike": atm["strike"] if atm else None,
        "atm_iv": atm_iv,
        "atm_straddle": straddle,
        "expected_move_pct": straddle / spot * 100 if straddle and spot else None,
        "top_oi_change": {"CE": top_changes("CE"), "PE": top_changes("PE")},
        "rows": [{"strike": r["strike"], "CE": side_out(r, "CE"), "PE": side_out(r, "PE")} for r in table_rows],
    }


# --------------------------------------------------------------------------- #
# NSE (unofficial) raw format
# --------------------------------------------------------------------------- #
def normalise_nse_chain(raw: dict, symbol: str, expiry: Optional[str] = None) -> dict:
    records = raw["records"]
    expiries = records.get("expiryDates") or []
    expiry = expiry or (expiries[0] if expiries else None)

    def side(d):
        if not d:
            return None
        oi = _num(d.get("openInterest"))
        chg = _num(d.get("changeinOpenInterest"))
        ltp, change = _num(d.get("lastPrice")), _num(d.get("change"))
        return {
            "ltp": ltp, "close": ltp - change if ltp is not None and change is not None else None,
            "oi": oi, "prev_oi": oi - chg if oi is not None and chg is not None else None,
            "volume": _num(d.get("totalTradedVolume")), "iv": _num(d.get("impliedVolatility")),
            "bid": _num(d.get("bidprice")), "ask": _num(d.get("askPrice")), "delta": None, "key": d.get("identifier"),
        }

    rows = [
        {"strike": float(r["strikePrice"]), "CE": side(r.get("CE")), "PE": side(r.get("PE"))}
        for r in records.get("data", []) if expiry is None or r.get("expiryDate") == expiry
    ]
    return {"symbol": symbol.upper(), "expiry": expiry, "expiries": expiries,
            "spot": _num(records.get("underlyingValue")), "source": "nse", "rows": rows}


def analyze_option_chain(chain: dict) -> dict:
    """Backwards-compatible entry point for the raw NSE payload."""
    return analyze_chain(normalise_nse_chain(chain, chain.get("symbol", "")), window=0)


# --------------------------------------------------------------------------- #
# Offline demo chain
# --------------------------------------------------------------------------- #
def _nice_step(x: float) -> float:
    """Round to 1, 2 or 5 x 10^k (strike spacing)."""
    mag = 10 ** math.floor(math.log10(x))
    return min((m * mag for m in (1, 2, 5, 10)), key=lambda v: abs(v - x))


def synthetic_chain(symbol: str = "DEMO", spot: float = 1000.0, n: int = 40, seed: int = 3) -> dict:
    import random

    rng = random.Random(seed)
    step = _nice_step(spot * 0.01)
    atm = round(spot / step) * step
    T, vol = 20 / 365, 0.22
    sd = spot * vol * math.sqrt(T)
    rows = []
    for i in range(-n // 2, n // 2 + 1):
        k = float(atm + i * step)
        tv = 0.4 * sd * math.exp(-((k - spot) / sd) ** 2 / 2)
        ce_ltp, pe_ltp = max(spot - k, 0) + tv, max(k - spot, 0) + tv
        # call writers stack OI above spot, put writers below, with thinner OI further out
        tail = 6_000 * math.exp(-abs(k - spot) / (1.5 * sd))
        ce_oi = (50_000 * math.exp(-((k - spot * 1.04) / (0.6 * sd)) ** 2) + tail) * rng.uniform(0.75, 1.25)
        pe_oi = (60_000 * math.exp(-((k - spot * 0.97) / (0.6 * sd)) ** 2) + tail) * rng.uniform(0.75, 1.25)

        def side(ltp, oi, kind):
            prev = oi / rng.uniform(0.85, 1.2)
            close = ltp / rng.uniform(0.9, 1.1)
            return {"ltp": round(ltp, 2), "close": round(close, 2), "oi": round(oi), "prev_oi": round(prev),
                    "volume": round(oi * rng.uniform(1, 4)), "iv": round(vol * 100 + abs(k - spot) / spot * 40, 2),
                    "delta": None, "bid": round(ltp * 0.995, 2), "ask": round(ltp * 1.005, 2),
                    "key": f"DEMO|{symbol}|{int(k)}{kind}"}

        rows.append({"strike": k, "CE": side(ce_ltp, ce_oi, "CE"), "PE": side(pe_ltp, pe_oi, "PE")})
    return {"symbol": symbol.upper(), "expiry": "demo", "expiries": ["demo"], "spot": spot,
            "source": "synthetic", "rows": rows}
