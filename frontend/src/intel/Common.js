import React from "react";

export function DemoBanner({ demo }) {
  if (!demo) return null;
  return (
    <div className="banner warn-banner">
      <div><b>Demo data.</b> The database holds synthetic prices from <code>python -m intel.demo</code>. Nothing on this
        page is real market data. Load NSE history with <code>python -m intel.ingest --lists --from 2023-01-01</code>.</div>
    </div>
  );
}

export function Sources({ sources, asOf }) {
  if (!sources || !sources.length) return null;
  return (
    <div className="provenance">
      As of <b>{asOf || "–"}</b> · Sources: {sources.map((s) => `${s.dataset} (latest ${s.latest}, ${s.files} files)`).join(" · ")}
    </div>
  );
}

export function ErrorBox({ error }) {
  return error ? <div className="panel error">{String(error)}</div> : null;
}

/** Minimal SVG line chart: series = [{key, label, color, values:[number|null]}], shared x. */
export function LineChart({ dates, series, height = 220, yFormat = (v) => v.toFixed(0) }) {
  const w = 900, h = height, padL = 52, padB = 22, padT = 8, padR = 8;
  const all = series.flatMap((s) => s.values).filter((v) => v !== null && v !== undefined && isFinite(v));
  if (!all.length) return <div className="muted small-text">No data</div>;
  let lo = Math.min(...all), hi = Math.max(...all);
  if (lo === hi) { lo -= 1; hi += 1; }
  const x = (i) => padL + (i / Math.max(dates.length - 1, 1)) * (w - padL - padR);
  const y = (v) => padT + (1 - (v - lo) / (hi - lo)) * (h - padT - padB);
  const path = (vals) => vals.map((v, i) => (v === null || v === undefined ? null : `${x(i)},${y(v)}`))
    .reduce((acc, p) => { if (!p) { acc.push([]); } else { acc[acc.length - 1].push(p); } return acc; }, [[]])
    .filter((seg) => seg.length).map((seg) => `M${seg.join("L")}`).join(" ");
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => lo + t * (hi - lo));
  const xt = [0, Math.floor(dates.length / 2), dates.length - 1];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="chart" role="img">
      {ticks.map((t) => (
        <g key={t}><line x1={padL} x2={w - padR} y1={y(t)} y2={y(t)} className="grid" />
          <text x={padL - 6} y={y(t) + 4} textAnchor="end" className="axis">{yFormat(t)}</text></g>
      ))}
      {xt.map((i) => <text key={i} x={x(i)} y={h - 4} textAnchor="middle" className="axis">{dates[i]}</text>)}
      {series.map((s) => <path key={s.key} d={path(s.values)} fill="none" stroke={s.color} strokeWidth={s.width || 1.6}
        strokeDasharray={s.dash} />)}
    </svg>
  );
}

export function BarChart({ dates, values, height = 90, color = "var(--faint)", colors, ref100 }) {
  const w = 900, h = height, padL = 52;
  const vals = values.map((v) => (v === null || v === undefined ? 0 : v));
  const max = Math.max(...vals, ref100 || 0, 1);
  const bw = (w - padL) / Math.max(vals.length, 1);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="chart" role="img">
      {vals.map((v, i) => <rect key={i} x={padL + i * bw} y={h - (v / max) * h} width={Math.max(bw - 1, 1)}
        height={(v / max) * h} fill={colors ? colors[i] : color} />)}
      {ref100 !== undefined && <line x1={padL} x2={w} y1={h - (ref100 / max) * h} y2={h - (ref100 / max) * h} className="grid" />}
      <text x={padL - 6} y={10} textAnchor="end" className="axis">{max >= 1e6 ? `${(max / 1e6).toFixed(1)}M` : max.toFixed(0)}</text>
    </svg>
  );
}

export function Legend({ items }) {
  return <div className="legend">{items.map((s) => <span key={s.label}><i style={{ background: s.color }} />{s.label}</span>)}</div>;
}

export const CLASS_INFO = {
  A: "A · confirmed disclosed transaction (bulk/block deal by a classified institution)",
  B: "B · observable ownership change (shareholding / MF holdings, from publication date)",
  C: "C · market-wide positioning — never stock-specific",
  D: "D · possible accumulation/distribution, inferred from price, volume and delivery",
  E: "E · unclassified large activity — direction or identity unknown",
  T: "T · technical / relative-strength signal",
};

export function ClassTag({ c }) {
  if (!c) return null;
  return <span className={`cls cls-${c}`} title={CLASS_INFO[c]}>{c}</span>;
}
