import requests
import pandas as pd

NIFTY_50 = [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "HINDUNILVR",
    "SBIN", "KOTAKBANK", "ITC", "BHARTIARTL", "LT", "AXISBANK", "ASIANPAINT",
    "BAJFINANCE", "HCLTECH", "MARUTI", "SUNPHARMA", "TITAN", "ULTRACEMCO", "WIPRO"
]

HEADERS = {'User-Agent': 'Mozilla/5.0'}

def get_stock_data(symbol: str):
    url = f"https://www.nseindia.com/api/quote-equity?symbol={symbol.upper()}"
    session = requests.Session()
    session.get('https://www.nseindia.com', headers=HEADERS)
    resp = session.get(url, headers=HEADERS)
    data = resp.json()["priceInfo"]["history"]
    df = pd.DataFrame(data)
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    for col in ["open", "dayHigh", "dayLow", "lastPrice", "totalTradedVolume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.rename(columns={
        "dayHigh": "high",
        "dayLow": "low",
        "lastPrice": "close",
        "totalTradedVolume": "volume"
    }, inplace=True)
    return df[["open", "high", "low", "close", "volume"]]

def get_option_chain(symbol: str):
    url = f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol.upper()}"
    session = requests.Session()
    session.get('https://www.nseindia.com', headers=HEADERS)
    resp = session.get(url, headers=HEADERS)
    data = resp.json()
    return data