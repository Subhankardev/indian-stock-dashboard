# Indian Stock Market Analytics Dashboard

A full-stack web dashboard for real-time Indian stock market analytics, including buy/sell signals, technical analysis, and NSE option data.

## Features

- **Trending Stock Screener**: See which stocks are trending with signals (BUY/SELL/HOLD).
- **Technical Indicators**: SMA, RSI, MACD, Bollinger Bands.
- **Buy/Sell Alerts**: watchlist scanner with forming / confirmed signal alerts, desktop notifications and Telegram (see below).
- **Option Chain Analytics** (Upstox): PCR, max pain, OI walls, ATM IV and expected move, OI build-ups per strike (see below).
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
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # first time only: then put your tokens in .env
uvicorn app.main:app --reload
```

### Settings: `backend/.env`

All settings live in `backend/.env`, which the backend reads at startup. Create it once from the template (`cp .env.example .env`) and fill in what you use:

```ini
UPSTOX_ACCESS_TOKEN=eyJ...          # regular token: daily; extended token: about a year
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=7844852392
```

- **Restart the backend after editing `.env`**. `--reload` only watches the code.
- `.env` is gitignored, so it can't be committed by accident. Never commit tokens.
- A variable exported in your terminal overrides `.env`, which is handy for one-off runs like `DATA_SOURCE=nse uvicorn ...`.
- **`http://localhost:8000/api/config`** shows every setting the backend sees and where it came from (`.env`, shell, or not set), with secrets masked. Check it first when something "isn't picking up" a setting.
- The tests never read `.env`, so your real tokens are never used during `pytest`.
- Long tokens copied from a chat or web page often carry invisible characters (zero-width spaces, soft hyphens) or stray quotes. The backend strips these automatically, and `/api/config` notes when it did.

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

## Buy/Sell alerts

The 🔔 **Signal alerts** panel watches a list of symbols and tells you when the Wave · GTI · OI rules fire a BUY or SELL. Code: `backend/app/alerts/`.

**How it works:**
- A scanner in the backend re-runs the strategy for each watched symbol. It runs **every minute while NSE is open** (Mon–Fri 09:15–15:30 IST) and every 10 minutes otherwise.
- During market hours, the last daily bar is **today's forming bar**, built from Upstox's intraday candle and refreshed every 60 s. That gives three kinds of alert:
  - **forming**: the signal would fire if the day closed at the current price. This is an early heads-up and can still change.
  - **faded**: a forming signal disappeared, either intraday or because it didn't hold into the close.
  - **confirmed**: the signal is on a completed daily bar. This is the one the backtest trades. Each confirmed signal is alerted once.
- Every alert includes the price, stop, target and the reason (wave setup, GTI and OI state).

**In the dashboard:**
- Add symbols with **+ Watch SYMBOL** or the *add symbol* box; remove them with ×.
- Click **Enable desktop notifications** once. On macOS, the browser also needs notifications allowed in *System Settings → Notifications*.
- **Send test alert** checks that notifications and sound work. **Scan now** runs a scan straight away.
- Clicking a notification opens that symbol.

The dashboard tab must be open to receive notifications; the backend keeps scanning and records alerts either way. The watchlist and alert history are saved in `backend/.alerts.json` (gitignored), so a restart doesn't re-alert.

### Telegram alerts (optional)

Every alert can also go to Telegram, so it reaches your phone even when the dashboard is closed. This takes about 5 minutes to set up:

1. In Telegram, message **@BotFather**, send `/newbot` and follow the prompts. It gives you a **bot token** like `123456:ABC-DEF…`.
2. Open a chat with your new bot and send it any message, e.g. "hi".
3. Put the token in `backend/.env` as `TELEGRAM_BOT_TOKEN=123456:ABC-DEF...` and restart the backend. Then open `http://localhost:8000/api/alerts/telegram/chats`, which lists your `chat_id`.
4. Add `TELEGRAM_CHAT_ID=<that number>` to `backend/.env` and restart the backend. The alerts panel should say "Telegram alerts on". Click **Telegram test**, or call `POST /api/alerts/telegram/test`.

Optional: `TELEGRAM_ALERT_KINDS=confirmed` sends only confirmed signals. The default is `forming,confirmed,faded,test`.

- Messages are sent from a background thread, so a slow or unreachable Telegram never delays scanning.
- If Telegram rate-limits a message, it is retried once.
- A failure shows up in the alerts panel, and the alert is still recorded and shown in the dashboard.
- The bot token never leaves the backend: the API shows it masked and removes it from error messages.

**API:**
- `GET /api/alerts?since=<id>`
- `PUT /api/alerts/watchlist` with body `{"symbols": [...]}`
- `POST` or `DELETE /api/alerts/watchlist/{symbol}`
- `POST /api/alerts/scan`
- `POST /api/alerts/test`
- `GET /api/alerts/telegram/chats`
- `POST /api/alerts/telegram/test`

**Environment variables:** `ALERTS_FILE`, `ALERTS_ENABLED` (`0` turns the scanner off), `ALERTS_INTERVAL` (seconds while the market is open; default 60).

## Option chain (Upstox)

`GET /api/options/{symbol}?expiry=YYYY-MM-DD` works for stocks (`RELIANCE`) and indices (`NIFTY`, `BANKNIFTY`, `FINNIFTY`, …). Code: `backend/app/option_analytics.py`, with fetching in `backend/app/upstox_data.py`.

