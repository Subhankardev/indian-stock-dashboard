"""Research assistant grounded in the database (Phase 9).

Claude answers questions using **read-only tools over this system's own data** — screener,
stock evidence and scores, rankings, institutional activity, derivatives, research results,
backtests, paper trades and data freshness. It does not browse the web, has no access beyond
these tools, and cannot place orders.

Model: Claude Opus 5.5 through the Anthropic Messages API, in a tool-use loop hosted by this
backend. Needs ``ANTHROPIC_API_KEY`` in ``backend/.env``.

Grounding rules (system prompt): every number must come from a tool result in this
conversation, with its date and evidence class; inferred activity is never called
institutional buying; market-wide data is never attributed to a stock; untested signals are
labelled as such; demo data is called out; no investment advice.
"""
from __future__ import annotations

import json
import math
from datetime import date
from typing import Any, Callable, Optional

import pandas as pd

from . import db
from .config import load

MODEL = "claude-opus-5-5"
MAX_TOOL_ROUNDS = 12
MAX_RESULT_CHARS = 12000

SYSTEM_PROMPT = """You are the research assistant inside an Indian equity (NSE) research system. You help one user understand what the data in this system says. Answer from tool results only.

How to answer:
- Use the tools to fetch facts before answering. Every number, ranking or claim about a stock must come from a tool result in this conversation. If the tools don't have it, say so plainly and suggest which import or command would add the data (for example python -m intel.ingest, python -m intel.imports, python -m intel.research).
- Give dates. Say the session date ("as of") of the data you used, and say when data looks stale.
- Keep evidence classes straight, and name the class when you cite evidence:
  A = confirmed disclosed transaction (bulk/block deal by a classified institution);
  B = ownership change from filings, usable only from its publication date;
  C = market-wide (FII/DII flows, participant OI) — never attribute it to a single stock;
  D = possible accumulation/distribution inferred from price, volume and delivery — call it "possible", never "institutional buying";
  E = large activity with unknown identity or direction;
  F = futures positioning from price and open interest — it does not show who traded.
- High delivery, high volume or rising open interest alone is not evidence that institutions are buying.
- Signals and scoring factors count only after they pass their event-study test. If something has not passed (or has not been run), say so. Unvalidated preview scores must be labelled as unvalidated.
- If the data is synthetic demo data (tools report demo: true), say so at the start of the answer.
- Backtest results describe the past on this data, after costs; they are not forecasts.
- This is research, not investment advice. Don't tell the user to buy or sell. You may describe what the system's rules and evidence show, including trade setups for paper trading.

Style: lead with the direct answer, then the supporting figures in a compact list or table, then caveats. Be concise; use INR (₹) and the Indian number format the tools return. Don't repeat raw JSON."""


def _clean(v):
    if v is None:
        return None
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else round(v, 4)
    if isinstance(v, (pd.Timestamp, date)):
        return str(pd.Timestamp(v).date())
    if hasattr(v, "item"):
        return _clean(v.item())
    return v


def _rows(df: pd.DataFrame, cols: Optional[list] = None, limit: int = 50) -> list[dict]:
    if df is None or len(df) == 0:
        return []
    if cols:
        df = df[[c for c in cols if c in df.columns]]
    return [{k: _clean(v) for k, v in r.items()} for r in df.head(limit).to_dict("records")]


# ------------------------------------------------------------------- tools --- #
def _obj(props: dict, required: list) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


