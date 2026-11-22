"""Alerts (Phase 7): stored in ``intel_alerts`` and optionally sent to Telegram.

Kinds: special (special situations), ranking_entry (new to the top-N bullish/bearish,
tested factors only), paper_open / paper_fill / paper_exit, stale_data, test.

    python -m intel.alerts --run         # generate today's alerts and send unsent ones
    python -m intel.alerts --test        # send a test message

Telegram uses TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID from backend/.env (see README).
"""
from __future__ import annotations

import argparse
import html
import json
from datetime import date, datetime, timezone
from typing import Optional

import pandas as pd
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from . import db
from .config import load

ICON = {"bullish": "🟢", "bearish": "🔴"}


def add(eng, kind: str, symbol: Optional[str], direction: Optional[str], day, title: str, detail: str = "",
        data: Optional[dict] = None) -> bool:
    """Store an alert; duplicates (same day, kind, symbol, title) are ignored. True if new."""
    try:
        with eng.begin() as c:
            c.execute(db.intel_alerts.insert().values(
                created=datetime.now(timezone.utc).replace(tzinfo=None), date=day, kind=kind, symbol=symbol,
                direction=direction, title=title, detail=detail, data=json.dumps(data or {}, default=str), sent=0))
        return True
    except IntegrityError:
        return False


def generate(eng, panel: pd.DataFrame, cfg: Optional[dict] = None) -> dict:
    from .scoring import FACTORS, active_factors, score, special_situations
    cfg = cfg or load()
    a = cfg["alerts"]
    made = {"special": 0, "ranking_entry": 0, "stale_data": 0}
    if panel.empty:
        return made
    last = panel["date"].max()
    day = pd.Timestamp(last).date()
    snap = panel[(panel["date"] == last) & panel["liquid"].fillna(False).astype(bool)]
    for s in special_situations(snap):
        made["special"] += add(eng, "special", s["symbol"], s["direction"], day, f"{s['symbol']}: {s['description']}",
                               f"close {s['close']:.2f} · {s.get('industry') or ''}", s)
    active = active_factors(eng)
    if active:
        dates = sorted(panel["date"].unique())
        if len(dates) >= 2:
            prev = panel[(panel["date"] == dates[-2]) & panel["liquid"].fillna(False).astype(bool)]
            w, n, mc = cfg["scoring"]["weights"], a["top_n"], cfg["scoring"]["min_confidence"]
            for side in ("bullish", "bearish"):
                def top(df):
                    s = score(df, active, w, side).join(df[["symbol"]])
                    s = s[s[f"{side}_confidence"] >= mc]
                    return s.sort_values(f"{side}_score", ascending=False).head(n)
                now, before = top(snap), set(top(prev)["symbol"])
                for _, r in now.iterrows():
                    if r["symbol"] not in before:
                        made["ranking_entry"] += add(
                            eng, "ranking_entry", r["symbol"], side, day,
                            f"{r['symbol']} entered the top {n} {side}",
                            f"score {r[f'{side}_score']:.0f} · confidence {r[f'{side}_confidence']:.0%} · "
                            f"active factors: {', '.join(sorted(active & set(FACTORS)))}")
    fresh = db.dataset_freshness(eng)
    cm = fresh[fresh["dataset"].isin(["cm", "demo"])]
    if len(cm):
        latest = pd.Timestamp(cm["latest"].max())
        age = len(pd.bdate_range(latest, pd.Timestamp(date.today()))) - 1
        if age > a["stale_days"]:
            made["stale_data"] += add(eng, "stale_data", None, None, date.today(),
                                      f"Price data is {age} sessions old (latest {latest.date()})",
                                      "Run: python -m intel.ingest --today")
    return made


def to_html(r: dict) -> str:
    e = html.escape
    head = f"{ICON.get(r.get('direction'), '🔔')} <b>{e(r['title'])}</b>"
    lines = [head]
    if r.get("detail"):
        lines.append(e(r["detail"]))
    lines.append(f"<i>{e(r['kind'])} · {e(str(r['date']))} · research/paper only, not advice</i>")
    return "\n".join(lines)


def send_pending(eng, notifier=None, cfg: Optional[dict] = None) -> dict:
    from app.alerts.telegram import TelegramNotifier
    cfg = cfg or load()
    n = notifier or TelegramNotifier.from_env()
    if not n.configured:
        return {"sent": 0, "skipped": "Telegram not configured"}
    kinds = set(cfg["alerts"]["kinds"]) | {"test"}
    rows = db.read_sql(eng, select(db.intel_alerts).where(db.intel_alerts.c.sent == 0).order_by(db.intel_alerts.c.id))
    sent = failed = 0
    for _, r in rows.iterrows():
        if r["kind"] not in kinds:
            continue
        try:
            n.send_now({"html": to_html(r.to_dict())})
        except Exception:
            failed += 1
            continue
        with eng.begin() as c:
            c.execute(update(db.intel_alerts).where(db.intel_alerts.c.id == int(r["id"])).values(sent=1))
        sent += 1
    return {"sent": sent, "failed": failed, "status": n.info()}


def recent(eng, limit: int = 200) -> pd.DataFrame:
    return db.read_sql(eng, select(db.intel_alerts).order_by(db.intel_alerts.c.id.desc()).limit(limit))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--test", action="store_true")
    a = ap.parse_args(argv)
    eng = db.engine()
    if a.test:
        add(eng, "test", None, None, date.today(), f"Test alert {datetime.now():%H:%M:%S}", "Telegram alerts work.")
    if a.run:
        from .panel import build_panel
        from .scoring import enrich
        print("Generated:", generate(eng, enrich(eng, build_panel(eng))))
    print("Telegram:", send_pending(eng))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