- **Upstox expiries** come from the instrument file, which needs no token and also maps `NIFTY` → `NSE_INDEX|Nifty 50`. The chain itself comes from `GET /v2/option/chain` and **needs `UPSTOX_ACCESS_TOKEN`**. It is cached for 30 seconds, and the dashboard refreshes it every minute.
- **Analytics:**
  - put-call ratio by OI and by volume
  - **max pain**
  - **OI walls**: the biggest call OI at or above spot is resistance, and the biggest put OI at or below spot is support
  - ATM IV, ATM straddle price and the **expected move** it implies
  - biggest OI changes vs the previous day
  - a per-contract **build-up** tag from that contract's price and OI change: long build-up, short build-up, short covering, long unwinding
- Parameters: `expiry` (default: nearest), `source=upstox|nse|synthetic`, `window` (strikes either side of ATM in `rows`; `0` = all), `spot` (for `synthetic`).
- The original response keys (`PCR`, `max_CE_OI_strike`, `max_PE_OI_strike`, `total_CE_OI`, `total_PE_OI`, `OI_data`) are still returned. With `DATA_SOURCE=nse`, the NSE chain goes through the same analytics and is now filtered to one expiry.
- On the dashboard, the option chain panel has an expiry picker, summary cards and a strike table with OI bars centred on ATM. The "demo data" box shows a demo chain.

## Wave Theory + Ghost Trade Indicator + Open Interest

Code: `backend/app/wave_oi/`. Endpoint: `GET /api/wave-oi/{symbol}`. It uses daily equity prices plus stock-futures OI summed across expiries.

### Data source: Upstox (default)

Price and OI come from the [Upstox API](https://upstox.com/developer/api-documentation/api-overview) (`backend/app/upstox_data.py`):

- Upstox's daily instrument file maps a symbol like `RELIANCE` to its equity and futures instrument keys.
- Daily candles come from `/v3/historical-candle/...`, plus today's bar from the intraday endpoint. Each row is `[timestamp, open, high, low, close, volume, open_interest]`, so futures candles carry OI directly.
- OI is summed across all futures contracts on the stock. That gives the total market position and smooths the month-end rollover.

Configuration (in `backend/.env`; see `backend/.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `DATA_SOURCE` | `upstox` | `upstox` or `nse` (the old unofficial NSE endpoints) |
| `UPSTOX_ACCESS_TOKEN` | *(empty)* | Optional. Candle data is public. Without a token, OI covers only the live futures contracts (about 3 months back). With a token, expired contracts are fetched through `/v2/expired-instruments/...` and OI history goes back further. A regular Upstox token expires daily; an *extended* token lasts about a year (treat it like a password). |
| `UPSTOX_MAX_EXPIRED` | `24` | How many expired monthly contracts to pull |

Set these in `backend/.env` (see *Settings* above) and restart the backend.

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

### Live feed (Upstox WebSocket)

The panel's top strip streams live data from the [Upstox Market Data Feed V3](https://upstox.com/developer/api-documentation/api-overview). Code is in `backend/app/live/`.

```
Upstox wss://api.upstox.com/v3/feed/market-data-feed   (protobuf, one connection, token stays on the server)
        │
   backend LiveHub   ── reference-counted subscriptions, latest-tick cache, reconnect with backoff
        │
   ws://localhost:8000/ws/live?symbols=RELIANCE   (JSON, one per browser tab)
        │
   LiveQuote strip in the Wave · GTI · OI panel
```

- For each symbol it streams the **equity** and its **near-month future** in `full` mode. The strip shows last price, change vs previous close, bid/ask, day O/H/L, volume, futures price and basis, and **live OI vs the previous day's OI**. It also shows a live price/OI read: fresh longs, short covering, fresh shorts or long liquidation (0.5% OI threshold).
- It compares the live price with the levels from the daily analysis. You see a warning when price crosses the GTI line ("GTI flips if it closes here") or breaks the wave stop or target, and otherwise the % distance to each.
- **Needs `UPSTOX_ACCESS_TOKEN`** on the backend. Without it the endpoint returns a clear error. In demo mode, or with `&source=mock`, a random-walk mock feed is used instead.
- It uses its own small client rather than the Upstox SDK. The SDK doesn't install on Python 3.13 (it depends on the obsolete PyPI `uuid` package) and turns off TLS certificate checking. The protobuf schema comes from the SDK (MIT licence). A readable copy is in `app/live/MarketDataFeedV3.proto`.
- `GET /api/live/status` shows the upstream connection state, subscribed keys and viewer count.

Messages sent to the browser: `hello`, `tick` (`{symbol, leg: "EQ"|"FUT", tick, live}`), `feed_status`, `market_status`, `error`. See `app/live/routes.py`.

### Tests

```bash
cd backend
pip install -r requirements-dev.txt
pytest
```

The tests also cover the Upstox data and live-feed code against fake Upstox responses, and the browser WebSocket end to end in mock mode. Beyond that, they cover the wave rules and the setup levels, OI quadrants, GTI flips, backtest accounting and the API. One test checks for look-ahead: it computes signals on truncated history and requires them to match the full-history signals.

> Educational tool, not investment advice. Wave counts are probabilistic and NSE endpoints are unofficial.

## Notes

- Option and equity data is fetched from NSE using unofficial endpoints and may be rate-limited.
- For production, consider adding caching, error handling, and security enhancements.