TOOLS: list[dict] = [
    {"name": "data_status",
     "description": "Data freshness per dataset (latest session, files), whether the database holds demo data, recent "
                    "download errors and the active universe/liquidity settings. Call first if dates matter.",
     "input_schema": _obj({}, [])},
    {"name": "market_overview",
     "description": "Market-wide context (class C): regime label with reasons, breadth (% above 50/200-DMA, "
                    "advancers/decliners, new highs/lows), sector strength table, latest FII/DII flows.",
     "input_schema": _obj({}, [])},
    {"name": "screen_stocks",
     "description": "Latest-session screener over the liquid universe. Sort by a column and optionally filter by industry. "
                    "Columns include rs_rank, rs_3m, rs_6m, rvol20, deliv_pct, deliv_pct_avg20, rsi14, adx, d_score "
                    "(class-D accumulation clues 0-5), x_score (distribution clues), pe, quality_score, value_score, "
                    "dist_52w_high_pct, avg_value_cr.",
     "input_schema": _obj({"sort_by": {"type": "string", "description": "column to sort descending, e.g. rs_rank"},
                           "ascending": {"type": "boolean"},
                           "industry": {"anyOf": [{"type": "string"}, {"type": "null"}], "description": "exact industry name or null"},
                           "limit": {"type": "integer", "description": "1-50"}},
                          ["sort_by", "ascending", "industry", "limit"])},
    {"name": "stock_report",
     "description": "Everything known about one NSE symbol on the latest session: price, trend, relative strength, "
                    "volume/delivery, accumulation/distribution evidence with classes, institutional deals, "
                    "shareholding changes, futures data, VWAP/anchored VWAP, fundamentals, factor percentiles and "
                    "score (only tested factors), conflicts, a paper-trade setup, and the source file of the price row.",
     "input_schema": _obj({"symbol": {"type": "string", "description": "NSE symbol, e.g. RELIANCE"}}, ["symbol"])},
    {"name": "rankings",
     "description": "Bullish and bearish rankings from factors that passed their tests (empty if none passed). "
                    "preview=true uses every factor and is UNVALIDATED. Also returns special situations.",
     "input_schema": _obj({"preview": {"type": "boolean"}, "limit": {"type": "integer", "description": "1-30"}},
                          ["preview", "limit"])},
    {"name": "institutional_activity",
     "description": "Bulk/block deals with client entity class and evidence class (A institutional / E other) over the "
                    "last N days, optionally for one symbol; plus the stocks with the most accumulation and "
                    "distribution evidence today.",
     "input_schema": _obj({"symbol": {"anyOf": [{"type": "string"}, {"type": "null"}]}, "days": {"type": "integer", "description": "1-365"}},
                          ["symbol", "days"])},
    {"name": "derivatives_overview",
     "description": "Participant-wise index-futures positioning (market-wide, class C), NIFTY PCR/IV/max-OI strikes, and "
                    "today's stock futures build-ups (class F).",
     "input_schema": _obj({}, [])},
    {"name": "research_results",
     "description": "Event-study results per signal (events, hit rate, 20-day excess return after costs, 95% range, "
                    "sub-periods, by year/regime, PASS/fail with reason). Optionally one signal.",
     "input_schema": _obj({"signal": {"anyOf": [{"type": "string"}, {"type": "null"}]}}, ["signal"])},
    {"name": "backtests",
     "description": "Saved portfolio backtests (list), or one run's full metrics vs Nifty 50/500 when run_id is given.",
     "input_schema": _obj({"run_id": {"anyOf": [{"type": "integer"}, {"type": "null"}]}}, ["run_id"])},
    {"name": "paper_trading",
     "description": "Paper-trade log and performance (simulated only, after costs).",
     "input_schema": _obj({}, [])},
]


