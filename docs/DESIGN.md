# Institutional Intelligence & Stock Selection System (NSE) — Design

Status: approved by the owner; built phase by phase in this repository.
This replaces the earlier Wave · GTI · OI dashboard, whose signals lost money on
3 years of NIFTY 50 data (−0.20R per trade, see the PR history). That result
shapes one rule of this design: **no signal reaches the scoring engine until it
has passed an out-of-sample test** (§11).

---

## 1. System architecture

```
        ┌───────────── Data sources ─────────────┐
        │ NSE archives (free files)  Upstox API  │
        │ NSE JSON (FII/DII, filings) Manual CSV │
        └───────────────┬────────────────────────┘
                        │ intel.sources  (download, pacing, raw-file cache, provenance)
                        ▼
                 intel.parsers  (one parser per file format, validated on real samples)
                        │
                        ▼
      ┌──────── intel.db (SQLAlchemy: SQLite default, PostgreSQL via DATABASE_URL) ───────┐
      │ prices_daily · delivery · index_daily · fo_contracts · participant_oi · deals ·   │
      │ fii_dii · shareholding · fundamentals · signals · signal_tests · source_files     │
      └───────────────┬───────────────────────────────────────────────────────────────────┘
                      │
   ┌──────────────────┼─────────────────────────────────────────────────────┐
   │ intel.universe   │ intel.indicators   intel.relative_strength          │
   │ intel.institutional (volume, delivery, deals, FII/DII, shareholding)   │
   │ intel.derivatives   intel.orderflow (only with tick data)              │
   │ intel.fundamentals  intel.market (sectors, breadth, regime)            │
   │ intel.scoring  intel.trades (setups, sizing, costs)                    │
   │ intel.research (event studies, backtests, walk-forward)  ◄── gate      │
   │ intel.assistant (Q&A over the computed data)                           │
   └──────────────────┬─────────────────────────────────────────────────────┘
                      ▼
        FastAPI (backend/app/main.py, /api/intel/*)  ──►  React dashboard
        CLI jobs: python -m intel.ingest / intel.scan / intel.research
```

Design principles:
- **Modular**: each data source is a small adapter; each analytic module only reads the database.
- **Provenance everywhere**: every stored row references the `source_files` record it came from
  (URL, as-of date, fetch time, SHA-256, row count). The UI shows the source and timestamp of
  every important number.
- **Evidence classes are explicit** (§8): confirmed disclosures, ownership changes, market-wide
  flows, inferred accumulation/distribution, and unclassified activity are never mixed.
- **Runs on one laptop first** (SQLite, scheduled CLI jobs). PostgreSQL/TimescaleDB, Redis and
  workers are optional upgrades when the data volume needs them; the code doesn't change.

## 2. Data sources

