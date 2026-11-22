# Institutional Intelligence & Stock Selection (NSE)

A research system for Indian equities. It downloads official NSE daily files, builds a survivorship-free universe with liquidity filters, computes indicators, relative strength and delivery analytics, and tests every signal on history before the signal is allowed to influence a ranking.

Design, data sources, schema, methodology and roadmap: **[docs/DESIGN.md](docs/DESIGN.md)**.

> Research tool, not investment advice. It never places trades. Ordinary volume or delivery is never labelled as "institutional buying"; every data point carries its source file and date.

## Status

| Phase | Scope | State |
|---|---|---|
| 0 | Design (docs/DESIGN.md) | done |
| 1 | NSE download + provenance, universe, liquidity filters, indicators, relative strength, event-study / walk-forward backtest framework, screener / stock / research pages | done |
| 2 | Delivery analytics, bulk/block deals with entity classes, FII/DII, shareholding + MF imports, accumulation/distribution evidence (classes A–E), Institutional page | done |
| 3 | F&O: futures build-ups (class F), basis, rollover, PCR, max-OI strikes, ATM IV, participant OI; Derivatives page | done |
| 4 | Daily VWAP + anchored VWAPs, absorption proxy, intraday VWAP bands + volume profile (Upstox 1-min), order-flow features behind a flag | done |
| 5 | Point-in-time fundamentals (CSV import), sector-aware quality & valuation scores, market cap, fundamentals tested as a filter | done |
| 6 | Multi-factor scoring (tested factors only, confidence, agreement, conflicts), market regime, breadth, sector strength, Rankings page, special situations | done |
| 7 | Trade setups (ATR/swing stops, R targets), sizing, Indian delivery cost model, exposure limits, paper trading, alerts (+ Telegram), daily job | done |
| 8 | Portfolio backtests, walk-forward reports, sensitivity | next |
| 9 | Research assistant grounded in the database | planned |

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

After that, once a day after about 18:30 IST, run the daily job (download → paper-trade update → alerts):

