# Indian Stock Market Analytics Dashboard

A full-stack web dashboard for real-time Indian stock market analytics, including buy/sell signals, technical analysis, and NSE option data.

## Features

- **Trending Stock Screener**: See which stocks are trending with signals (BUY/SELL/HOLD).
- **Technical Indicators**: SMA, RSI, MACD, Bollinger Bands.
- **Option Chain Analytics**: PCR, OI-based support/resistance.
- **Wave · GTI · OI Analysis**: Elliott wave structure, Ghost Trade Indicator and futures open interest combined into signals, with a backtest (see below).
- **Real-Time**: Data updates every few minutes.

## Tech Stack

- **Frontend**: React
- **Backend**: FastAPI (Python)
- **Data**: Upstox API (default) or NSE India (unofficial APIs)
- **Deployment**: Docker

## Setup

### Backend

```bash
cd backend
docker build -t stock-backend .
docker run -p 8000:8000 stock-backend
```

Or for dev:

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
docker build -t stock-frontend .
docker run -p 3000:3000 stock-frontend
```

Or for dev:

```bash
npm install
npm start
```

## Usage

- Go to http://localhost:3000
- Click any stock for details and option analytics, or type an NSE symbol and press **Analyze**.
- Tick **demo data** to see the Wave · GTI · OI panel on synthetic data (useful when NSE blocks your network).

## Wave Theory + Ghost Trade Indicator + Open Interest

Code: `backend/app/wave_oi/`. Endpoint: `GET /api/wave-oi/{symbol}`. It uses daily equity prices plus stock-futures OI summed across expiries.

### Data source: Upstox (default)

Price and OI come from the [Upstox API](https://upstox.com/developer/api-documentation/api-overview) (`backend/app/upstox_data.py`):

- Upstox's daily instrument file maps a symbol like `RELIANCE` to its equity and futures instrument keys.
- Daily candles come from `/v3/historical-candle/...`, plus today's bar from the intraday endpoint. Each row is `[timestamp, open, high, low, close, volume, open_interest]`, so futures candles carry OI directly.
- OI is summed across all futures contracts on the stock. That gives the total market position and smooths the month-end rollover.

Configuration (see `backend/.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `DATA_SOURCE` | `upstox` | `upstox` or `nse` (the old unofficial NSE endpoints) |
| `UPSTOX_ACCESS_TOKEN` | *(empty)* | Optional. Candle data is public. Without a token, OI covers only the live futures contracts (about 3 months back). With a token, expired contracts are fetched through `/v2/expired-instruments/...` and OI history goes back further. Upstox tokens expire daily. |
| `UPSTOX_MAX_EXPIRED` | `24` | How many expired monthly contracts to pull |

```bash
cd backend
export UPSTOX_ACCESS_TOKEN=...   # optional
uvicorn app.main:app --reload
```

On bars before OI history starts, signals are not OI-filtered, and the response's `warning` says from which date OI applies. `?source=upstox|nse|synthetic` overrides `DATA_SOURCE` for a single request.

| Piece | What it does | File |
|---|---|---|
| **Swing pivots** | ZigZag with an ATR-scaled reversal (`zz_atr_mult`, default 2.5 × ATR). A pivot only counts once price has reversed from it, so there is no look-ahead. | `waves.py` |
| **Elliott waves** | Checks the latest pivots against the impulse rules: wave 2 stays above wave 1's start, wave 3 is never the shortest, wave 4 doesn't overlap wave 1. Reports the active setup: **W3** (wave 2 done, ride wave 3), **W5** (wave 4 done, ride wave 5) or **ABC** (5 waves done, expect a correction). Each setup gets a Fibonacci target, a stop beyond the last pivot, the price that invalidates the count, and a 0–1 score for how closely the ratios match 0.618 / 1.618 / 0.382 / 1.0. | `waves.py` |
| **Ghost Trade Indicator** | No public definition exists (the TradingView "GTI" scripts are closed source), so this is our own. An ATR trailing line on smoothed mid-price sets trend direction and timing, flipping on a close through the line. Its "ghost" part flags bars where OI jumps abnormally (z-score ≥ 2) while price barely moves: positions being built quietly. Where the bar closes within its range hints at which side is building. | `gti.py`, `oi.py` |
| **Open interest** | Price/OI quadrant over 5 bars: `new_longs` (↑↑), `short_covering` (↑↓), `new_shorts` (↓↑), `long_liquidation` (↓↓). Also flags OI divergence: a new price extreme while OI is shrinking. | `oi.py` |
| **Signal** | **BUY** when the wave setup points up, GTI is up, OI confirms (`new_longs` or a recent bullish ghost build-up) and reward:risk ≥ `min_rr` (default 1.5). **SELL** is the mirror image. | `strategy.py` |
| **Backtest** | Fills at the next bar's open, sizes each trade to risk 1% of equity, checks the stop before the target within a bar, and exits on a GTI flip against the trade. Charges 5 bps per side. | `backtest.py` |

Query parameters: `days`, `source=upstox|nse|synthetic`, `use_wave`, `use_oi`, `allow_short`, `min_rr`, `zz_atr_mult`, `risk_per_trade`, `fee_bps`.

```bash
curl "http://localhost:8000/api/wave-oi/RELIANCE?days=730"
curl "http://localhost:8000/api/wave-oi/DEMO?source=synthetic"
```

If futures OI can't be fetched (for example a non-F&O stock), the analysis still runs without OI confirmation and the response includes a `warning`.

### Tests

```bash
cd backend
pip install -r requirements-dev.txt
pytest
```

The tests cover the wave rules and the setup levels, OI quadrants, GTI flips, backtest accounting and the API. One test checks for look-ahead: it computes signals on truncated history and requires them to match the full-history signals.

> Educational tool, not investment advice. Wave counts are probabilistic and NSE endpoints are unofficial.

## Notes

- Option and equity data is fetched from NSE using unofficial endpoints and may be rate-limited.
- For production, consider adding caching, error handling, and security enhancements.