"""Parsers for NSE file formats. Each one was checked against a real published file
(copies live in intel/tests/fixtures). They return tidy DataFrames whose columns match
the tables in intel.db; validation errors raise ParseError with a reason.
"""
from __future__ import annotations

import io
import re
import zipfile
from typing import Union

import numpy as np
import pandas as pd

Raw = Union[bytes, str]


class ParseError(ValueError):
    pass


def _text(raw: Raw) -> str:
    if isinstance(raw, str):
        return raw
    if raw[:2] == b"PK":  # zipped CSV (UDiFF files are published as .csv.zip)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
            raw = z.read(name)
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ParseError("undecodable file")


def _clean_cols(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in df.columns]
    return df


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.strip().str.replace(",", "").replace({"-": None, "": None}),
                         errors="coerce")


def _require(df: pd.DataFrame, cols, what: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ParseError(f"{what}: missing columns {missing}; got {list(df.columns)[:12]}")


# --------------------------------------------------------------------------- #
# Cash market: sec_bhavdata_full_DDMMYYYY.csv (prices + delivery)
# --------------------------------------------------------------------------- #
def cm_delivery(raw: Raw, series_keep=("EQ", "BE", "BZ")) -> pd.DataFrame:
    df = _clean_cols(pd.read_csv(io.StringIO(_text(raw)), dtype=str, skipinitialspace=True))
    _require(df, ["SYMBOL", "SERIES", "DATE1", "CLOSE_PRICE", "TTL_TRD_QNTY", "DELIV_QTY"], "sec_bhavdata_full")
    df = df.apply(lambda s: s.str.strip() if s.dtype == object else s)
    if series_keep:
        df = df[df["SERIES"].isin(series_keep)]
    out = pd.DataFrame({
        "symbol": df["SYMBOL"], "series": df["SERIES"],
        "date": pd.to_datetime(df["DATE1"], format="%d-%b-%Y", errors="coerce").dt.date,
        "prev_close": _num(df["PREV_CLOSE"]), "open": _num(df["OPEN_PRICE"]), "high": _num(df["HIGH_PRICE"]),
        "low": _num(df["LOW_PRICE"]), "last": _num(df["LAST_PRICE"]), "close": _num(df["CLOSE_PRICE"]),
        "avg_price": _num(df["AVG_PRICE"]), "volume": _num(df["TTL_TRD_QNTY"]),
        "turnover_lacs": _num(df["TURNOVER_LACS"]), "trades": _num(df["NO_OF_TRADES"]),
        "deliv_qty": _num(df["DELIV_QTY"]), "deliv_pct": _num(df["DELIV_PER"]),
    }).dropna(subset=["date", "close"])
    bad = out[(out["high"] < out["low"]) | (out["deliv_qty"] > out["volume"] * 1.0001)]
    if len(bad) > max(5, 0.01 * len(out)):
        raise ParseError(f"sec_bhavdata_full: {len(bad)} rows fail sanity checks (high<low or delivery>volume)")
    return out.drop_duplicates(["symbol", "series", "date"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Indices: ind_close_all_DDMMYYYY.csv
# --------------------------------------------------------------------------- #
def index_close(raw: Raw) -> pd.DataFrame:
    df = _clean_cols(pd.read_csv(io.StringIO(_text(raw)), dtype=str))
    _require(df, ["Index Name", "Index Date", "Closing Index Value"], "ind_close_all")
    out = pd.DataFrame({
        "index_name": df["Index Name"].str.strip(),
        "date": pd.to_datetime(df["Index Date"].str.strip(), format="%d-%m-%Y", errors="coerce").dt.date,
        "open": _num(df["Open Index Value"]), "high": _num(df["High Index Value"]),
        "low": _num(df["Low Index Value"]), "close": _num(df["Closing Index Value"]),
        "volume": _num(df.get("Volume", pd.Series(dtype=str))),
        "turnover_cr": _num(df.get("Turnover (Rs. Cr.)", pd.Series(dtype=str))),
        "pe": _num(df.get("P/E", pd.Series(dtype=str))), "pb": _num(df.get("P/B", pd.Series(dtype=str))),
        "div_yield": _num(df.get("Div Yield", pd.Series(dtype=str))),
    }).dropna(subset=["date", "close"])
    return out.drop_duplicates(["index_name", "date"]).reset_index(drop=True)  # NSE repeats "Nifty 50"


# --------------------------------------------------------------------------- #
# Index constituents: ind_nifty500list.csv etc.
# --------------------------------------------------------------------------- #
def index_list(raw: Raw) -> pd.DataFrame:
    df = _clean_cols(pd.read_csv(io.StringIO(_text(raw)), dtype=str))
    _require(df, ["Symbol"], "index list")
    return pd.DataFrame({
        "symbol": df["Symbol"].str.strip(),
        "company": df.get("Company Name", pd.Series([None] * len(df))),
        "industry": df.get("Industry", pd.Series([None] * len(df))),
        "isin": df.get("ISIN Code", pd.Series([None] * len(df))),
    }).dropna(subset=["symbol"]).drop_duplicates("symbol").reset_index(drop=True)


# --------------------------------------------------------------------------- #
# F&O bhavcopy (UDiFF): BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv(.zip)
# --------------------------------------------------------------------------- #
def fo_udiff(raw: Raw) -> pd.DataFrame:
    df = _clean_cols(pd.read_csv(io.StringIO(_text(raw)), dtype=str))
    _require(df, ["TradDt", "FinInstrmTp", "TckrSymb", "XpryDt", "ClsPric", "OpnIntrst"], "UDiFF F&O bhavcopy")
    out = pd.DataFrame({
        "date": pd.to_datetime(df["TradDt"], errors="coerce").dt.date,
        "instrument": df["FinInstrmTp"].str.strip(),
        "symbol": df["TckrSymb"].str.strip(),
        "expiry": pd.to_datetime(df["XpryDt"], errors="coerce").dt.date,
        "strike": _num(df["StrkPric"]).fillna(0.0),
        "option_type": df["OptnTp"].fillna("").str.strip(),
        "open": _num(df["OpnPric"]), "high": _num(df["HghPric"]), "low": _num(df["LwPric"]),
        "close": _num(df["ClsPric"]), "settle": _num(df["SttlmPric"]), "underlying": _num(df["UndrlygPric"]),
        "oi": _num(df["OpnIntrst"]), "oi_change": _num(df["ChngInOpnIntrst"]),
        "volume": _num(df["TtlTradgVol"]), "value": _num(df["TtlTrfVal"]),
        "lot_size": _num(df["NewBrdLotQty"]),
    }).dropna(subset=["date", "expiry"])
    out = out[out["instrument"].isin(["STF", "IDF", "STO", "IDO"])]
    return out.drop_duplicates(["date", "instrument", "symbol", "expiry", "strike", "option_type"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Participant-wise OI: fao_participant_oi_DDMMYYYY.csv
# --------------------------------------------------------------------------- #
_POI_COLS = {
    "Future Index Long": "fut_idx_long", "Future Index Short": "fut_idx_short",
    "Future Stock Long": "fut_stk_long", "Future Stock Short": "fut_stk_short",
    "Option Index Call Long": "opt_idx_call_long", "Option Index Put Long": "opt_idx_put_long",
    "Option Index Call Short": "opt_idx_call_short", "Option Index Put Short": "opt_idx_put_short",
    "Option Stock Call Long": "opt_stk_call_long", "Option Stock Put Long": "opt_stk_put_long",
    "Option Stock Call Short": "opt_stk_call_short", "Option Stock Put Short": "opt_stk_put_short",
    "Total Long Contracts": "total_long", "Total Short Contracts": "total_short",
}


def participant_oi(raw: Raw) -> pd.DataFrame:
    text = _text(raw)
    lines = text.splitlines()
    m = re.search(r"as on\s+([A-Za-z]{3}\s+\d{1,2}\s*,\s*\d{4})", lines[0] if lines else "")
    day = pd.to_datetime(m.group(1).replace(" ", ""), format="%b%d,%Y").date() if m else None
    start = next((i for i, l in enumerate(lines) if l.lower().startswith("client type")), None)
    if start is None:
        raise ParseError("participant OI: header row 'Client Type' not found")
    df = _clean_cols(pd.read_csv(io.StringIO("\n".join(lines[start:])), dtype=str))
    _require(df, ["Client Type"] + list(_POI_COLS), "participant OI")
    out = pd.DataFrame({"client_type": df["Client Type"].str.strip()})
    for src, dst in _POI_COLS.items():
        out[dst] = _num(df[src])
    out["date"] = day
    return out.dropna(subset=["client_type"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Bulk / block deals: bulk.csv, block.csv
# --------------------------------------------------------------------------- #
def deals(raw: Raw, kind: str) -> pd.DataFrame:
    df = _clean_cols(pd.read_csv(io.StringIO(_text(raw)), dtype=str))
    _require(df, ["Date", "Symbol", "Client Name", "Buy/Sell", "Quantity Traded"], f"{kind} deals")
    df = df[~df["Date"].astype(str).str.upper().str.contains("NO RECORDS")]
    price_col = next((c for c in df.columns if c.startswith("Trade Price")), None)
    out = pd.DataFrame({
        "date": pd.to_datetime(df["Date"].str.strip(), format="%d-%b-%Y", errors="coerce").dt.date,
        "kind": kind,
        "symbol": df["Symbol"].str.strip(),
        "security_name": df.get("Security Name", pd.Series([None] * len(df))),
        "client": df["Client Name"].str.strip().str.replace(r"\s+", " ", regex=True),
        "side": df["Buy/Sell"].str.strip().str.upper(),
        "qty": _num(df["Quantity Traded"]),
        "price": _num(df[price_col]) if price_col else np.nan,
        "remarks": df.get("Remarks", pd.Series([None] * len(df))),
    }).dropna(subset=["date", "symbol", "client"])
    return out[out["side"].isin(["BUY", "SELL"])].reset_index(drop=True)


# --------------------------------------------------------------------------- #
# FII/DII cash flows: /api/fiidiiTradeReact JSON
# --------------------------------------------------------------------------- #
def fii_dii(rows: list) -> pd.DataFrame:
    recs = []
    for r in rows or []:
        cat = str(r.get("category", "")).upper()
        cat = "FII" if cat.startswith("FII") or cat.startswith("FPI") else ("DII" if cat.startswith("DII") else None)
        if not cat:
            continue
        recs.append({
            "date": pd.to_datetime(str(r.get("date", "")).strip(), format="%d-%b-%Y", errors="coerce"),
            "category": cat,
            "buy_value": _num(pd.Series([r.get("buyValue")])).iloc[0],
            "sell_value": _num(pd.Series([r.get("sellValue")])).iloc[0],
            "net_value": _num(pd.Series([r.get("netValue")])).iloc[0],
        })
    df = pd.DataFrame(recs, columns=["date", "category", "buy_value", "sell_value", "net_value"])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.dropna(subset=["date"]).reset_index(drop=True)
