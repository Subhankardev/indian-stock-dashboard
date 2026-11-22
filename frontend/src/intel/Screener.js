import React, { useEffect, useState } from "react";
import { getJSON, fmt, pct, cls } from "./api";
import { DemoBanner, ErrorBox, Sources } from "./Common";

const COLS = [
  ["symbol", "Symbol"], ["industry", "Industry"], ["close", "Close"], ["ret_1d", "1D"],
  ["rs_rank", "RS rank"], ["rs_3m", "RS 3M"], ["rs_6m", "RS 6M"], ["rs_mom_20", "RS mom"],
  ["rvol20", "RVOL"], ["deliv_pct", "Deliv %"], ["deliv_pct_avg20", "Deliv 20D"], ["rsi14", "RSI"],
  ["adx", "ADX"], ["d_score", "Acc clues"], ["accumulation_score", "Acc score"], ["dist_52w_high_pct", "From 52W hi"], ["avg_value_cr", "Avg ₹cr/day"],
];

function cell(k, r) {
  const v = r[k];
  switch (k) {
    case "symbol": return <b>{v}</b>;
    case "industry": return <span className="muted">{v || "–"}</span>;
    case "ret_1d": return <span className={cls(v)}>{pct(v)}</span>;
    case "rs_3m": case "rs_6m": case "rs_mom_20": return <span className={cls(v)}>{pct(v)}</span>;
    case "dist_52w_high_pct": return pct(v);
    case "rs_rank": return <span className={v >= 80 ? "up" : v <= 20 ? "down" : ""}>{fmt(v, 0)}</span>;
    case "rvol20": return <span className={v >= 1.5 ? "up" : ""}>{fmt(v, 2)}×</span>;
    case "deliv_pct": case "deliv_pct_avg20": return fmt(v, 1);
    case "rsi14": case "adx": return fmt(v, 0);
    case "avg_value_cr": return fmt(v, 1);
    case "d_score": return v === null ? "–" : `${v}/5`;
    case "accumulation_score": return v === null ? <span className="muted" title="No accumulation component has passed its test">–</span> : fmt(v, 0);
    default: return fmt(v);
  }
}

export default function Screener({ onOpen }) {
  const [q, setQ] = useState({ liquid_only: true, sort: "rs_rank", industry: "", limit: 200 });
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    const p = new URLSearchParams({ liquid_only: q.liquid_only, sort: q.sort, limit: q.limit });
    if (q.industry) p.set("industry", q.industry);
    setErr(null);
    getJSON(`/api/intel/screener?${p}`).then(setD).catch(setErr);
  }, [q]);
  return (
    <>
      {d && <DemoBanner demo={d.demo} />}
      <div className="panel">
        <div className="panel-head">
          <h3>Screener · relative strength, volume & delivery</h3>
          <div className="controls">
            <label className="toggle-dark"><input type="checkbox" checked={q.liquid_only}
              onChange={(e) => setQ({ ...q, liquid_only: e.target.checked })} /> Liquid only</label>
            <select value={q.industry} onChange={(e) => setQ({ ...q, industry: e.target.value })}>
              <option value="">All industries</option>
              {(d?.industries || []).map((i) => <option key={i}>{i}</option>)}
            </select>
          </div>
        </div>
        <ErrorBox error={err && err.message} />
        {d && d.message && <div className="empty">{d.message}</div>}
        {d && d.rows.length > 0 && (
          <>
            <div className="muted small-text">Universe <b>{d.universe}</b> · showing {d.shown} of {d.total} stocks.
              Click a column to sort; click a row to open the stock. RS = return vs Nifty 50 in percentage points
              (not RSI). RVOL = volume ÷ previous 20-session average. Delivery % is from NSE's daily delivery data;
              high delivery alone does not prove institutional buying.</div>
            <div className="table-wrap">
              <table className="grid-table screener">
                <thead><tr>{COLS.map(([k, l]) => (
                  <th key={k} className={q.sort === k ? "sorted" : ""} onClick={() => k !== "symbol" && k !== "industry" && setQ({ ...q, sort: k })}>{l}</th>))}</tr></thead>
                <tbody>{d.rows.map((r) => (
                  <tr key={r.symbol} onClick={() => onOpen(r.symbol)} className="clickable">
                    {COLS.map(([k]) => <td key={k} className={k === "symbol" || k === "industry" ? "" : "num"}>{cell(k, r)}</td>)}
                  </tr>))}
                </tbody>
              </table>
            </div>
            <Sources sources={d.sources} asOf={d.as_of} />
          </>
        )}
      </div>
    </>
  );
}
