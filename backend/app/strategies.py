import pandas as pd

def analyze_stock(df: pd.DataFrame):
    df = df.copy()
    df['SMA_5'] = df['close'].rolling(window=5).mean()
    df['SMA_20'] = df['close'].rolling(window=20).mean()
    # RSI
    delta = df['close'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss
    df['RSI_14'] = 100 - (100 / (1 + rs))
    # MACD
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema12 - ema26
    df['MACD_signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    # Bollinger Bands
    df['BB_MID'] = df['close'].rolling(window=20).mean()
    df['BB_UPPER'] = df['BB_MID'] + 2 * df['close'].rolling(window=20).std()
    df['BB_LOWER'] = df['BB_MID'] - 2 * df['close'].rolling(window=20).std()

    latest = df.iloc[-1]
    prev = df.iloc[-2]
    buy_signal = (
        prev['SMA_5'] < prev['SMA_20'] and latest['SMA_5'] > latest['SMA_20'] and
        latest['RSI_14'] > 50 and latest['MACD'] > latest['MACD_signal'] and
        latest['close'] > latest['BB_MID']
    )
    sell_signal = (
        prev['SMA_5'] > prev['SMA_20'] and latest['SMA_5'] < latest['SMA_20'] and
        latest['RSI_14'] < 50 and latest['MACD'] < latest['MACD_signal'] and
        latest['close'] < latest['BB_MID']
    )
    if buy_signal:
        signal = "BUY"
        reason = "SMA crossover, RSI>50, MACD bullish, price above BB mid"
    elif sell_signal:
        signal = "SELL"
        reason = "SMA bearish crossover, RSI<50, MACD bearish, price below BB mid"
    else:
        signal = "HOLD"
        reason = "No strong signal"
    return {
        "signal": signal,
        "reason": reason,
        "indicators": {
            "SMA_5": float(latest['SMA_5']),
            "SMA_20": float(latest['SMA_20']),
            "RSI_14": float(latest['RSI_14']),
            "MACD": float(latest['MACD']),
            "MACD_signal": float(latest['MACD_signal']),
            "BB_MID": float(latest['BB_MID']),
            "BB_UPPER": float(latest['BB_UPPER']),
            "BB_LOWER": float(latest['BB_LOWER']),
        },
        "latest_close": float(latest['close'])
    }