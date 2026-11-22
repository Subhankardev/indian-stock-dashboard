import React, { useEffect, useState } from "react";
import { getJSON, fmt, pct, cls } from "./api";
import { BarChart, ClassTag, CLASS_INFO, DemoBanner, ErrorBox, Sources } from "./Common";

const ENTITY = { mutual_fund: "Mutual fund", insurer: "Insurer", fpi: "FPI", bank: "Bank", pension: "Pension",
  broker_prop: "Broker / prop", corporate: "Corporate", individual: "Individual", promoter: "Promoter", unknown: "Unknown" };

function ScoreList({ title, rows, side, rankedBy, onOpen }) {
  const scoreKey = `${side}_score`;
  const sub = side === "accumulation" ? "d_score" : "x_score";
  return (
    <div className="panel half">
      <div className="panel-head"><h3>{title}</h3>
        <span className="muted small-text">ranked by {rankedBy === scoreKey ? "evidence score" : "class-D clue count"}</span></div>
      <table className="grid-table">
        <thead><tr><th>Symbol</th><th className="num">Score</th><th className="num">Confidence</th>
          <th className="num">D clues</th><th className="num">A</th><th className="num">B</th><th className="num">RS rank</th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.symbol} className="clickable" onClick={() => onOpen(r.symbol)}>
            <td><b>{r.symbol}</b><div className="muted small-text">{r.industry}</div></td>
            <td className="num">{r[scoreKey] === null ? <span className="muted" title="No component has passed its test yet">–</span> : fmt(r[scoreKey], 0)}</td>
            <td className="num">{fmt(r[`${side}_confidence`] * 100, 0)}%</td>
            <td className="num">{r[sub]} / 5</td>
            <td className="num">{fmt(r[`${side}_A`], 0)}</td>
            <td className="num">{fmt(r[`${side}_B`], 0)}</td>
            <td className="num">{fmt(r.rs_rank, 0)}</td>
          </tr>))}
        </tbody>
      </table>
    </div>
  );
}

export default function Institutional({ onOpen }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  const [instOnly, setInstOnly] = useState(false);
  useEffect(() => { getJSON("/api/intel/institutional?days=30&limit=15").then(setD).catch(setErr); }, []);
  if (err) return <ErrorBox error={err.message} />;
  if (!d) return <div className="panel muted">Loading…</div>;
  const f = d.flows;
  const lastF = f[f.length - 1];
  const deals = instOnly ? d.deals.filter((x) => x.evidence_class === "A") : d.deals;
  return (
    <>
      <DemoBanner demo={d.demo} />
      <div className="panel">
        <div className="panel-head"><h3>Evidence classes</h3></div>
        <div className="class-legend">{Object.entries(CLASS_INFO).filter(([k]) => k !== "T").map(([k, v]) => (
          <div key={k}><ClassTag c={k} /> {v.slice(4)}</div>))}</div>
        {d.passed_components && d.passed_components.length === 0 && (
          <div className="warn small-text">No accumulation/distribution component has passed its event study yet, so
            scores are blank and lists are ranked by the count of class-D clues. Run the tests on the Research page.</div>)}
      </div>
      <div className="panel">
        <div className="panel-head"><h3>FII / DII cash-market flows <ClassTag c="C" /></h3>
          <span className="muted small-text">{d.flows_note}</span></div>
        {f.length === 0 ? <div className="empty">No flow data. <code>python -m intel.ingest --today</code> stores each day's
          provisional figures (NSE only shows the latest day), or import history with <code>python -m intel.imports fii_dii file.csv</code>.</div> : (
          <>
            <div className="cards">
              <div className="card"><div className="muted small-text">FII net · {lastF.date}</div><div className={`big ${cls(lastF.FII)}`}>₹{fmt(lastF.FII, 0)} cr</div></div>
              <div className="card"><div className="muted small-text">FII 5D / 20D</div><div className={`big ${cls(lastF.FII_20d)}`}>{fmt(lastF.FII_5d, 0)} / {fmt(lastF.FII_20d, 0)}</div></div>
              <div className="card"><div className="muted small-text">DII net · {lastF.date}</div><div className={`big ${cls(lastF.DII)}`}>₹{fmt(lastF.DII, 0)} cr</div></div>
              <div className="card"><div className="muted small-text">DII 5D / 20D</div><div className={`big ${cls(lastF.DII_20d)}`}>{fmt(lastF.DII_5d, 0)} / {fmt(lastF.DII_20d, 0)}</div></div>
            </div>
            <div className="muted small-text">FII net, daily (green buy, red sell)</div>
            <BarChart dates={f.map((x) => x.date)} values={f.map((x) => Math.abs(x.FII || 0))}
              colors={f.map((x) => (x.FII >= 0 ? "var(--up)" : "var(--down)"))} height={80} />
          </>
        )}
      </div>
      <div className="two-col">
        <ScoreList title="Accumulation evidence" side="accumulation" rows={d.accumulation} rankedBy={d.ranked_by?.accumulation} onOpen={onOpen} />
        <ScoreList title="Distribution evidence" side="distribution" rows={d.distribution} rankedBy={d.ranked_by?.distribution} onOpen={onOpen} />
      </div>
      <div className="panel">
        <div className="panel-head"><h3>Bulk & block deals · last 30 days</h3>
          <label className="toggle-dark"><input type="checkbox" checked={instOnly} onChange={(e) => setInstOnly(e.target.checked)} /> Institutions only (class A)</label></div>
        <div className="muted small-text">Client names are classified by name rules unless set manually; the method is shown.
          A deal by a broker, prop desk or individual is class E: large, disclosed, but not institutional.</div>
        <div className="table-wrap">
          <table className="grid-table">
            <thead><tr><th>Date</th><th>Symbol</th><th>Client</th><th>Entity</th><th>Side</th><th className="num">Qty</th>
              <th className="num">Price</th><th className="num">₹ cr</th><th>Class</th></tr></thead>
            <tbody>{deals.slice(0, 200).map((x, i) => (
              <tr key={i}>
                <td>{x.date}</td><td className="clickable" onClick={() => onOpen(x.symbol)}><b>{x.symbol}</b></td>
                <td>{x.client}</td><td>{ENTITY[x.entity_class] || x.entity_class}<div className="muted small-text">{x.method}</div></td>
                <td className={x.side === "BUY" ? "up" : "down"}>{x.side}</td><td className="num">{fmt(x.qty, 0)}</td>
                <td className="num">{fmt(x.price)}</td><td className="num">{fmt(x.value_cr, 1)}</td><td><ClassTag c={x.evidence_class} /></td>
              </tr>))}
            </tbody>
          </table>
          {deals.length === 0 && <div className="empty">No deals stored for this period.</div>}
        </div>
        <Sources sources={d.sources} asOf={d.as_of} />
      </div>
    </>
  );
}