| Data | Source | Format (verified on real files) | Cost | History | Notes |
|---|---|---|---|---|---|
| Daily OHLCV + **delivery** for every NSE security | NSE archives `products/content/sec_bhavdata_full_DDMMYYYY.csv` | CSV; headers padded with spaces; `-` when no delivery | Free | Many years | Includes delisted symbols → survivorship-bias-free universe |
| Daily index OHLC (Nifty 50, sectors) | NSE archives `content/indices/ind_close_all_DDMMYYYY.csv` | CSV; DD-MM-YYYY dates; duplicate rows occur | Free | Many years | Benchmarks for relative strength and sectors |
| Index constituents | NSE archives `content/indices/ind_nifty{50,100,200,500}list.csv` | CSV: Company Name, Industry, Symbol, Series, ISIN | Free | **Current only** | Historical membership isn't published as a file → see §11 bias controls |
| F&O bhavcopy (futures + options, OI) | NSE archives `content/fo/BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip` (UDiFF) | CSV: TradDt, FinInstrmTp (STF/STO/IDF/IDO), TckrSymb, XpryDt, StrkPric, OptnTp, ClsPric, UndrlygPric, OpnIntrst, ChngInOpnIntrst, TtlTradgVol… | Free | Since UDiFF (Jul 2024); older format before | Stock-level OI, basis, strike-wise OI |
| Participant-wise OI (Client/DII/FII/Pro) | NSE archives `content/nsccl/fao_participant_oi_DDMMYYYY.csv` | CSV with a title row, then header (some names contain tabs) | Free | Many years | **Market-wide**, by instrument class; never stock-specific |
| Bulk / block deals | NSE archives `content/equities/bulk.csv`, `block.csv` (daily) | CSV: Date, Symbol, Client Name, Buy/Sell, Quantity Traded, Trade Price; "NO RECORDS" row on quiet days | Free | Daily files (archive them) | Disclosed client names — not automatically institutions |
| FII/DII cash flows | NSE JSON `/api/fiidiiTradeReact` | JSON rows: category, date, buyValue, sellValue, netValue | Free | **Latest day only** | Must be stored daily to build history (NSDL publishes FPI history as an alternative) |
| Shareholding pattern (quarterly) | NSE corporate filings (XBRL per company) | XBRL | Free | Since XBRL filings | 21+ day publication lag after quarter end |
| Mutual-fund portfolios (monthly) | Each AMC's website (AMFI mandates disclosure) | Excel, one layout per AMC | Free | Monthly | No single official API; adapter per AMC |
| Live quotes / intraday candles / option chain | Upstox API (already integrated) | JSON / protobuf | Free for data (per-order fees only apply to orders) | Intraday ~1 yr+ | Daily token; already in `backend/app/upstox_*` |
| Tick-by-tick trades / order book (order flow) | NSE historical tick data (paid product) or a licensed vendor | — | **Paid** | — | Broker websockets give quotes and 5-level depth, **not** trade aggressor side |
| Fundamentals | Exchange filings (XBRL results), or a licensed API | — | Free (filings) / Paid (licensed) | — | **Screener.in has no API; scraping breaks its terms** — supported only as a manual CSV export import |

Every source is registered in `intel.sources.registry` with: data, URL pattern, auth, cost,
rate-limit policy, history, update time, reliability notes and licence restrictions.
Licence: NSE data is free for personal use; redistribution/commercial use needs an NSE data licence.

## 3. API availability & cost comparison (as reported Oct–Nov 2026 — verify before paying)

| Provider | Data API cost | Historical data | Notes |
|---|---|---|---|
| NSE archives | Free | Daily files, many years | No auth; pace requests, send a browser User-Agent |
| Upstox | Free for data | Daily since 2000, intraday | Daily OAuth token (one-click login built) |
| Angel One SmartAPI | Reported free | Yes | Not integrated |
| Zerodha Kite Connect | Reported ₹500/month per app | Yes | Not integrated |
| Dhan | Data reported ~₹499/30 days | Yes | Not integrated |
| NSE tick/order data | Paid NSE data product | Tick-level | Needed only for Phase 4 order flow |

## 4. Data acquisition strategy

1. **Backfill** (`python -m intel.ingest --from 2022-01-01`): walk trading days, download each
   NSE daily file once, store the raw file under `backend/data/raw/<dataset>/`, parse, load.
   A 404 on a weekday is treated as a holiday and recorded, not retried forever.
2. **Daily job** (after 7 pm IST, when participant OI is out): fetch the day's files + FII/DII
   JSON + bulk/block deals.
3. **Pacing**: ≥ 0.5 s between NSE requests, retries with backoff, raw-file cache so re-runs
   cost nothing. Upstox calls reuse the existing paced client.
4. **Validation** on every load: required columns, numeric ranges (high ≥ low, delivery ≤ volume),
   duplicate keys, and row counts vs the previous day; failures are logged in `source_files`.
5. **Freshness** is computed per dataset and shown on the dashboard.

## 5. Database schema (core tables)

