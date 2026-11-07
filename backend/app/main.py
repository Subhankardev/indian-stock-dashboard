# Load backend/.env before anything reads its settings.
from app.config import describe as describe_config, load_settings

load_settings()

import os
from datetime import date
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from app import nse_data, upstox_data
from app.nse_data import get_stock_data, get_option_chain
from app.strategies import analyze_stock
from app.option_analytics import analyze_chain, normalise_nse_chain, synthetic_chain
from app.wave_oi import BacktestConfig, StrategyConfig, build_report, make_synthetic
from app.wave_oi.tuning import load_rules, split_params
from contextlib import asynccontextmanager

from app.live import routes as live_routes
from app.alerts import routes as alert_routes
from app import upstox_auth


@asynccontextmanager
async def lifespan(app):
    async with live_routes.lifespan(app), alert_routes.lifespan(app):
        yield


app = FastAPI(title="Indian Stock Market Analytics API", lifespan=lifespan)
app.include_router(live_routes.router)
app.include_router(alert_routes.router)
app.include_router(upstox_auth.router)

# "upstox" (default) or "nse": where price / OI history comes from.
DATA_SOURCE = os.getenv("DATA_SOURCE", "upstox").lower()


def load_daily_prices(symbol: str):
    if DATA_SOURCE == "upstox":
        return upstox_data.get_daily_prices(symbol)
    return get_stock_data(symbol)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/stock/{symbol}")
def stock_analysis(symbol: str):
    # Unhandled errors become a bare 500 without CORS headers, which browsers report as a CORS failure.
    try:
        df = load_daily_prices(symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch prices for {symbol}: {exc}")
    return analyze_stock(df)

@app.get("/api/options/{symbol}")
def option_analysis(
    symbol: str,
    expiry: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD; default nearest"),
    source: Literal["default", "upstox", "nse", "synthetic"] = "default",
    window: int = Query(12, ge=0, le=100, description="Strikes either side of ATM in `rows` (0 = all)"),
    spot: float = Query(1000.0, gt=0, description="Spot price for source=synthetic"),
):
    """Option chain analytics: PCR, max pain, OI walls, ATM IV / straddle, OI build-ups."""
    if source == "default":
        source = DATA_SOURCE
    try:
        if source == "synthetic":
            chain = synthetic_chain(symbol, spot)
        elif source == "upstox":
            chain = upstox_data.client().option_chain(symbol, expiry)
        else:
            nse_expiry = date.fromisoformat(expiry).strftime("%d-%b-%Y") if expiry else None
            chain = normalise_nse_chain(get_option_chain(symbol), symbol, nse_expiry)
    except upstox_data.UpstoxError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch {source} option chain for {symbol}: {exc}")
    if not chain["rows"]:
        raise HTTPException(status_code=404, detail=f"Empty option chain for {symbol} {chain.get('expiry')}")
    return analyze_chain(chain, window=window)

@app.get("/api/trending")
def trending_stocks(limit: int = 10):
    from app.nse_data import NIFTY_50
    results = []
    for symbol in NIFTY_50:
        try:
            df = load_daily_prices(symbol)
            analysis = analyze_stock(df)
            if analysis["signal"] != "HOLD":
                results.append({"symbol": symbol, **analysis})
            if len(results) >= limit:
                break
        except Exception:
            continue
    return results


@app.get("/api/wave-oi/{symbol}")
def wave_oi_analysis(
    symbol: str,
    days: int = Query(730, ge=120, le=3650, description="Calendar days of daily history"),
    source: Literal["default", "upstox", "nse", "synthetic"] = Query(
        "default", description="'default' follows DATA_SOURCE; 'synthetic' = offline demo data"),
    use_wave: bool = True,
    use_oi: Optional[bool] = Query(None, description="default: active rules (tuned or built-in)"),
    allow_short: bool = True,
    min_rr: Optional[float] = Query(None, ge=0.0, le=10.0, description="default: active rules"),
    zz_atr_mult: Optional[float] = Query(None, gt=0.0, le=10.0, description="Swing size in ATRs; default: active rules"),
    risk_per_trade: float = Query(0.01, gt=0.0, le=0.1),
    fee_bps: float = Query(5.0, ge=0.0, le=100.0),
):
    """Elliott wave structure + Ghost Trade Indicator + open-interest confirmation,
    with the latest signal and a backtest of the same rules."""
    warning = None
    if source == "default":
        source = DATA_SOURCE
    if source == "synthetic":
        df = make_synthetic(bars=int(days * 250 / 365))
    else:
        provider = upstox_data if source == "upstox" else nse_data
        try:
            df, warning = provider.get_price_oi_history(symbol, days)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Could not fetch {source} data for {symbol}: {exc}")
    if len(df) < 60:
        raise HTTPException(status_code=422, detail=f"Need at least 60 daily bars, got {len(df)}")

    rules = load_rules()  # backend/strategy.json from `python -m app.tune --apply`, else defaults
    params = dict(rules["params"])
    for name, value in (("use_oi", use_oi), ("min_rr", min_rr), ("zz_atr_mult", zz_atr_mult)):
        if value is not None:
            params[name] = value
    cfg, bt_cfg = split_params({**params, "use_wave": use_wave, "allow_short": allow_short})
    bt_cfg.risk_per_trade, bt_cfg.fee_bps = risk_per_trade, fee_bps
    report = build_report(df, symbol, cfg, bt_cfg, source=source)
    report["rules"] = {"source": rules["source"], "tuned_at": rules["tuned_at"], "params": params,
                       "summary": rules["summary"]}
    report["warning"] = warning
    return report


@app.get("/api/config")
def config_status():
    """Which settings the backend sees and where each came from (secrets masked)."""
    return describe_config()
