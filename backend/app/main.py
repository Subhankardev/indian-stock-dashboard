from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.nse_data import get_stock_data, get_option_chain
from app.strategies import analyze_stock
from app.option_analytics import analyze_option_chain

app = FastAPI(title="Indian Stock Market Analytics API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/stock/{symbol}")
def stock_analysis(symbol: str):
    df = get_stock_data(symbol)
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
            df = get_stock_data(symbol)
            analysis = analyze_stock(df)
            if analysis["signal"] != "HOLD":
                results.append({"symbol": symbol, **analysis})
            if len(results) >= limit:
                break
        except Exception:
            continue
    return results