| Table | Key | Columns |
|---|---|---|
| `source_files` | id | dataset, url, as_of, fetched_at, sha256, rows, status, error |
| `prices_daily` | symbol, series, date | open, high, low, close, prev_close, last, avg_price, volume, turnover_lacs, trades, source_id |
| `delivery_daily` | symbol, series, date | deliv_qty, deliv_pct, source_id |
| `index_daily` | index_name, date | open, high, low, close, pe, pb, div_yield, source_id |
| `index_members` | index_name, symbol, as_of | industry, isin, source_id |
| `fo_daily` | date, instrument, symbol, expiry, strike, option_type | close, settle, underlying, oi, oi_change, volume, value, lot_size, source_id |
| `participant_oi` | date, client_type | fut_idx_long/short, fut_stk_long/short, opt_idx_call/put_long/short, opt_stk_…, total_long/short, source_id |
| `deals` | date, kind(bulk/block), symbol, client, side, qty, price | remarks, entity_class, source_id |
| `entities` | name | class (MF/insurer/FPI/bank/broker/prop/individual/unknown), verified_by, notes |
| `fii_dii_daily` | date, category | buy_value, sell_value, net_value, source_id |
| `shareholding` | symbol, quarter_end, category | pct, shares, published_on, source_id |
| `fundamentals` | symbol, period, metric | value, published_on, source_id |
| `signals` | id | date, symbol, module, name, direction, strength, evidence_class, evidence_json, rule_version |
| `signal_tests` | id | signal_name, rule_version, run_at, universe, period, horizons, stats_json, passed |
| `paper_trades` | id | signal_id, entry/stop/targets, size, status, exits, costs |

Indexes on (symbol, date) everywhere; `date` partitions if moved to TimescaleDB.

## 6. Backend module structure

```
backend/
  intel/
    config.py            screening & scoring parameters (backend/intel_config.json, editable without code)
    db.py                schema + engine (DATABASE_URL)
    sources/             nse_archives.py, nse_json.py, upstox adapter, registry.py
    parsers/             one module per file format (tested on real samples)
    ingest.py            backfill + daily jobs (CLI)
    universe.py          universes + liquidity / quality filters
    indicators.py        RSI, MACD, EMAs/DMAs, ADX, ATR, Bollinger, Donchian, ROC, Supertrend, RVOL, OBV, CMF, MFI, A/D
    relative_strength.py RS ratios/returns vs Nifty 50/100/500 and sector, RS rank & momentum
    institutional/       volume_price.py, delivery.py, deals.py, flows.py, shareholding.py, evidence.py
    derivatives.py       OI build-up classes, basis, PCR, IV, strike-wise OI, participant positioning
    orderflow.py         VWAP/anchored VWAP (daily data); tick features disabled without tick data
    fundamentals.py      growth/quality/valuation, sector-aware
    market.py            sector strength, breadth, regime
    scoring.py           factor scores, confidence, missing-data penalties, composites
    trades.py            setups, ATR stops, sizing, exposure limits, costs
    research/            event_study.py, backtest.py, walkforward.py, reports.py
    assistant.py         natural-language Q&A grounded in the database
    tests/               unit tests + real-format fixtures
  app/main.py            FastAPI app; /api/intel/* routers
```

## 7. Frontend page structure

- **Overview**: market regime, sector table, FII/DII flows (market-wide, labelled as such), breadth,
  counts of new accumulation/distribution signals, **data freshness** per dataset.
- **Screener / Rankings**: universe picker, filters, bullish and bearish lists (§10 of the brief),
  each row with its score, evidence class and confidence.
- **Institutional**: recent bulk/block deals with entity class, ownership changes, MF activity,
  delivery anomalies.
- **Stock page**: price + RS + volume + delivery charts, VWAP/OBV, ownership history, deal history,
  F&O positioning, fundamentals, evidence for/against, score breakdown, trade setup, signal history
  with tested performance.
- **Research**: every signal's test results (event study + walk-forward), pass/fail.
- **Alerts** and **Assistant**.

## 8. Accumulation methodology (evidence-based)

Each piece of evidence is stored with a class; the class is shown wherever it's used.

