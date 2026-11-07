"""Option ideas for option *buyers*, derived from an underlying signal.

Given a direction (BUY -> call, SELL -> put), the underlying's target and stop,
and a live option chain, this picks a contract and translates the underlying's
levels into premium levels:

* **Expiry** - the nearest expiry with at least ``min_trading_days`` left, so
  time decay doesn't eat the move.
* **Strike** - the liquid strike whose delta is closest to ``target_delta``
  (0.60: at-the-money to slightly in-the-money), plus a cheaper "aggressive"
  strike (~0.40 delta) and a safer "conservative" one (~0.75 delta).
* **Premium target / stop** - Black-Scholes re-pricing of the option at the
  underlying's target and stop, a couple of trading days later, using the
  option's own implied volatility. These are estimates, not quotes.
* **Warnings** - expensive options (IV well above realised volatility), an OI
  wall between price and target, heavy theta, wide spread, thin liquidity.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

RISK_FREE = 0.065          # annual, roughly the Indian T-bill yield
HOLD_DAYS = 2              # trading days assumed to reach target/stop (for theta)
TRADING_DAYS = 252


# --------------------------------------------------------------------------- #
# Black-Scholes
# --------------------------------------------------------------------------- #
def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(spot: float, strike: float, years: float, vol: float, kind: str, r: float = RISK_FREE) -> float:
    """European option price; ``vol`` as a fraction (0.15 = 15%)."""
    intrinsic = max(0.0, spot - strike) if kind == "CE" else max(0.0, strike - spot)
    if years <= 0 or vol <= 0:
        return intrinsic
    sq = vol * math.sqrt(years)
    d1 = (math.log(spot / strike) + (r + 0.5 * vol * vol) * years) / sq
    d2 = d1 - sq
    if kind == "CE":
        return spot * _ncdf(d1) - strike * math.exp(-r * years) * _ncdf(d2)
    return strike * math.exp(-r * years) * _ncdf(-d2) - spot * _ncdf(-d1)


def bs_delta(spot: float, strike: float, years: float, vol: float, kind: str, r: float = RISK_FREE) -> float:
    if years <= 0 or vol <= 0:
        itm = spot > strike if kind == "CE" else spot < strike
        return (1.0 if itm else 0.0) * (1 if kind == "CE" else -1)
    d1 = (math.log(spot / strike) + (r + 0.5 * vol * vol) * years) / (vol * math.sqrt(years))
    return _ncdf(d1) if kind == "CE" else _ncdf(d1) - 1.0


def implied_vol(price: float, spot: float, strike: float, years: float, kind: str) -> Optional[float]:
    """Bisection IV solve (fraction) - used when the chain doesn't carry IV."""
    if price <= 0 or years <= 0:
        return None
    lo, hi = 0.01, 3.0
    if not (bs_price(spot, strike, years, lo, kind) <= price <= bs_price(spot, strike, years, hi, kind)):
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if bs_price(spot, strike, years, mid, kind) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def trading_days_between(start: date, end: date) -> int:
    return int(np.busday_count(start, end))


def pick_expiry(expiries: list[str], today: date, min_trading_days: int = 4) -> Optional[str]:
    """Nearest expiry (YYYY-MM-DD) with at least ``min_trading_days`` trading days to go."""
    future = sorted(e for e in expiries if _is_date(e) and date.fromisoformat(e) >= today)
    for e in future:
        if trading_days_between(today, date.fromisoformat(e)) >= min_trading_days:
            return e
    return future[-1] if future else None


def _is_date(s) -> bool:
    try:
        date.fromisoformat(str(s))
        return True
    except ValueError:
        return False


