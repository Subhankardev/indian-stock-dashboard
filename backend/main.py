from fastapi import FastAPI
from nse_data import get_stock_data
from strategies import sma_crossover_signal

app = FastAPI()

@app.get("/api/signal/{symbol}")
def get_signal(symbol: str):
    data = get_stock_data(symbol)
    signal, details = sma_crossover_signal(data)
    return {"symbol": symbol, "signal": signal, "details": details}