class Tools:
    """Read-only tool implementations over the database (panel is the enriched research panel)."""

    def __init__(self, eng, panel_loader: Callable[[], pd.DataFrame]):
        self.eng = eng
        self._panel_loader = panel_loader
        self._panel: Optional[pd.DataFrame] = None

    @property
    def panel(self) -> pd.DataFrame:
        if self._panel is None:
            self._panel = self._panel_loader()
        return self._panel

    def _demo(self) -> bool:
        from sqlalchemy import func, select
        with self.eng.connect() as c:
            return bool(c.execute(select(func.count()).select_from(db.source_files)
                                  .where(db.source_files.c.dataset == "demo")).scalar())

    def run(self, name: str, args: dict) -> dict:
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            raise ValueError(f"unknown tool {name}")
        out = fn(**args)
        out.setdefault("demo", self._demo())
        return out

    # each tool returns a JSON-serialisable dict
    def t_data_status(self) -> dict:
        cfg = load()
        fr = db.dataset_freshness(self.eng)
        return {"datasets": _rows(fr), "universe": cfg["universe"], "liquidity": cfg["liquidity"],
                "today": str(date.today())}

    def t_market_overview(self) -> dict:
        from . import evidence, market
        p = self.panel
        if p.empty:
            return {"error": "no price data"}
        br = market.breadth(p, days=5)
        return {"regime": market.regime(self.eng, p), "breadth_last_days": _rows(br),
                "sectors": _rows(market.sector_table(p), ["industry", "sector_stocks", "sector_rs_6m", "sector_rs_3m",
                                                          "sector_pct_above_200dma", "sector_rank_pct", "rank_change_20d"]),
                "fii_dii_last": _rows(evidence.market_flows(self.eng, days=5)), "evidence_class": "C"}

    def t_screen_stocks(self, sort_by: str, ascending: bool, industry: Optional[str], limit: int) -> dict:
        from .panel import latest_snapshot
        p = self.panel
        if p.empty:
            return {"error": "no price data"}
        limit = max(1, min(int(limit), 50))
        snap = latest_snapshot(p)
        snap = snap[snap["liquid"].fillna(False).astype(bool)]
        if industry:
            snap = snap[snap["industry"] == industry]
        if sort_by not in snap.columns:
            return {"error": f"unknown column {sort_by}"}
        snap = snap.sort_values(sort_by, ascending=ascending, na_position="last")
        cols = ["symbol", "industry", "close", "ret_1d", "rs_rank", "rs_3m", "rs_6m", "rvol20", "deliv_pct",
                "deliv_pct_avg20", "rsi14", "adx", "d_score", "x_score", "pe", "quality_score", "value_score",
                "dist_52w_high_pct", "avg_value_cr"]
        return {"as_of": _clean(snap["date"].iloc[0]) if len(snap) else None, "liquid_stocks": int(len(snap)),
                "rows": _rows(snap, cols, limit)}

    def t_stock_report(self, symbol: str) -> dict:
        from . import evidence, scoring
        from .setups import setup_for
        sym = symbol.upper().strip()
        p = self.panel
        g = p[p["symbol"] == sym] if not p.empty else p
        if g.empty:
            return {"error": f"no data for {sym}"}
        r = g.iloc[-1]
        cfg = load()
        active = scoring.active_factors(self.eng)
        sc = {side: {k: _clean(v) for k, v in scoring.score(g.iloc[[-1]], active, cfg["scoring"]["weights"], side).iloc[0].items()}
              for side in ("bullish", "bearish")}
        keys = ["date", "industry", "close", "ret_1d", "dma50", "dma200", "rs_rank", "rs_1m", "rs_3m", "rs_6m",
                "rs_mom_20", "rsi14", "adx", "rvol20", "deliv_pct", "deliv_pct_avg20", "atr_pct", "dist_52w_high_pct",
                "avg_value_cr", "liquid", "d_score", "x_score", "f_buildup", "f_oi_chg_pct", "f_basis_pct", "f_pcr_oi",
                "f_atm_iv", "dist_avwap_hi52_pct", "dist_avwap_lo52_pct", "dist_avwap_volspike60_pct", "pe", "pb",
                "mcap_cr", "roe_pct", "de_ratio", "rev_growth_pct", "pat_growth_pct", "quality_score", "value_score",
                "fa_effective", "fa_financial"]
        deals = evidence.deals_classified(self.eng, symbol=sym)
        own = evidence.ownership_changes(self.eng, sym)
        return {
            "symbol": sym, "latest": {k: _clean(r.get(k)) for k in keys if k in g},
            "evidence_items": evidence.stock_evidence(self.eng, r),
            "factor_percentiles": {f: {"bullish": _clean(r.get(f"p_{f}")), "bearish": _clean(r.get(f"q_{f}")),
                                       "active": f in active} for f in scoring.FACTORS},
            "scores_from_tested_factors": sc, "active_factors": sorted(active), "conflicts": scoring.conflicts(r),
            "deals": _rows(deals, ["date", "kind", "client", "side", "qty", "price", "value_cr", "entity_class",
                                   "method", "evidence_class"], 10),
            "shareholding": _rows(own.tail(4) if len(own) else own),
            "paper_setup_long": setup_for(r, g, "bullish", cfg),
            "price_history_20d": _rows(g.tail(20), ["date", "close", "volume", "deliv_pct"], 20),
        }

    def t_rankings(self, preview: bool, limit: int) -> dict:
        from .scoring import rankings
        p = self.panel
        if p.empty:
            return {"error": "no price data"}
        res = rankings(self.eng, p, preview=preview, limit=max(1, min(int(limit), 30)))
        res["label"] = "UNVALIDATED preview — all factors on" if preview else "tested factors only"
        return res

    def t_institutional_activity(self, symbol: Optional[str], days: int) -> dict:
        from . import evidence
        from .panel import latest_snapshot
        p = self.panel
        days = max(1, min(int(days), 365))
        last = p["date"].max() if not p.empty else pd.Timestamp(date.today())
        d = evidence.deals_classified(self.eng, start=(last - pd.Timedelta(days=days)).date(),
                                      symbol=symbol.upper() if symbol else None)
        out = {"deals": _rows(d, ["date", "kind", "symbol", "client", "side", "qty", "price", "value_cr", "entity_class",
                                  "method", "evidence_class"], 60), "deal_count": int(len(d))}
        if not p.empty and "d_score" in p:
            snap = latest_snapshot(p)
            snap = snap[snap["liquid"].fillna(False).astype(bool)]
            out["most_accumulation_clues"] = _rows(snap.sort_values("d_score", ascending=False), ["symbol", "d_score", "x_score", "close"], 10)
            out["most_distribution_clues"] = _rows(snap.sort_values("x_score", ascending=False), ["symbol", "x_score", "d_score", "close"], 10)
            out["as_of"] = _clean(last)
        return out

    def t_derivatives_overview(self) -> dict:
        from . import derivatives as dv
        from .panel import latest_snapshot
        part = dv.participant_summary(self.eng, days=5)
        idx = dv.index_summary(self.eng, days=5)
        out = {"participants_last_days": _rows(part, ["date", "client_type", "idx_fut_long_pct", "idx_fut_net", "idx_fut_net_chg"]),
               "indices": _rows(idx, ["date", "symbol", "pcr_oi", "atm_iv", "max_ce_oi_strike", "max_pe_oi_strike", "fut_oi"]),
               "note": "participants = class C (market-wide); build-ups = class F (positioning, not who traded)"}
        p = self.panel
        if not p.empty and "f_buildup" in p:
            snap = latest_snapshot(p)
            fo = snap[snap["f_has_fo"].fillna(False).astype(bool)]
            out["buildups_today"] = {b: _rows(fo[fo["f_buildup"] == b].sort_values("f_oi_chg_pct", ascending=False),
                                              ["symbol", "f_ret_pct", "f_oi_chg_pct", "f_pcr_oi"], 8)
                                     for b in dv.BUILDUPS}
        return out

    def t_research_results(self, signal: Optional[str]) -> dict:
        from sqlalchemy import func, select
        from .research.signals import EVIDENCE_CLASS, SIGNALS
        latest = (select(db.signal_tests.c.signal_name, func.max(db.signal_tests.c.id).label("id"))
                  .group_by(db.signal_tests.c.signal_name).subquery())
        with self.eng.connect() as c:
            rows = c.execute(select(db.signal_tests).join(latest, db.signal_tests.c.id == latest.c.id)).all()
        h = load()["research"]["pass_horizon"]
        out = []
        for r in rows:
            m = dict(r._mapping)
            if signal and m["signal_name"] != signal:
                continue
            s = json.loads(m["stats"])
            hz = (s.get("horizons") or {}).get(str(h)) or {}
            out.append({"signal": m["signal_name"], "class": EVIDENCE_CLASS.get(m["signal_name"]),
                        "run_at": str(m["run_at"]), "period": m["period"], "passed": bool(m["passed"]),
                        "verdict": s.get("verdict"), "events": s.get("events"),
                        f"h{h}": {k: hz.get(k) for k in ("n", "hit_rate", "day_mean_pct", "ci95", "bootstrap_ci95",
                                                          "subperiod_means", "by_year", "by_regime", "top_symbol_share")}})
        never = sorted(set(SIGNALS) - {o["signal"] for o in out}) if not signal else []
        return {"results": out, "never_run": never, "rules": load()["research"]}

    def t_backtests(self, run_id: Optional[int]) -> dict:
        from .backtest import get_run, list_runs
        if run_id is None:
            return {"runs": list_runs(self.eng, 20)}
        r = get_run(self.eng, int(run_id))
        if r is None:
            return {"error": f"no backtest {run_id}"}
        r.pop("series", None)
        return r

    def t_paper_trading(self) -> dict:
        from .paper import report, trades
        t = trades(self.eng)
        return {"report": report(self.eng), "recent": _rows(t.drop(columns=["notes"], errors="ignore").tail(20)),
                "note": "simulated only; no orders are placed"}


