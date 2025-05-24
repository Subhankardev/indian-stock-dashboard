# Indian Stock Market Analytics Dashboard

A full-stack web dashboard for real-time Indian stock market analytics, including buy/sell signals, technical analysis, and NSE option data.

## Features

- **Trending Stock Screener**: See which stocks are trending with signals (BUY/SELL/HOLD).
- **Technical Indicators**: SMA, RSI, MACD, Bollinger Bands.
- **Option Chain Analytics**: PCR, OI-based support/resistance.
- **Real-Time**: Data updates every few minutes.

## Tech Stack

- **Frontend**: React
- **Backend**: FastAPI (Python)
- **Data**: NSE India (unofficial APIs)
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
- Click any stock for details and option analytics.

## Notes

- Option and equity data is fetched from NSE using unofficial endpoints and may be rate-limited.
- For production, consider adding caching, error handling, and security enhancements.