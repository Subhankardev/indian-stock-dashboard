import React, { useEffect, useState } from "react";
import { getJSON, fmt } from "./api";
import { DemoBanner, ErrorBox } from "./Common";

const SHORT = { accumulation: "Acc", relative_strength: "RS", momentum: "Mom", volume_delivery: "Vol",
  derivatives: "F&O", fundamentals: "Qual", valuation: "Val", sector: "Sect", risk: "Risk" };

function Heat({ v }) {
  if (v === null || v === undefined) return <td className="num muted">–</td>;
  const a = Math.abs(v - 50) / 50;
  const bg = v >= 50 ? `rgba(5,150,105,${a * 0.35})` : `rgba(220,38,38,${a * 0.35})`;
  return <td className="num" style={{ background: bg }}>{fmt(v, 0)}</td>;
}

function List({ title, rows, factors, onOpen }) {
  const names = Object.keys(factors);
  return (
    <div className="panel">
      <div className="panel-head"><h3>{title}</h3></div>
      {rows.length === 0 ? <div className="empty">Nothing ranked.</div> : (
        <div className="table-wrap"><table className="grid-table">
          <thead><tr><th>#</th><th>Symbol</th><th className="num">Score</th><th className="num">Conf.</th><th className="num">Agree</th>
            {names.map((f) => <th key={f} className={`num ${factors[f].active ? "" : "inactive"}`} title={factors[f].description}>{SHORT[f]}</th>)}
            <th>Conflicts</th></tr></thead>
          <tbody>{rows.map((r, i) => (
            <tr key={r.symbol} className="clickable" onClick={() => onOpen(r.symbol)}>
              <td className="muted">{i + 1}</td><td><b>{r.symbol}</b><div className="muted small-text">{r.industry}</div></td>
              <td className="num"><b>{fmt(r.score, 0)}</b></td><td className="num">{fmt(r.confidence * 100, 0)}%</td>
              <td className="num">{r.agreement === null ? "–" : `${fmt(r.agreement * 100, 0)}%`}</td>
              {names.map((f) => factors[f].active ? <Heat key={f} v={r.factors[f]} /> : <td key={f} className="num inactive">{fmt(r.factors[f], 0)}</td>)}
              <td className="small-text">{r.conflicts.length ? <span className="down">⚠ {r.conflicts.join("; ")}</span> : ""}</td>
            </tr>))}
          </tbody></table></div>)}
    </div>
  );
}

export default function Rankings({ onOpen }) {
  const [preview, setPreview] = useState(false);
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => { setD(null); getJSON(`/api/intel/rankings?limit=25&preview=${preview}`).then(setD).catch(setErr); }, [preview]);
  if (err) return <ErrorBox error={err.message} />;
  if (!d) return <div className="panel muted">Loading…</div>;
  return (
    <>
      <DemoBanner demo={d.demo} />
      {d.preview && <div className="banner bad-banner"><div><b>Unvalidated preview.</b> Every factor is switched on, including
        factors that have not passed their tests. Use this to inspect the machinery, not to pick stocks.</div></div>}
      <div className="panel">
        <div className="panel-head"><h3>Rankings · {d.as_of}</h3>
          <label className="toggle-dark"><input type="checkbox" checked={preview} onChange={(e) => setPreview(e.target.checked)} /> Unvalidated preview (all factors)</label></div>
        <div className="muted small-text">Score = weighted average of active factor percentiles (0–100), weighted by data coverage. A factor is
          active only after its <b>factor_*</b> test passes on the Research page. Confidence = share of active weight with data;
          rows below {fmt(d.min_confidence * 100, 0)}% are not ranked{d.below_confidence ? ` (${d.below_confidence} hidden)` : ""}. Agreement = share of
          available factors above 60. The score is a rank, not a probability.</div>
        <div className="factor-chips">{Object.entries(d.factors).map(([f, x]) => (
          <span key={f} className={`pill ${x.active ? "ok" : "off"}`} title={x.description}>{f} · w {x.weight}{x.active ? "" : " · inactive"}</span>))}</div>
        {d.message && <div className="warn small-text">{d.message}</div>}
      </div>
      <List title="Bullish" rows={d.bullish} factors={d.factors} onOpen={onOpen} />
      <List title="Bearish" rows={d.bearish} factors={d.factors} onOpen={onOpen} />
      {d.special && (
        <div className="panel">
          <div className="panel-head"><h3>Special situations today</h3><span className="muted small-text">combinations worth a look — not recommendations, not yet tested</span></div>
          {d.special.length === 0 ? <div className="muted small-text">None today.</div> : (
            <table className="grid-table"><thead><tr><th>Symbol</th><th>Situation</th><th>Direction</th></tr></thead>
              <tbody>{d.special.map((s, i) => (
                <tr key={i} className="clickable" onClick={() => onOpen(s.symbol)}><td><b>{s.symbol}</b></td><td>{s.description}</td>
                  <td className={s.direction === "bullish" ? "up" : "down"}>{s.direction}</td></tr>))}
              </tbody></table>)}
        </div>)}
    </>
  );
}
