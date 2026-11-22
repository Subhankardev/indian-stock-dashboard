import React, { useEffect, useState } from "react";
import { getJSON, fmt } from "./api";
import { Legend, LineChart } from "./Common";

export default function ScorePanel({ symbol }) {
  const [preview, setPreview] = useState(false);
  const [s, setS] = useState(null);
  useEffect(() => { setS(null); getJSON(`/api/intel/stock/${encodeURIComponent(symbol)}/score?preview=${preview}`).then(setS).catch(() => setS(false)); }, [symbol, preview]);
  if (!s) return null;
  const b = s.scores.bullish, br = s.scores.bearish;
  return (
    <div className="panel">
      <div className="panel-head"><h3>Score breakdown</h3>
        <label className="toggle-dark"><input type="checkbox" checked={preview} onChange={(e) => setPreview(e.target.checked)} /> Unvalidated preview</label></div>
      <div className="cards">
        <div className="card"><div className="muted small-text">Bullish score</div><div className="big up">{fmt(b.bullish_score, 0)}</div>
          <div className="muted small-text">confidence {fmt((b.bullish_confidence || 0) * 100, 0)}% · agreement {b.bullish_agreement === null ? "–" : `${fmt(b.bullish_agreement * 100, 0)}%`}</div></div>
        <div className="card"><div className="muted small-text">Bearish score</div><div className="big down">{fmt(br.bearish_score, 0)}</div>
          <div className="muted small-text">confidence {fmt((br.bearish_confidence || 0) * 100, 0)}%</div></div>
      </div>
      {s.active.length === 0 && !preview && <div className="warn small-text">No factor has passed its test, so there is no score. Tick “Unvalidated preview” to inspect.</div>}
      {s.conflicts.length > 0 && <div className="warn small-text">⚠ Conflicts: {s.conflicts.join("; ")}</div>}
      <table className="grid-table"><thead><tr><th>Factor</th><th className="num">Weight</th><th>Status</th><th className="num">Bullish pct</th>
        <th className="num">Bearish pct</th><th className="num">Coverage</th></tr></thead>
        <tbody>{s.factors.map((f) => (
          <tr key={f.factor} className={f.active ? "" : "inactive"} title={f.description}><td>{f.factor}<div className="muted small-text">{f.description}</div></td>
            <td className="num">{f.weight}</td><td>{f.active ? <span className="pill ok">active</span> : <span className="pill off">not passed</span>}</td>
            <td className="num">{fmt(f.bullish_pct, 0)}</td><td className="num">{fmt(f.bearish_pct, 0)}</td>
            <td className="num">{f.coverage === null ? "–" : f.coverage === 0 ? "missing" : f.coverage < 1 ? "stale" : "✓"}</td></tr>))}
        </tbody></table>
      {s.history.some((h) => h.bullish_score !== null) && (
        <>
          <Legend items={[{ label: "Bullish score history", color: "var(--up)" }]} />
          <LineChart dates={s.history.map((h) => h.date)} height={120} yFormat={(v) => v.toFixed(0)}
            series={[{ key: "b", color: "var(--up)", values: s.history.map((h) => h.bullish_score) }]} />
        </>)}
    </div>
  );
}
