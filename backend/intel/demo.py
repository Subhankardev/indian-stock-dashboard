"""Synthetic NSE-like data for tests and offline demos — clearly labelled 'demo'.

    python -m intel.demo            # writes backend/data/demo.db; run the API with
                                    # DATABASE_URL=sqlite:///backend/data/demo.db

Prices follow market + sector + stock-specific random walks; delivery, turnover and an
index series (Nifty 50, Nifty 500) are derived consistently. Nothing here is real market data.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from . import db

SECTORS = ["Financial Services", "Information Technology", "Automobile", "Healthcare", "Metals & Mining", "FMCG"]


def make(n_symbols: int = 60, days: int = 600, seed: int = 11, end: date = date(2026, 11, 20)) -> dict:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=end, periods=days)
    mkt = rng.normal(0.0004, 0.009, days)
    sect = {s: rng.normal(0, 0.006, days) for s in SECTORS}
    rows, members = [], []
    for i in range(n_symbols):
        sym = f"DEMO{i:02d}"
        sector = SECTORS[i % len(SECTORS)]
        beta = rng.uniform(0.7, 1.3)
        drift = rng.normal(0, 0.0004)
        r = drift + beta * mkt + sect[sector] + rng.normal(0, 0.014, days)
        close = rng.uniform(80, 3000) * np.exp(np.cumsum(r))
        prev = np.concatenate([[close[0] / np.exp(r[0])], close[:-1]])
        opn = prev * np.exp(rng.normal(0, 0.004, days))
        high = np.maximum(opn, close) * (1 + np.abs(rng.normal(0, 0.007, days)))
        low = np.minimum(opn, close) * (1 - np.abs(rng.normal(0, 0.007, days)))
        base_vol = rng.uniform(2e5, 4e6)
        vol = base_vol * np.exp(rng.normal(0, 0.45, days) + 3 * np.abs(r))
        dpct = np.clip(rng.normal(45, 12, days), 5, 98)
        rows.append(pd.DataFrame({
            "symbol": sym, "series": "EQ", "date": dates.date, "prev_close": prev, "open": opn, "high": high,
            "low": low, "last": close, "close": close, "avg_price": (high + low + close) / 3, "volume": vol.round(),
            "turnover_lacs": vol * close / 1e5, "trades": (vol / 150).round(),
            "deliv_qty": (vol * dpct / 100).round(), "deliv_pct": dpct.round(2)}))
        members.append({"symbol": sym, "company": f"Demo Company {i:02d} Ltd", "industry": sector, "isin": None})
    prices = pd.concat(rows, ignore_index=True)
    idx_close = 20000 * np.exp(np.cumsum(mkt))
    idx = []
    for name, scale in (("Nifty 50", 1.0), ("Nifty 500", 0.9)):
        c = idx_close * scale
        idx.append(pd.DataFrame({"index_name": name, "date": dates.date, "open": c, "high": c * 1.004,
                                 "low": c * 0.996, "close": c, "volume": None, "turnover_cr": None,
                                 "pe": 22.0, "pb": 3.5, "div_yield": 1.2}))
    syms = [m["symbol"] for m in members]
    # bulk/block deals: a few hundred, mixed institutional and other clients (names say DEMO)
    clients = ["DEMO MUTUAL FUND A/C DEMO FLEXICAP FUND", "DEMO LIFE INSURANCE CO. LTD", "DEMO GLOBAL FUND PLC",
               "DEMO SECURITIES PRIVATE LIMITED", "DEMO TRADING LLP", "RAMESH DEMO KUMAR"]
    n_deals = max(n_symbols * 4, 20)
    di = rng.integers(0, days, n_deals)
    si = rng.integers(0, n_symbols, n_deals)
    deals = pd.DataFrame({
        "date": dates.date[di], "kind": np.where(rng.random(n_deals) < 0.8, "bulk", "block"),
        "symbol": [syms[i] for i in si], "security_name": None,
        "client": rng.choice(clients, n_deals), "side": rng.choice(["BUY", "SELL"], n_deals),
        "qty": rng.integers(50_000, 2_000_000, n_deals).astype(float), "remarks": None})
    deals["price"] = [prices.loc[(prices.symbol == s_) & (prices.date == d_), "close"].iloc[0]
                      for s_, d_ in zip(deals["symbol"], deals["date"])]
    deals = deals.drop_duplicates(["date", "kind", "symbol", "client", "side"])
    # quarterly shareholding with the 21-day publication lag
    qtrs = [q.date() for q in pd.date_range(dates[0], dates[-1], freq="QE")]
    sh = []
    for s_ in syms:
        fpi, dii = rng.uniform(5, 30), rng.uniform(5, 25)
        promoter = rng.uniform(35, 70)
        for q in qtrs:
            fpi, dii = max(fpi + rng.normal(0, 0.8), 0.1), max(dii + rng.normal(0, 0.6), 0.1)
            pub = (pd.Timestamp(q) + pd.Timedelta(days=int(rng.integers(14, 22)))).date()
            for cat, v in (("promoter", promoter), ("fpi", fpi), ("dii", dii), ("public", max(100 - promoter - fpi - dii, 0))):
                sh.append({"symbol": s_, "quarter_end": q, "category": cat, "pct": round(v, 2), "shares": None,
                           "published_on": pub})
    # monthly MF holdings, two demo AMCs, published 10 days after month end
    months = [m.date() for m in pd.date_range(dates[0], dates[-1], freq="ME")]
    mf = []
    for s_ in syms[: max(n_symbols // 2, 1)]:
        base = rng.uniform(1e5, 5e6)
        for m in months:
            base *= np.exp(rng.normal(0, 0.06))
            for amc in ("DEMO AMC ONE", "DEMO AMC TWO"):
                mf.append({"month_end": m, "amc": amc, "scheme": f"{amc} EQUITY FUND", "symbol": s_,
                           "shares": round(base * rng.uniform(0.3, 0.7)), "market_value_cr": None,
                           "published_on": (pd.Timestamp(m) + pd.Timedelta(days=10)).date()})
    flows = pd.DataFrame([{"date": d_, "category": c, "buy_value": b, "sell_value": b - n, "net_value": n}
                          for d_, m_ in zip(dates.date, mkt) for c, b, n in
                          (("FII", 12000.0, round(m_ * 2e5 + rng.normal(0, 1500), 2)),
                           ("DII", 11000.0, round(-m_ * 1e5 + rng.normal(0, 1200), 2)))])
    # derivatives: the first ~third of symbols plus NIFTY have F&O; monthly expiries on the last Tuesday
    expiries = sorted({(m + pd.offsets.LastWeekOfMonth(weekday=1)).date() for m in pd.date_range(dates[0], dates[-1] + pd.Timedelta(days=62), freq="MS")})
    def near_exp(d_):
        return next(e for e in expiries if e >= d_)
    deriv = []
    fo_syms = syms[: max(n_symbols // 3, 1)]
    for s_ in fo_syms + ["NIFTY"]:
        if s_ == "NIFTY":
            px, kind, lot = idx_close, "index", 75
        else:
            px, kind, lot = prices.loc[prices.symbol == s_, "close"].to_numpy(), "stock", 500
        oi = rng.uniform(2e6, 2e7) * (5 if kind == "index" else 1)
        r_ = np.diff(np.log(px), prepend=np.log(px[0]))
        for k, d_ in enumerate(dates.date):
            oi *= np.exp(rng.normal(0, 0.03) + 2.0 * r_[k] * rng.choice([1, -1], p=[0.6, 0.4]))
            e = near_exp(d_)
            dte = (pd.Timestamp(e) - pd.Timestamp(d_)).days
            ce, pe = oi * rng.uniform(1.5, 3), oi * rng.uniform(1.0, 2.5)
            deriv.append({"date": d_, "symbol": s_, "kind": kind, "underlying": px[k], "near_expiry": e,
                          "days_to_expiry": dte, "fut_close": px[k] * (1 + 0.065 * dte / 365),
                          "fut_oi": round(oi), "fut_oi_change": None, "fut_volume": round(oi * 0.3),
                          "basis_pct": 6.5 * dte / 365, "rollover_pct": float(np.clip(100 - dte * 3.3, 5, 95)),
                          "ce_oi": round(ce), "pe_oi": round(pe), "pcr_oi": pe / ce, "ce_volume": None,
                          "pe_volume": None, "pcr_volume": None,
                          "max_ce_oi_strike": round(px[k] * 1.05, -1), "max_pe_oi_strike": round(px[k] * 0.95, -1),
                          "atm_strike": round(px[k], -1), "atm_iv": float(rng.uniform(12, 35)), "lot_size": lot})
    part = []
    fii_long = 0.4
    for d_ in dates.date:
        fii_long = float(np.clip(fii_long + rng.normal(0, 0.02), 0.1, 0.9))
        for ct, lp in (("FII", fii_long), ("DII", 0.6), ("Pro", 0.5), ("Client", 1 - fii_long)):
            tot = 4e5
            part.append({"date": d_, "client_type": ct, "fut_idx_long": tot * lp, "fut_idx_short": tot * (1 - lp),
                         "fut_stk_long": 1e6, "fut_stk_short": 1e6, "opt_idx_call_long": 5e5, "opt_idx_put_long": 5e5,
                         "opt_idx_call_short": 5e5, "opt_idx_put_short": 5e5, "opt_stk_call_long": 1e5,
                         "opt_stk_put_long": 1e5, "opt_stk_call_short": 1e5, "opt_stk_put_short": 1e5,
                         "total_long": 3e6, "total_short": 3e6})
    # quarterly fundamentals (₹ crore; shares in crore), published 30-45 days after quarter end
    fq = [q.date() for q in pd.date_range(dates[0] - pd.Timedelta(days=500), dates[-1], freq="QE")]
    fa = []
    for i_, s_ in enumerate(syms):
        last_close = prices.loc[prices.symbol == s_, "close"].iloc[-1]
        rev = float(rng.uniform(500, 20000)) / 4
        margin = float(rng.uniform(0.05, 0.25))
        shares = rev * 4 * margin * float(rng.uniform(12, 40)) / last_close  # crore shares → P/E ≈ 12-40
        growth = float(rng.normal(0.03, 0.03))
        equity = rev * 4 * rng.uniform(0.4, 1.5)
        debt = equity * rng.uniform(0, 1.2)
        fin = SECTORS[i_ % len(SECTORS)] == "Financial Services"
        for q in fq:
            rev *= np.exp(growth + rng.normal(0, 0.04))
            pat = rev * margin * np.exp(rng.normal(0, 0.15))
            equity += pat * 0.7
            pub = (pd.Timestamp(q) + pd.Timedelta(days=int(rng.integers(30, 46)))).date()
            vals = {"revenue": rev, "ebitda": rev * (margin + 0.08), "pat": pat, "eps": pat / shares,
                    "interest": debt * 0.02, "total_equity": equity, "total_debt": debt,
                    "cfo": pat * rng.uniform(0.6, 1.4), "shares": shares}
            if fin:
                vals.update(gnpa_pct=float(rng.uniform(0.5, 5)), nnpa_pct=float(rng.uniform(0.1, 2)))
            for k, v in vals.items():
                fa.append({"symbol": s_, "period_end": q, "metric": f"Q:{k}", "value": round(float(v), 4),
                           "published_on": pub})
    return {"prices": prices, "indices": pd.concat(idx, ignore_index=True), "members": pd.DataFrame(members),
            "fundamentals": pd.DataFrame(fa), "deals": deals, "shareholding": pd.DataFrame(sh), "mf": pd.DataFrame(mf), "fii_dii": flows,
            "deriv": pd.DataFrame(deriv), "participant_oi": pd.DataFrame(part)}


def seed(eng, data: dict | None = None) -> dict:
    data = data or make()
    src = db.record_source(eng, "demo", data["prices"]["date"].max(), "synthetic://intel.demo", None,
                           len(data["prices"]), "ok")
    db.replace_rows(eng, db.prices_daily, data["prices"].assign(source_id=src), [db.prices_daily.c.symbol.like("DEMO%")])
    db.replace_rows(eng, db.index_daily, data["indices"].assign(source_id=src),
                    [db.index_daily.c.index_name.in_(["Nifty 50", "Nifty 500"])])
    as_of = data["prices"]["date"].max()
    for name in ("nifty50", "nifty500"):
        m = data["members"] if name == "nifty500" else data["members"].head(50)
        db.replace_rows(eng, db.index_members, m.assign(index_name=name, as_of=as_of, source_id=src),
                        [db.index_members.c.index_name == name])
    if "deals" in data:
        db.replace_rows(eng, db.deals, data["deals"].assign(source_id=src), [db.deals.c.symbol.like("DEMO%")])
        db.replace_rows(eng, db.shareholding, data["shareholding"].assign(source_id=src),
                        [db.shareholding.c.symbol.like("DEMO%")])
        db.replace_rows(eng, db.mf_holdings, data["mf"].assign(source_id=src), [db.mf_holdings.c.amc.like("DEMO%")])
        db.replace_rows(eng, db.fii_dii_daily, data["fii_dii"].assign(source_id=src),
                        [db.fii_dii_daily.c.date.in_(list(data["fii_dii"]["date"].unique()))])
    if "fundamentals" in data:
        db.replace_rows(eng, db.fundamentals, data["fundamentals"].assign(source_id=src),
                        [db.fundamentals.c.symbol.like("DEMO%")])
    if "deriv" in data:
        db.replace_rows(eng, db.deriv_daily, data["deriv"].assign(source_id=src),
                        [db.deriv_daily.c.symbol.in_(list(data["deriv"]["symbol"].unique()))])
        db.replace_rows(eng, db.participant_oi, data["participant_oi"].assign(source_id=src),
                        [db.participant_oi.c.date.in_(list(data["participant_oi"]["date"].unique()))])
    return {k: len(v) for k, v in data.items()}


if __name__ == "__main__":
    import os
    from .config import DATA_DIR
    url = os.getenv("DATABASE_URL") or f"sqlite:///{DATA_DIR / 'demo.db'}"
    print(seed(db.engine(url)), "->", url)