| Class | Examples | Can it say "institution bought"? |
|---|---|---|
| A. Confirmed disclosed transaction | Bulk/block deal where the client is a verified MF/insurer/FPI | Yes — that entity, that day |
| B. Observable ownership change | Shareholding pattern: FPI % up 0.8 pp QoQ; MF portfolio adds | Yes — with the publication lag shown |
| C. Market-wide positioning | FII/DII cash flows; participant OI | **Never stock-specific** |
| D. Possible accumulation (inferred) | Rising delivery qty + price holding/rising, high-RVOL up-days, low-volume pullbacks, absorption on daily bars, OBV/CMF/A-D up | No — "possible" only |
| E. Unclassified large activity | Big volume spike with no direction | No |

Accumulation score (inputs normalised to cross-sectional percentiles, 0–100):
`0.30·deal_evidence(A) + 0.25·ownership_change(B) + 0.30·inferred_accumulation(D) + 0.15·derivatives_long_buildup`
— weights are **starting points**; each component enters only after passing its own test (§11),
and a component with missing data contributes nothing and lowers the confidence score.

Inferred accumulation (D) sub-signals: delivery quantity 20-day trend vs 50-day average with price
not falling; ≥3 of the last 10 sessions with delivery % > 1.5× its 50-day median and close in the
upper half of the range; up-volume/down-volume ratio over 20 days; CMF(20) > 0 and rising; failed
breakdown (close back above a 20-day low within 3 sessions on above-average volume).

## 9. Distribution methodology

Mirror image: confirmed sells by verified institutions (A), ownership reductions (B), and inferred
distribution (D): high delivery on down days, heavy volume without price progress near highs
(failed breakouts), up/down volume ratio < 1, CMF falling, short build-up in futures. Same classes,
same tests, separate bearish score.

## 10. Multi-factor scoring

For stock *i* on date *t*: each factor f ∈ {accumulation, distribution, RS, momentum, volume,
delivery, derivatives, fundamentals, valuation, sector, risk} is a cross-sectional percentile
`p_f(i,t) ∈ [0,100]` within the chosen universe (sector-neutral where noted).

```
bullish(i,t) = Σ_f w_f · p_f(i,t) · c_f(i,t)  /  Σ_f w_f · c_f(i,t)
confidence(i,t) = Σ_f w_f · c_f(i,t) / Σ_f w_f              (c_f = 1 if fresh data, 0 if missing,
                                                              decays with staleness)
agreement(i,t) = share of available factors with p_f > 60
```

- Default weights from the brief (accumulation 20, RS 15, volume+delivery 15, momentum 15,
  fundamentals 10, derivatives 10, sector 5, valuation 5, risk 5), editable in `intel_config.json`.
- **Only factors that passed their test are active**; untested factors show in the UI but carry weight 0.
- Missing data never counts as neutral: it lowers confidence, and rankings require confidence ≥ 0.6.
- Conflicts (e.g. accumulation high but distribution high) are flagged, not averaged away.
- The score is a **rank, not a probability**, until calibrated against outcomes.

## 11. Backtesting methodology (the gate)

1. **Event studies** for every signal: forward returns at 5/10/20/60 sessions, in excess of Nifty 500
   (and sector), hit rate, mean and median excess return, t-statistic and bootstrap CI, by year and by
   market regime.
2. **Walk-forward**: parameters chosen on rolling 2-year windows, evaluated on the next 6 months,
   stitched together. Only out-of-sample results count.
3. **Pass rule**: ≥ 200 events, mean 20-day excess return > 0 with a 95% CI above 0 *after costs*,
   positive in ≥ 2 of 3 sub-periods, and not dependent on one stock or month.
4. **Trade simulation** (Phase 7/8): entries next open, ATR stops, sizing by risk, realistic costs
   (brokerage, STT, exchange fees, GST, stamp duty, slippage ~0.1% liquid / 0.3% mid-cap),
   capacity limit (≤ 5% of 20-day traded value), exposure limits; CAGR, Sharpe, Sortino, max DD,
   profit factor, expectancy, turnover, exposure vs Nifty 50 / Nifty 500.