def realized_vol(closes: pd.Series, window: int = 20) -> Optional[float]:
    """Annualised close-to-close volatility (fraction) over the last ``window`` days."""
    r = np.log(closes.astype(float)).diff().dropna().tail(window)
    if len(r) < max(5, window // 2):
        return None
    return float(r.std(ddof=1) * math.sqrt(TRADING_DAYS))


def _years(today: date, expiry: str, extra_trading_days: int = 0) -> float:
    days = trading_days_between(today, date.fromisoformat(expiry)) - extra_trading_days
    return max(days, 0) / TRADING_DAYS + 0.25 / TRADING_DAYS  # expiry-day session


# --------------------------------------------------------------------------- #
# The suggestion
# --------------------------------------------------------------------------- #
def suggest(
    chain: dict,
    side: str,
    target: float,
    stop: float,
    today: Optional[date] = None,
    closes: Optional[pd.Series] = None,
    lot_size: Optional[int] = None,
    target_delta: float = 0.60,
) -> dict:
    """``chain`` is a normalised chain (see option_analytics) for ONE expiry."""
    today = today or date.today()
    side = side.upper()
    if side not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL")
    kind = "CE" if side == "BUY" else "PE"
    spot = float(chain["spot"])
    expiry = chain["expiry"]
    if not _is_date(expiry):
        raise ValueError(f"Unknown expiry {expiry!r}")
    years_now = _years(today, expiry)
    years_later = _years(today, expiry, HOLD_DAYS)

    rows = []
    for r in chain["rows"]:
        o = r.get(kind)
        if not o:
            continue
        price = o.get("ask") or o.get("ltp")
        if not price or price <= 0:
            continue
        iv = (o.get("iv") or 0) / 100 or implied_vol(o.get("ltp") or price, spot, r["strike"], years_now, kind)
        if not iv:
            continue
        delta = o.get("delta")
        if delta is None or delta == 0:
            delta = bs_delta(spot, r["strike"], years_now, iv, kind)
        bid, ask = o.get("bid"), o.get("ask")
        mid = (bid + ask) / 2 if bid and ask else price
        spread = (ask - bid) / mid if bid and ask and mid else None
        rows.append({"strike": r["strike"], "opt": o, "price": float(price), "iv": iv,
                     "delta": abs(float(delta)), "spread": spread})
    if not rows:
        raise ValueError(f"No priced {kind} contracts in the chain")

    # Only contracts a buyer would actually trade: not lottery tickets, not deep in-the-money.
    tradable = [x for x in rows if 0.15 <= x["delta"] <= 0.90] or rows
    max_oi = max((x["opt"].get("oi") or 0) for x in tradable) or 1
    liquid = [x for x in tradable if (x["opt"].get("oi") or 0) >= 0.02 * max_oi
              and x["spread"] is not None and x["spread"] <= 0.05]
    pool = liquid or tradable

    def nearest(d):
        return min(pool, key=lambda x: abs(x["delta"] - d))

    def describe(x, role):
        k = x["strike"]
        entry = x["price"]
        at_target = bs_price(target, k, years_later, x["iv"], kind)
        at_stop = bs_price(stop, k, years_later, x["iv"], kind)
        risk = entry - at_stop
        reward = at_target - entry
        o = x["opt"]
        out = {
            "role": role, "type": kind, "strike": k, "expiry": expiry, "key": o.get("key"),
            "premium": round(entry, 2), "ltp": o.get("ltp"), "bid": o.get("bid"), "ask": o.get("ask"),
            "delta": round(x["delta"], 2), "iv_pct": round(x["iv"] * 100, 1), "oi": o.get("oi"),
            "spread_pct": round(x["spread"] * 100, 1) if x["spread"] is not None else None,
            "target_premium": round(at_target, 2), "stop_premium": round(max(at_stop, 0.05), 2),
            "reward_risk": round(reward / risk, 2) if risk > 0 else None,
            "theta_pct_per_day": (round(abs(o["theta"]) / entry * 100, 1) if o.get("theta") else None),
        }
        if lot_size:
            out.update(lot_size=lot_size, cost_per_lot=round(entry * lot_size, 2),
                       max_loss_per_lot=round(max(risk, 0) * lot_size, 2),
                       gain_at_target_per_lot=round(max(reward, 0) * lot_size, 2))
        return out

    primary = nearest(target_delta)
    ideas = [describe(primary, "recommended")]
    for d, role in ((0.40, "aggressive (cheaper, needs a bigger move)"), (0.75, "conservative (in-the-money)")):
        alt = nearest(d)
        if alt["strike"] != primary["strike"] and all(alt["strike"] != i["strike"] for i in ideas):
            ideas.append(describe(alt, role))

    # ---- warnings -------------------------------------------------------- #
    warnings = []
    main = ideas[0]
    hv = realized_vol(closes) if closes is not None else None
    if hv and main["iv_pct"] / 100 > 1.3 * hv:
        warnings.append(f"Options look expensive: implied volatility {main['iv_pct']:.0f}% vs "
                        f"{hv * 100:.0f}% realised over 20 days. A move can be eaten by IV falling.")
    walls = [(r["strike"], (r.get(kind) or {}).get("oi") or 0) for r in chain["rows"]]
    between = [(k, oi) for k, oi in walls
               if (spot < k <= target if kind == "CE" else target <= k < spot)]
    if between:
        k, oi = max(between, key=lambda w: w[1])
        biggest = max(oi for _, oi in walls) or 1
        typical = float(np.median([o for _, o in walls if o])) if any(o for _, o in walls) else 0.0
        if oi >= 0.6 * biggest and oi >= 2 * typical:  # a wall must stand out from its neighbours
            who = "Call" if kind == "CE" else "Put"
            warnings.append(f"{who} writers hold a large OI wall at {k:g}, between price and target - "
                            "they tend to defend it; consider booking profit near it.")
    if main.get("theta_pct_per_day") and main["theta_pct_per_day"] >= 4:
        warnings.append(f"Heavy time decay: about {main['theta_pct_per_day']}% of the premium per day.")
    if main.get("spread_pct") and main["spread_pct"] > 3:
        warnings.append(f"Wide bid-ask spread ({main['spread_pct']}%) - use a limit order.")
    if not liquid:
        warnings.append("Thin liquidity across strikes - fills may be poor.")
    if main["reward_risk"] is not None and main["reward_risk"] < 1:
        warnings.append("In premium terms the reward is smaller than the risk at these levels.")
    dte = trading_days_between(today, date.fromisoformat(expiry))

    return {
        "side": side, "type": kind, "spot": spot, "expiry": expiry, "trading_days_to_expiry": dte,
        "underlying_target": target, "underlying_stop": stop,
        "ideas": ideas, "warnings": warnings,
        "realized_vol_pct": round(hv * 100, 1) if hv else None,
        "assumptions": (f"Premium target/stop are Black-Scholes estimates at the underlying's target/stop "
                        f"{HOLD_DAYS} trading days from now with today's IV - not quotes. Entry is the ask."),
    }


# --------------------------------------------------------------------------- #
# Fetching what the suggestion needs
# --------------------------------------------------------------------------- #
def suggest_for_symbol(symbol: str, side: str, target: float, stop: float,
                       source: str = "upstox", spot: Optional[float] = None,
                       today: Optional[date] = None) -> dict:
    """Pick the expiry, fetch its chain, lot size and recent closes, then ``suggest``."""
    today = today or date.today()
    if source == "synthetic":
        from datetime import timedelta
        from app.option_analytics import synthetic_chain
        from app.wave_oi import make_synthetic
        chain = synthetic_chain(symbol, spot or 1000.0)
        chain["expiry"] = (pd.Timestamp(today) + pd.offsets.BDay(6)).date().isoformat()
        closes = make_synthetic(120)["close"] * ((spot or 1000.0) / make_synthetic(120)["close"].iloc[-1])
        out = suggest(chain, side, target, stop, today=today, closes=closes, lot_size=75)
        out["source"] = "synthetic"
        return out

    from app import upstox_data
    client = upstox_data.client()
    _, expiries = client.option_meta(symbol)
    expiry = pick_expiry(expiries, today)
    if expiry is None:
        raise ValueError(f"No upcoming option expiries for {symbol}")
    chain = client.option_chain(symbol, expiry)
    lot = None
    for r in chain["rows"]:
        key = (r.get("CE") or r.get("PE") or {}).get("key")
        if key:
            info = upstox_data.instrument_by_key(key) or {}
            lot = info.get("lot_size")
            break
    try:
        closes = upstox_data.get_daily_prices(symbol, 60)["close"]
    except Exception:
        closes = None
    out = suggest(chain, side, target, stop, today=today, closes=closes, lot_size=lot)
    out["source"] = "upstox"
    return out
