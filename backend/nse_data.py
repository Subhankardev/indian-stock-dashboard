import pandas as pd
import requests

def get_stock_data(symbol: str):
    """
    Fetch historical OHLCV data from NSE for the given symbol.

    Uses unofficial NSE endpoint.
    """
    url = f"https://www.nseindia.com/api/quote-equity?symbol={symbol.upper()}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    session = requests.Session()
    session.get('https://www.nseindia.com', headers=headers)
    resp = session.get(url, headers=headers)
    data = resp.json()

    # Parse last 60 days OHLC
    ohlc = data['priceInfo']['history']
    df = pd.DataFrame(ohlc)
    df['date'] = pd.to_datetime(df['date'])
    return df