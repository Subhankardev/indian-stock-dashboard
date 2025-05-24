import pandas as pd

def sma_crossover_signal(df: pd.DataFrame, short_period=5, long_period=20):
    """
    Simple Moving Average Crossover
    Returns 'BUY', 'SELL', or 'HOLD'
    """
    df = df.copy()
    df['SMA_SHORT'] = df['close'].rolling(window=short_period).mean()
    df['SMA_LONG'] = df['close'].rolling(window=long_period).mean()
    if df['SMA_SHORT'].iloc[-2] < df['SMA_LONG'].iloc[-2] and df['SMA_SHORT'].iloc[-1] > df['SMA_LONG'].iloc[-1]:
        return 'BUY', 'Short SMA crossed above Long SMA'
    elif df['SMA_SHORT'].iloc[-2] > df['SMA_LONG'].iloc[-2] and df['SMA_SHORT'].iloc[-1] < df['SMA_LONG'].iloc[-1]:
        return 'SELL', 'Short SMA crossed below Long SMA'
    else:
        return 'HOLD', 'No crossover'