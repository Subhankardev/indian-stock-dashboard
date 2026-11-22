import React, { useEffect, useState } from "react";
import { getJSON, fmt } from "./api";
import { ClassTag } from "./Common";

function Subs({ title, subs }) {
  return (
    <div className="half">
      <div className="section-label">{title}</div>
      <ul className="subs">{Object.entries(subs).map(([k, s]) => (
        <li key={k} className={s.on ? "on" : ""}><span className="dot-ind">{s.on ? "●" : "○"}</span> {s.label}</li>))}</ul>
    </div>
  );
}

export default function EvidencePanel({ symbol }) {
  const [e, setE] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => { setE(null); getJSON(`/api/intel/stock/${encodeURIComponent(symbol)}/evidence`).then(setE).catch(setErr); }, [symbol]);
  if (err) return <div className="panel error">{err.message}</div>;
  if (!e) return null;
  const s = e.scores || {};
  const own = e.ownership;
  return (
    <div className="panel">
      <div className="panel-head"><h3>Accumulation / distribution evidence</h3>
        <span className="muted small-text">as of {e.as_of}</span></div>
      <div className="cards">
        {["accumulation", "distribution"].map((side) => (
          <div className="card" key={side}>
            <div className="muted small-text">{side === "accumulation" ? "Accumulation" : "Distribution"} score</div>
            <div className="big">{s[`${side}_score`] === null || s[`${side}_score`] === undefined ? "–" : fmt(s[`${side}_score`], 0)}</div>
            <div className="muted small-text">confidence {fmt((s[`${side}_confidence`] || 0) * 100, 0)}% ·
              A {fmt(s[`${side}_A`], 0)} · B {fmt(s[`${side}_B`], 0)} · D {fmt(s[`${side}_D`], 0)}</div>
            {(s[`${side}_score`] === null || s[`${side}_score`] === undefined) &&
              <div className="muted small-text">No component has passed its test, so there is no score.</div>}
          </div>))}
      </div>
      <div className="two-col">
        <Subs title="Class D · possible accumulation clues" subs={e.subsignals.accumulation} />
        <Subs title="Class D · possible distribution clues" subs={e.subsignals.distribution} />
      </div>
      {e.items.length > 0 && (
        <ul className="evidence">{e.items.map((it, i) => (
          <li key={i}><ClassTag c={it.class} /> <b className={it.direction === "bullish" ? "up" : it.direction === "bearish" ? "down" : ""}>{it.label}</b> — {it.detail}</li>))}
        </ul>)}
      <div className="two-col">
        <div className="half">
          <div className="section-label">Shareholding <ClassTag c="B" /></div>
          {own.length === 0 ? <div className="muted small-text">No shareholding imported (python -m intel.imports shareholding file.csv).</div> : (
            <table className="grid-table"><thead><tr><th>Quarter</th><th>Published</th><th className="num">Promoter</th>
              <th className="num">FPI</th><th className="num">DII</th><th className="num">FPI+DII Δ pp</th></tr></thead>
              <tbody>{own.slice(-8).reverse().map((o) => (
                <tr key={o.quarter_end}><td>{o.quarter_end}</td><td>{o.published_on}</td><td className="num">{fmt(o.promoter)}</td>
                  <td className="num">{fmt(o.fpi)}</td><td className="num">{fmt(o.dii)}</td>
                  <td className={`num ${o.inst_change_pp > 0 ? "up" : o.inst_change_pp < 0 ? "down" : ""}`}>{fmt(o.inst_change_pp)}</td></tr>))}
              </tbody></table>)}
        </div>
        <div className="half">
          <div className="section-label">Bulk / block deals</div>
          {e.deals.length === 0 ? <div className="muted small-text">No deals stored for this stock.</div> : (
            <table className="grid-table"><thead><tr><th>Date</th><th>Client</th><th>Side</th><th className="num">₹ cr</th><th>Class</th></tr></thead>
              <tbody>{e.deals.slice(0, 12).map((x, i) => (
                <tr key={i}><td>{x.date}</td><td>{x.client}<div className="muted small-text">{x.entity_class} · {x.method}</div></td>
                  <td className={x.side === "BUY" ? "up" : "down"}>{x.side}</td><td className="num">{fmt(x.value_cr, 1)}</td>
                  <td><ClassTag c={x.evidence_class} /></td></tr>))}
              </tbody></table>)}
        </div>
      </div>
    </div>
  );
}
