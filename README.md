# Institutional Intelligence & Stock Selection (NSE)

A research system for Indian equities. It downloads official NSE daily files, builds a survivorship-free universe with liquidity filters, computes indicators, relative strength and delivery analytics, and tests every signal on history before the signal is allowed to influence a ranking.

Design, data sources, schema, methodology and roadmap: **[docs/DESIGN.md](docs/DESIGN.md)**.

> Research tool, not investment advice. It never places trades. Ordinary volume or delivery is never labelled as "institutional buying"; every data point carries its source file and date.

## Status

| Phase | Scope | State |
|---|---|---|
| 0 | Design (docs/DESIGN.md) | done |
| 1 | NSE download + provenance, universe, liquidity filters, indicators, relative strength, event-study / walk-forward backtest framework, screener / stock / research pages | done |
| 2 | Delivery analytics, bulk/block deals with entity classes, FII/DII, shareholding, accumulation/distribution evidence | next |
| 3–9 | Derivatives, VWAP/order flow, fundamentals, scoring, trade setups + paper trading, portfolio backtests, research assistant | planned |

## Quick start (on your Mac)

NSE blocks many cloud and VPN IP ranges, so run the download from your own network.

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
```

Download index lists and daily cash-market + index files since 2023 (about 1,000 files; paced at ≥0.5 s each, cached in `backend/data/raw/`):

```bash
python -m intel.ingest --lists --from 2023-01-01
```

After that, once a day after about 18:30 IST:

```bash
python -m intel.ingest --days 5 --today
```

Other datasets: `--datasets cm,indices,fo,participant_oi`. A 404 for a weekday is recorded as a holiday; a malformed file is recorded as an error with the reason (see the Overview page).

Test the Phase 1 signals on the stored history:

```bash
python -m intel.research
```

Start the API and the dashboard:

```bash
uvicorn app.main:app --reload
```

```bash
cd frontend && npm install && npm start
```

Open http://localhost:3000.

### Without NSE access: demo data

```bash
cd backend
python -m intel.demo
```

```bash
DATABASE_URL=sqlite:///data/demo.db uvicorn app.main:app --reload
```

Every page shows a **Demo data** banner while synthetic data is loaded.

## Pages

- **Screener**: the latest session for every liquid stock, sortable by RS rank, relative strength over 3/6 months, RS momentum, RVOL, delivery % and its 20-day average, RSI, ADX, distance from the 52-week high and average traded value. Filter by industry.
- **Stock**: price with EMA 20 / 50 DMA / 200 DMA, volume, the RS line vs Nifty 50, daily delivery %, and the file the latest row came from. With an Upstox token, also the option chain.
- **Research**: each signal's event study, giving events, hit rate, 20-day excess return after costs, 95% range, sub-period results and a PASS/fail verdict with the reason.
- **Overview**: data freshness per dataset (flags stale data), recent download errors, the source registry, and the active configuration.

## How signals are tested

For each event, the study enters at the **next session's open** and exits at the close *h* sessions later. It subtracts the Nifty 500 return over the same span and 30 bps round-trip costs. Bearish signals are measured as shorts. Events on the same day are averaged first, so a market-wide day counts once.

A signal **passes** only if all of these hold:
- ≥200 events
- the 95% range of the 20-day excess return is above zero
- it is positive in ≥2 of 3 sub-periods
- no single stock drives more than 25% of the result

`intel/research/walkforward.py` chooses among rule variants using only past data and reports the out-of-sample result. Tests check that indicators don't use future data, that a planted effect is found, and that pure noise fails.

## Configuration

Copy `backend/intel_config.example.json` to `backend/intel_config.json` and change any value; no code changes are needed. You can set the universe (`all`, `nifty50`, `nifty100`, `nifty200`, `nifty500`, `fno`, `custom`), liquidity thresholds, RS windows and benchmark, research horizons, costs and pass rules, and scoring weights. Defaults are in `backend/intel/config.py`.

Storage is SQLite at `backend/data/intel.db` by default. Set `DATABASE_URL=postgresql+psycopg://…` in `backend/.env` to use Postgres.

## Code map

```
backend/intel/
  sources.py        NSE URLs, paced/cached/retrying fetcher, source registry
  parsers.py        validated parsers for each NSE file format (tested on real samples)
  ingest.py         CLI: backfill + daily job, provenance in source_files
  db.py             schema (SQLAlchemy Core)
  indicators.py     trend, momentum, volatility, volume, delivery indicators
  relative_strength.py  RS vs benchmark, cross-sectional rank, sector-relative
  panel.py          universe + liquidity filters + indicators per stock/date
  research/         event study, signals, walk-forward, CLI
  api.py            /api/intel/* endpoints
  demo.py           synthetic data (always labelled demo)
backend/app/        FastAPI app, Upstox client and login, live feed, option-chain analytics, Telegram sender
frontend/src/intel/ React pages
```

## Upstox (optional)

The live feed (`/ws/live`) and the option chain on the stock page use Upstox's daily access token. Put `UPSTOX_API_KEY` and `UPSTOX_API_SECRET` in `backend/.env` (template: `backend/.env.example`), register `http://localhost:8000/api/upstox/callback` as your app's redirect URL, and open `http://localhost:8000/api/upstox/login` each morning. `GET /api/upstox/status` checks the token, and `GET /api/config` shows which settings the backend sees, with secrets masked. Never commit `.env`.

## Tests

```bash
cd backend
pytest
```

The tests never read your `.env` and never touch your real database.
