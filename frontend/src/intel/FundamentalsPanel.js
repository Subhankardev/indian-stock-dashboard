import React, { useEffect, useState } from "react";
import { getJSON, fmt, pct } from "./api";

function Item({ label, v, unit = "", d = 1 }) {
  return <div className="card"><div className="muted small-text">{label}</div><div className="big">{v === null || v === undefined ? "–" : `${fmt(v, d)}${unit}`}</div></div>;
}

export default function FundamentalsPanel({ symbol, onOpen }) {
  const [d, setD] = useState(null);
  useEffect(() => { setD(null); getJSON(`/api/intel/stock/${encodeURIComponent(symbol)}/fundamentals`).then(setD).catch(() => setD(false)); }, [symbol]);
  if (!d) return null;
  const L = d.latest;
  if (!L.fa_effective) {
    return (
      <div className="panel"><div className="panel-head"><h3>Fundamentals</h3></div>
        <div className="muted small-text">No fundamentals imported for {symbol}. Import results with
          <code> python -m intel.imports fundamentals file.csv</code> (symbol, period_end, period_type, metric, value[, published_on]).</div></div>);
  }
  return (
    <div className="panel">
      <div className="panel-head"><h3>Fundamentals & valuation</h3>
        <span className="muted small-text">{L.fa_basis === "ttm" ? "trailing 4 quarters" : "latest annual"} · known since {L.fa_effective}
          {L.fa_financial ? " · financial: leverage/margins excluded" : ""}{L.fa_sector_relative ? " · scored vs industry peers" : " · scored vs universe (too few peers)"}</span></div>
      <div className="cards">
        <Item label="Quality score (0–100)" v={L.quality_score} d={0} />
        <Item label="Value score (0–100)" v={L.value_score} d={0} />
        <Item label="P/E (TTM)" v={L.pe} />
        <Item label="P/B" v={L.pb} />
        <Item label="Market cap" v={L.mcap_cr} unit=" cr" d={0} />
        <Item label="ROE" v={L.roe_pct} unit="%" />
        {!L.fa_financial && <Item label="ROCE" v={L.roce_pct} unit="%" />}
        {!L.fa_financial && <Item label="Debt / equity" v={L.de_ratio} d={2} />}
        <Item label="Revenue growth (YoY TTM)" v={L.rev_growth_pct} unit="%" />
        <Item label="Profit growth (YoY TTM)" v={L.pat_growth_pct} unit="%" />
        {!L.fa_financial && <Item label="Cash from ops / profit" v={L.cfo_to_pat} d={2} />}
        {L.fa_financial && <Item label="Gross NPA" v={L.gnpa_pct} unit="%" d={2} />}
      </div>
      <div className="two-col">
        <div>
          <div className="section-label">History (as published)</div>
          <table className="grid-table"><thead><tr><th>Known from</th><th>Period</th><th className="num">Revenue TTM</th>
            <th className="num">Profit TTM</th><th className="num">ROE</th><th className="num">Rev growth</th></tr></thead>
            <tbody>{[...d.history].reverse().map((h) => (
              <tr key={h.effective}><td>{h.effective}</td><td>{h.period_end}</td><td className="num">{fmt(h.ttm_revenue, 0)}</td>
                <td className="num">{fmt(h.ttm_pat, 0)}</td><td className="num">{fmt(h.roe_pct)}%</td><td className="num">{pct(h.rev_growth_pct)}</td></tr>))}
            </tbody></table>
        </div>
        <div>
          <div className="section-label">Industry peers · {d.industry}</div>
          <table className="grid-table"><thead><tr><th>Symbol</th><th className="num">P/E</th><th className="num">ROE</th>
            <th className="num">Quality</th><th className="num">Value</th></tr></thead>
            <tbody>{d.peers.map((p) => (
              <tr key={p.symbol} className={`clickable ${p.symbol === symbol ? "self" : ""}`} onClick={() => onOpen && onOpen(p.symbol)}>
                <td><b>{p.symbol}</b></td><td className="num">{fmt(p.pe, 1)}</td><td className="num">{fmt(p.roe_pct, 1)}%</td>
                <td className="num">{fmt(p.quality_score, 0)}</td><td className="num">{fmt(p.value_score, 0)}</td></tr>))}
            </tbody></table>
        </div>
      </div>
      <div className="muted small-text">{d.note}</div>
    </div>
  );
}
