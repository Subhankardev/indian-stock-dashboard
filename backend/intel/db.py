"""Database schema and helpers (SQLAlchemy Core).

SQLite at backend/data/intel.db by default; set DATABASE_URL (e.g. postgresql+psycopg://…)
to use PostgreSQL without code changes. Every data row carries ``source_id`` →
``source_files`` for provenance.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Iterable, Optional

import pandas as pd
from sqlalchemy import (Column, Date, DateTime, Float, Integer, MetaData, String, Table, Text,
                        UniqueConstraint, create_engine, delete, func, insert, select)
from sqlalchemy.engine import Engine

from .config import DATA_DIR

meta = MetaData()

source_files = Table(
    "source_files", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("dataset", String(40), nullable=False),
    Column("as_of", Date),
    Column("url", Text),
    Column("fetched_at", DateTime),
    Column("sha256", String(64)),
    Column("rows", Integer),
    Column("status", String(20)),          # ok | holiday | error | empty
    Column("error", Text),
    UniqueConstraint("dataset", "as_of", name="uq_source_dataset_day"),
)

prices_daily = Table(
    "prices_daily", meta,
    Column("symbol", String(40), primary_key=True),
    Column("series", String(4), primary_key=True),
    Column("date", Date, primary_key=True),
    Column("prev_close", Float), Column("open", Float), Column("high", Float), Column("low", Float),
    Column("last", Float), Column("close", Float), Column("avg_price", Float),
    Column("volume", Float), Column("turnover_lacs", Float), Column("trades", Float),
    Column("deliv_qty", Float), Column("deliv_pct", Float),
    Column("source_id", Integer),
)

index_daily = Table(
    "index_daily", meta,
    Column("index_name", String(80), primary_key=True),
    Column("date", Date, primary_key=True),
    Column("open", Float), Column("high", Float), Column("low", Float), Column("close", Float),
    Column("volume", Float), Column("turnover_cr", Float),
    Column("pe", Float), Column("pb", Float), Column("div_yield", Float),
    Column("source_id", Integer),
)

index_members = Table(
    "index_members", meta,
    Column("index_name", String(40), primary_key=True),
    Column("symbol", String(40), primary_key=True),
    Column("as_of", Date, primary_key=True),
    Column("company", Text), Column("industry", Text), Column("isin", String(20)),
    Column("source_id", Integer),
)

fo_daily = Table(
    "fo_daily", meta,
    Column("date", Date, primary_key=True),
    Column("instrument", String(4), primary_key=True),      # STF IDF STO IDO
    Column("symbol", String(40), primary_key=True),
    Column("expiry", Date, primary_key=True),
    Column("strike", Float, primary_key=True),               # 0 for futures
    Column("option_type", String(2), primary_key=True),      # CE PE or '' for futures
    Column("open", Float), Column("high", Float), Column("low", Float), Column("close", Float),
    Column("settle", Float), Column("underlying", Float),
    Column("oi", Float), Column("oi_change", Float), Column("volume", Float), Column("value", Float),
    Column("lot_size", Float),
    Column("source_id", Integer),
)

deriv_daily = Table(
    "deriv_daily", meta,                   # one row per underlying per day, from the F&O bhavcopy
    Column("date", Date, primary_key=True),
    Column("symbol", String(40), primary_key=True),
    Column("kind", String(6)),             # stock | index
    Column("underlying", Float),
    Column("near_expiry", Date), Column("days_to_expiry", Integer),
    Column("fut_close", Float), Column("fut_oi", Float), Column("fut_oi_change", Float),
    Column("fut_volume", Float), Column("basis_pct", Float), Column("rollover_pct", Float),
    Column("ce_oi", Float), Column("pe_oi", Float), Column("pcr_oi", Float),
    Column("ce_volume", Float), Column("pe_volume", Float), Column("pcr_volume", Float),
    Column("max_ce_oi_strike", Float), Column("max_pe_oi_strike", Float),
    Column("atm_strike", Float), Column("atm_iv", Float),
    Column("lot_size", Float),
    Column("source_id", Integer),
)

participant_oi = Table(
    "participant_oi", meta,
    Column("date", Date, primary_key=True),
    Column("client_type", String(10), primary_key=True),     # Client DII FII Pro TOTAL
    Column("fut_idx_long", Float), Column("fut_idx_short", Float),
    Column("fut_stk_long", Float), Column("fut_stk_short", Float),
    Column("opt_idx_call_long", Float), Column("opt_idx_put_long", Float),
    Column("opt_idx_call_short", Float), Column("opt_idx_put_short", Float),
    Column("opt_stk_call_long", Float), Column("opt_stk_put_long", Float),
    Column("opt_stk_call_short", Float), Column("opt_stk_put_short", Float),
    Column("total_long", Float), Column("total_short", Float),
    Column("source_id", Integer),
)

deals = Table(
    "deals", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("date", Date, nullable=False),
    Column("kind", String(6), nullable=False),               # bulk | block
    Column("symbol", String(40), nullable=False),
    Column("security_name", Text),
    Column("client", Text, nullable=False),
    Column("side", String(4), nullable=False),                # BUY | SELL
    Column("qty", Float), Column("price", Float),
    Column("remarks", Text),
    Column("source_id", Integer),
    UniqueConstraint("date", "kind", "symbol", "client", "side", "qty", "price", name="uq_deal"),
)

entities = Table(
    "entities", meta,
    Column("name", String(200), primary_key=True),
    Column("entity_class", String(30)),    # mutual_fund insurer fpi bank pension broker_prop individual unknown
    Column("verified_by", Text),           # rule name or "manual"
    Column("notes", Text),
)

fii_dii_daily = Table(
    "fii_dii_daily", meta,
    Column("date", Date, primary_key=True),
    Column("category", String(10), primary_key=True),       # FII DII
    Column("buy_value", Float), Column("sell_value", Float), Column("net_value", Float),
    Column("source_id", Integer),
)

shareholding = Table(
    "shareholding", meta,
    Column("symbol", String(40), primary_key=True),
    Column("quarter_end", Date, primary_key=True),
    Column("category", String(40), primary_key=True),       # promoter public fpi dii mutual_funds insurance …
    Column("pct", Float), Column("shares", Float),
    Column("published_on", Date),
    Column("source_id", Integer),
)

mf_holdings = Table(
    "mf_holdings", meta,
    Column("month_end", Date, primary_key=True),
    Column("amc", String(120), primary_key=True),
    Column("scheme", String(200), primary_key=True),
    Column("symbol", String(40), primary_key=True),
    Column("shares", Float), Column("market_value_cr", Float),
    Column("published_on", Date),          # when the AMC published it (≈ month end + 10 days)
    Column("source_id", Integer),
)

fundamentals = Table(
    "fundamentals", meta,
    Column("symbol", String(40), primary_key=True),
    Column("period_end", Date, primary_key=True),
    Column("metric", String(40), primary_key=True),
    Column("value", Float),
    Column("published_on", Date),
    Column("source_id", Integer),
)

signals = Table(
    "signals", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("date", Date, nullable=False),
    Column("symbol", String(40), nullable=False),
    Column("module", String(30)), Column("name", String(60)),
    Column("direction", String(8)),        # bullish | bearish | neutral
    Column("strength", Float),
    Column("evidence_class", String(2)),   # A B C D E (see docs/DESIGN.md §8)
    Column("evidence", Text),              # JSON
    Column("rule_version", String(20)),
    UniqueConstraint("date", "symbol", "name", "rule_version", name="uq_signal"),
)

signal_tests = Table(
    "signal_tests", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("signal_name", String(60)), Column("rule_version", String(20)),
    Column("run_at", DateTime), Column("universe", Text), Column("period", Text),
    Column("stats", Text),                 # JSON
    Column("passed", Integer),
)

paper_trades = Table(
    "paper_trades", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("signal_id", Integer), Column("symbol", String(40)), Column("direction", String(8)),
    Column("opened", Date), Column("entry", Float), Column("stop", Float),
    Column("target1", Float), Column("target2", Float), Column("target3", Float),
    Column("qty", Float), Column("status", String(12)), Column("closed", Date),
    Column("exit_price", Float), Column("costs", Float), Column("pnl", Float), Column("notes", Text),
)

_engines: dict[str, Engine] = {}


def database_url() -> str:
    return os.getenv("DATABASE_URL") or f"sqlite:///{DATA_DIR / 'intel.db'}"


def engine(url: Optional[str] = None) -> Engine:
    url = url or database_url()
    if url not in _engines:
        if url.startswith("sqlite:///"):
            path = url[len("sqlite:///"):]
            if path and path != ":memory:":
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        eng = create_engine(url, future=True)
        meta.create_all(eng)
        _engines[url] = eng
    return _engines[url]


def reset_engines() -> None:
    for e in _engines.values():
        e.dispose()
    _engines.clear()


def record_source(eng: Engine, dataset: str, as_of, url: str, sha256: Optional[str], rows: int,
                  status: str, error: Optional[str] = None) -> int:
    """Upsert the provenance record for (dataset, day) and return its id."""
    with eng.begin() as c:
        c.execute(delete(source_files).where(source_files.c.dataset == dataset,
                                             source_files.c.as_of == as_of))
        res = c.execute(insert(source_files).values(
            dataset=dataset, as_of=as_of, url=url, sha256=sha256, rows=rows, status=status, error=error,
            fetched_at=datetime.now(timezone.utc).replace(tzinfo=None)))
        return int(res.inserted_primary_key[0])


def replace_rows(eng: Engine, table: Table, df: pd.DataFrame, where: Iterable) -> int:
    """Delete rows matching ``where`` then insert ``df`` (idempotent per file/day)."""
    rows = df.where(pd.notna(df), None).to_dict("records") if len(df) else []
    with eng.begin() as c:
        c.execute(delete(table).where(*where))
        if rows:
            c.execute(insert(table), rows)
    return len(rows)


def read_sql(eng: Engine, stmt, parse_dates=None) -> pd.DataFrame:
    with eng.connect() as c:
        return pd.read_sql(stmt, c, parse_dates=parse_dates)


def dataset_freshness(eng: Engine) -> pd.DataFrame:
    stmt = (select(source_files.c.dataset, func.max(source_files.c.as_of).label("latest"),
                   func.count().label("files"))
            .where(source_files.c.status.in_(["ok", "empty"]))
            .group_by(source_files.c.dataset))
    return read_sql(eng, stmt)