def _result_text(obj: Any) -> str:
    s = json.dumps(obj, default=str, ensure_ascii=False)
    if len(s) > MAX_RESULT_CHARS:
        s = s[:MAX_RESULT_CHARS] + ' …[truncated: narrow the request, e.g. fewer rows]"'
    return s


def _client():
    import anthropic
    return anthropic.Anthropic()


def ask(question: str, history: Optional[list[dict]] = None, tools: Optional[Tools] = None, client=None,
        effort: str = "high") -> dict:
    """Answer ``question`` with a tool-use loop. ``history`` = prior [{role, content}] text turns.

    Returns {answer, tool_calls: [{name, input, ok}], usage, model, stop_reason}.
    """
    client = client or _client()
    messages: list = [{"role": m["role"], "content": m["content"]} for m in (history or [])
                      if m.get("role") in ("user", "assistant") and m.get("content")]
    messages.append({"role": "user", "content": question})
    tool_defs = [{**t, "strict": True} for t in TOOLS]
    calls: list[dict] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    response = None
    for _ in range(MAX_TOOL_ROUNDS):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            tools=tool_defs,
            messages=messages,
            cache_control={"type": "ephemeral"},          # also cache the growing conversation prefix
            output_config={"effort": effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",                           # re-run a safety decline on the recommended model
        )
        u = getattr(response, "usage", None)
        for k in usage:
            usage[k] += int(getattr(u, k, 0) or 0)
        if response.stop_reason == "refusal":
            break
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason == "pause_turn":
            continue
        if response.stop_reason != "tool_use":
            break
        results = []
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            try:
                out = tools.run(block.name, dict(block.input or {}))
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": _result_text(out)})
                calls.append({"name": block.name, "input": dict(block.input or {}), "ok": True})
            except Exception as exc:  # report the failure to the model instead of crashing the loop
                results.append({"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                                "content": f"Tool error: {exc}"})
                calls.append({"name": block.name, "input": dict(block.input or {}), "ok": False, "error": str(exc)})
        messages.append({"role": "user", "content": results})
    if response is None:
        raise RuntimeError("no response")
    if response.stop_reason == "refusal":
        det = getattr(response, "stop_details", None)
        answer = ("The model declined to answer this request"
                  + (f" ({det.category})" if det is not None and getattr(det, "category", None) else "") + ".")
    else:
        answer = "\n".join(b.text for b in response.content if getattr(b, "type", None) == "text").strip()
        if response.stop_reason == "tool_use":
            answer = (answer + "\n\n" if answer else "") + "(Stopped after the maximum number of tool calls; ask a narrower question.)"
        elif response.stop_reason == "max_tokens":
            answer += "\n\n(Answer cut off at the length limit.)"
    return {"answer": answer, "tool_calls": calls, "usage": usage, "model": getattr(response, "model", MODEL),
            "stop_reason": response.stop_reason}


def configured() -> bool:
    import os
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