5. **Bias controls**: point-in-time joins (a value is usable only from its `published_on`),
   signals computed only from data ≤ t, survivorship-free universe from bhavcopy (all symbols that
   traded then, not today's index list), and parameter-sensitivity tables.
6. Every signal ever generated is stored, including those not traded.

## 12. Development roadmap

| Phase | Content | Exit criterion |
|---|---|---|
| 0 | This design | Done |
| 1 | Data store + NSE ingestion (CM+delivery, indices, index lists), universe & liquidity filters, indicators, relative strength, **research framework (event study + walk-forward)**, basic dashboard | Pipeline runs on real data on the owner's Mac; RS/momentum signals tested — **built** |
| 2 | Delivery analytics, bulk/block deals + entity classification, FII/DII store, shareholding, MF portfolios, evidence classes, accumulation/distribution detection | Each signal tested; evidence shown with classes — **built** (shareholding/MF via CSV import; per-AMC adapters and XBRL parsing remain manual) |
| 3 | F&O bhavcopy, OI build-up classes, basis, PCR, IV, strike OI, participant OI | Tested; market-wide vs stock-specific separated — **built** |
| 4 | VWAP / anchored VWAP on daily data; tick-based order flow behind a feature flag (needs paid data) | Disabled features explain what data they need — **built** |
| 5 | Fundamentals from filings / CSV import, sector-aware quality & valuation | Tested as a filter — **built** |
| 6 | Scoring engine with confidence, agreement, conflicts; ranked lists & special alerts | Only passed factors weighted — **built** |
| 7 | Trade setups, ATR stops, sizing, exposure limits, costs, paper trading, alerts | Paper-trade log — **built** |
| 8 | Full portfolio backtests, walk-forward reports, sensitivity | Report vs Nifty 50/500 |
| 9 | Research assistant grounded in the database | Answers cite stored data and evidence classes |

## 13. Infrastructure & subscription costs

| Item | Cost |
|---|---|
| Running on the owner's Mac (SQLite) | ₹0 |
| NSE official daily files, Upstox data API | ₹0 |
| Optional cloud VM + PostgreSQL (small) | ~₹1,500–3,000/month |
| Optional extra broker data (Kite / Dhan) | ~₹500/month each (as reported) |
| Tick-level data for Phase 4 order flow | Paid NSE/vendor product — only if Phases 1–3 show value |
| Licensed fundamentals API | Optional; filings + CSV import are free |
| Research assistant (Claude API) | Pay per use; a few rupees per question at typical sizes |

**Free vs paid:** everything in Phases 1–3, 5 (via filings/CSV), 6–9 runs on free official data.
Only Phase 4 tick-level order flow and footprint charts need paid data.

## Limitations stated up front

- Shareholding and MF data are delayed by weeks; they confirm, they don't time.
- FII/DII and participant OI are market-wide; the system never attributes them to a stock.
- Daily data can only *suggest* absorption; true order-flow absorption needs tick data.
- Index membership history isn't in the free files; backtests use the full bhavcopy universe
  filtered by liquidity at each date, which avoids survivorship bias without it.
- No real orders are placed. Signals are research until paper-traded and tested.

## Sources checked while writing this design

- NSE data catalog (community): https://nikhilsuthar.github.io/indian-market-data/data-catalog.html
- NSE UDiFF guidance (BSE): https://www.bseindia.com/downloads1/UDiFF_guidance_document_Ver1_0.pdf
- Participant-wise OI explainer: https://algotest.in/blog/participant-wise-open-interest/
- Broker API pricing (Oct 2026): https://onetradejournal.com/trading-apis ,
  https://support.zerodha.com/category/trading-and-markets/kite-api/articles/what-are-the-charges-for-kite-apis
- Real sample files used as test fixtures: girishg4t/nse-bse-bhavcopy (delivery bhavcopy),
  hirupamganguly/NSE-OI-analysis-GUI-and-Backend (participant OI), AvilPage/historical-option-chain-data
  (UDiFF F&O), NeelDigonto/BhavCopy (bulk/block), pkjmesra/PKScreener (index list),
  Theraj4014/Theoritical-Prices-and-Greeks-calcualtor (index closes).