```bash
python -m intel.daily
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

## Institutional evidence (Phase 2)

Every piece of accumulation/distribution evidence carries a class, shown wherever it is used:

| Class | What | Example |
|---|---|---|
| A | Confirmed disclosed transaction | Bulk/block deal where the client is classified as a mutual fund, insurer, FPI, bank or pension fund |
| B | Ownership change | FPI+DII holding up ≥0.5 pp quarter on quarter; MF shares up ≥5% month on month. Used only from the **publication date** |
| C | Market-wide | FII/DII cash flows. **Never** attached to a stock |
| D | Possible (inferred) | Rising delivery while price holds, high-delivery up-days, up/down volume, CMF, failed breakdowns |
| E | Unclassified large activity | Deals by brokers, prop desks and individuals; volume spikes without a price move |

- **Daily:** `python -m intel.ingest --today` also stores the day's bulk/block deals and the provisional FII/DII figures. NSE only shows the latest day of each, so run it every day.
- **History and data NSE doesn't publish as one file:** import a CSV.
  ```bash
  python -m intel.imports deals bulk_deals.csv --kind bulk     # NSE website's bulk-deal CSV download
  python -m intel.imports shareholding shareholding.csv        # symbol,quarter_end,category,pct[,published_on]
  python -m intel.imports mf mf_holdings.csv                   # month_end,amc,scheme,symbol,shares[,published_on]
  python -m intel.imports fii_dii fii_dii.csv                  # date,category,buy_value,sell_value,net_value
  ```
  Without `published_on`, a filing is assumed public 21 days after the quarter end (shareholding) or 10 days after the month end (MF), so it is never used before it could have been known.
- **Entity classes** come from name rules (`intel/entities.py`). Each deal shows the rule that matched. Correct a name with `PUT /api/intel/entities` or `backend/entity_overrides.csv` (`name,entity_class,notes`); a manual entry always wins.
- **Accumulation / distribution score** = 0.30·A + 0.25·B + 0.30·D + 0.15·derivatives. A component is weighted **only after its signal passes** its event study on the Research page (`scoring.require_pass`). Confidence is the share of components that have data; derivatives arrive in Phase 3, so confidence is at most 85% for now.
- `python -m intel.evidence --store` writes every fired signal, with its evidence, to the `signals` table.

## Derivatives (Phase 3)

`python -m intel.ingest --days 60 --datasets fo,participant_oi` loads the F&O bhavcopy and participant-wise OI.

- Each day's F&O file is summarised **per underlying** into `deriv_daily`:
  - total futures OI across expiries, so monthly rollover isn't mistaken for unwinding
  - basis and rollover %
  - near-expiry PCR, the strikes with the highest call and put OI
  - ATM implied volatility (Black-Scholes, solved from the settlement price)
- Raw option rows (about 50–100k a day) aren't stored unless `nse.store_option_rows` is true. Run `python -m intel.derivatives --rebuild` to recompute the summaries from stored rows.
- **Build-ups (class F)** compare the 5-session price change with the change in total futures OI (threshold ±5%):
  - long build-up: price up, OI up
  - short build-up: price down, OI up
  - short covering: price up, OI down
  - long unwinding: price down, OI down

  They are blanked for 3 sessions after an expiry. They describe positioning, not who traded.
- **Participant OI** (FII/DII/Pro/Client share of index-futures longs) is market-wide (class C).
- Futures build-ups are the derivatives component (weight 0.15) of the accumulation and distribution scores, and count only after `long_buildup` or `short_buildup` passes its test.

## VWAP and order flow (Phase 4)

- **Daily VWAP is exact**: NSE's bhavcopy gives each day's average traded price (turnover ÷ volume). Rolling VWAP 20/50 and **anchored VWAPs** are built from it. Anchors are the 52-week high, the 52-week low, the highest-volume session of the last 60, and the start of the year; each is chosen only from data available on that day.
- **Absorption (daily-bar proxy, class D)**: ≥2× volume in a narrow range (≤0.6× ATR) closing in the upper or lower third.
- **Intraday**: VWAP with ±1σ/±2σ bands and a volume profile (POC, 70% value area) from Upstox 1-minute candles. This is a bar approximation, not order flow.
- **Order flow** (cumulative delta, large trades, order-book imbalance, tick absorption, icebergs) needs tick-by-tick or order-book data. It is **disabled**; the Overview page lists what each feature needs and why. Enable it with `"orderflow": {"enabled": true, "provider": ...}` once a licensed feed and its loader exist.

## Fundamentals (Phase 5)

There is no free bulk fundamentals API, and Screener.in has none (scraping it breaks its terms), so results come in as a CSV:

```bash
python -m intel.imports fundamentals fundamentals.csv   # symbol,period_end,period_type(Q|A),metric,value[,published_on]
```

- **Metrics**: `revenue, ebitda, pat, eps, interest, total_equity, total_debt, cfo, shares, gnpa_pct, nnpa_pct, car_pct`. Values are in ₹ crore, except shares (crore), EPS (₹) and percentages.
- **Point-in-time**: a quarter counts from its `published_on` date. The default is period end + 45 days for quarters and + 60 days for annual results, the SEBI deadlines. TTM needs 4 published quarters, and figures go stale after 270 days without a new filing.
- **Ratios**: ROE, ROCE, debt/equity, margins, cash conversion, YoY TTM growth. With price, also P/E, P/B, earnings and book yield, and **market cap**.
- **Sector-aware**:
  - Financials skip leverage, EBITDA margin and cash conversion, and use GNPA instead.
  - Quality and value are percentiles **within the industry** when it has ≥5 stocks with data; otherwise they are ranked against the whole universe, and the page says so.
- **Tested as a filter**: `rs_leaders_quality` and `breakout_volume_quality` are the base signals restricted to quality ≥60. The Research page compares each with its base.

## Scoring (Phase 6)

Nine factors are cross-sectional percentiles among liquid stocks on each date: accumulation, relative strength, momentum, volume and delivery, derivatives, fundamentals, valuation, sector, and risk.

```
score      = Σ w·p·c / Σ w·c         over ACTIVE factors      (c = 1 data, 0.5 stale, 0 missing)
confidence = Σ w·c / Σ w             over ACTIVE factors
agreement  = share of available factors with p > 60
```

- A factor is **active only after its own test passes**. `factor_<name>` is the event study of stocks entering that factor's top quintile, and runs with `python -m intel.research`. Until something passes, the Rankings page shows nothing. **Unvalidated preview** switches every factor on and is clearly bannered.
- Rankings require confidence ≥ `scoring.min_confidence` (0.6). Weights are in `scoring.weights`.
- **Conflicts** are flagged per stock rather than averaged away. Examples: strong accumulation and distribution evidence together, strong RS with distribution clues, accumulation during a futures short build-up.
- **Special situations** are combinations listed for a look, such as accumulation clues on a breakout, or institutional buying above the 52W-high AVWAP. They are not recommendations.
- **Market** (Overview): the regime (index vs 200-DMA, 50-DMA slope, breadth), % above 50/200-DMA, advancers/decliners, new highs/lows, and sector RS ranking with its 20-day change.

## Trade setups, paper trading and alerts (Phase 7)

> Nothing in this system places a real order. Setups are for research and paper trading only.

- **Setup**:
  - The trigger is the day's high (breakout entry: it must trade through next session) or the close (entry at the next open).
  - The stop is the tighter of `trigger − 2·ATR14` and the 10-session swing low.
  - Targets are at 1.5R and 3R.
- **Sizing**: 0.5% of capital at risk, capped at 10% of capital per position and at 1% of the 20-day average volume. The UI says which of these limits set the quantity.
- **Costs** use an equity delivery model:
  - brokerage, STT 0.1% on both sides, exchange charges, SEBI fee
  - stamp duty of 0.015% on the buy, GST, the DP charge on the sell, and slippage

  Defaults are in `costs`; verify them with your broker.
- **Exposure limits**: at most 10 open positions, 30% of capital per sector, 100% in total, and one position per stock.
- **Paper trading** (`python -m intel.paper --update | --auto | --report`) is conservative:
  - Breakout entries fill only on a trade through the trigger, and a gap fills at the open.
  - If one bar touches both stop and target, the stop counts first.
  - Gap-through stops exit at the open, and trades time out after 20 sessions.
  - P&L is after costs, with R-multiples, win rate, expectancy and drawdown.
  - `--auto` opens the top-ranked names only when factors have passed their tests.
- **Alerts** go to the `intel_alerts` table and, when `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` are set, to Telegram:
  - special situations, and new entrants to the top 10 (tested factors only)
  - paper set, fill and exit
  - stale data

  Duplicates are suppressed, and each alert is sent once.

## Pages

- **Rankings**: bullish and bearish lists with score, confidence, agreement, a factor heat-map, conflicts and special situations.
- **Screener**: the latest session for every liquid stock, sortable by RS rank, relative strength over 3/6 months, RS momentum, RVOL, delivery % and its 20-day average, RSI, ADX, distance from the 52-week high and average traded value. Filter by industry.
- **Stock**: score breakdown, trade setup with a paper-trade button, price with EMA 20 / 50 DMA / 200 DMA, volume, the RS line vs Nifty 50, daily delivery %, the accumulation/distribution evidence with classes, shareholding and deals, and the file the latest row came from. With an Upstox token, also the option chain.
- **Institutional**: FII/DII flows (class C), stocks with the most accumulation/distribution evidence, and recent bulk/block deals with entity classes.
- **Derivatives**: participant index-futures positioning, NIFTY PCR and IV, and today's stock futures build-ups.
- **Paper**: the paper-trade log, P&L after costs, equity curve, and process/auto buttons.
- **Alerts**: the alert feed, Telegram status and test.
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
  entities.py       deal-client classification (rules + manual overrides)
  evidence.py       accumulation/distribution evidence, classes A–E, scores
  imports.py        CSV imports: deals, shareholding, MF holdings, FII/DII
  derivatives.py    F&O daily summaries, implied volatility, build-ups, participant OI
  vwap.py           daily rolling + anchored VWAP, absorption proxy
  intraday.py       1-minute VWAP bands and volume profile
  orderflow.py      tick/order-book features (feature-flagged, explain what they need)
  fundamentals.py   point-in-time fundamentals, sector-relative quality/value
  market.py         regime, breadth, sector strength
  scoring.py        factor percentiles, gated scores, confidence, conflicts, rankings
  setups.py         trade setups, sizing, cost model, exposure limits
  paper.py          paper-trading engine
  alerts.py         alert generation + Telegram delivery
  daily.py          the daily job
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
