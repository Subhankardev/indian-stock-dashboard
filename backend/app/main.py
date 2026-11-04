import os
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from app import nse_data, upstox_data
from app.nse_data import get_stock_data, get_option_chain
from app.strategies import analyze_stock
from app.option_analytics import analyze_option_chain
from app.wave_oi import BacktestConfig, StrategyConfig, build_report, make_synthetic
from app.live import routes as live_routes

app = FastAPI(title="Indian Stock Market Analytics API", lifespan=live_routes.lifespan)
app.include_router(live_routes.router)

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
    df = load_daily_prices(symbol)
    analysis = analyze_stock(df)
    return analysis

@app.get("/api/options/{symbol}")
def option_analysis(symbol: str):
    chain = get_option_chain(symbol)
    analysis = analyze_option_chain(chain)
    return analysis

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
    use_oi: bool = True,
    allow_short: bool = True,
    min_rr: float = Query(1.5, ge=0.0, le=10.0),
    zz_atr_mult: float = Query(2.5, gt=0.0, le=10.0, description="Swing size in ATRs"),
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

    cfg = StrategyConfig(
        use_wave=use_wave, use_oi=use_oi, allow_short=allow_short,
        min_rr=min_rr, zz_atr_mult=zz_atr_mult,
    )
    bt_cfg = BacktestConfig(risk_per_trade=risk_per_trade, fee_bps=fee_bps)
    report = build_report(df, symbol, cfg, bt_cfg, source=source)
    report["warning"] = warning
    return report