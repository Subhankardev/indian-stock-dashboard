import React, { useEffect, useState } from "react";
import { getJSON, fmt, pct, cls } from "./api";
import { Legend, LineChart } from "./Common";

const COLORS = { close: "var(--text)", vwap20: "#0ea5e9", avwap_hi52: "#e11d48", avwap_lo52: "#059669",
  avwap_volspike60: "#8b5cf6", avwap_ytd: "#f59e0b" };
const NAMES = { close: "Close", vwap20: "VWAP 20D", avwap_hi52: "AVWAP 52W high", avwap_lo52: "AVWAP 52W low",
  avwap_volspike60: "AVWAP volume spike", avwap_ytd: "AVWAP YTD" };

function Profile({ p, last }) {
  if (!p || !p.bins.length) return null;
  const max = Math.max(...p.bins.map((b) => b.volume));
  const rows = [...p.bins].reverse();
  return (
    <div className="profile">{rows.map((b) => {
      const inVA = b.price >= p.val && b.price <= p.vah;
      const isPoc = Math.abs(b.price - p.poc) < 1e-9;
      return (
        <div key={b.price} className="prow" title={`${fmt(b.price)} · ${fmt(b.volume, 0)}`}>
          <span className="plabel">{isPoc || (last && Math.abs(b.price - last) === Math.min(...p.bins.map((x) => Math.abs(x.price - last)))) ? fmt(b.price) : ""}</span>
          <span className={`pbar ${isPoc ? "poc" : inVA ? "va" : ""}`} style={{ width: `${(b.volume / max) * 100}%` }} />
        </div>);
    })}</div>
  );
}

export default function VwapPanel({ symbol, days }) {
  const [v, setV] = useState(null);
  const [i, setI] = useState(null);
  const [iErr, setIErr] = useState(null);
  useEffect(() => {
    setV(null); setI(null); setIErr(null);
    getJSON(`/api/intel/stock/${encodeURIComponent(symbol)}/vwap?days=${days}`).then(setV).catch(() => setV(false));
    getJSON(`/api/intel/stock/${encodeURIComponent(symbol)}/intraday`).then(setI).catch((e) => setIErr(e.message));
  }, [symbol, days]);
  if (!v) return null;
  const s = v.series, L = v.latest;
  const keys = Object.keys(COLORS);
  return (
    <>
      <div className="panel">
        <div className="panel-head"><h3>VWAP & anchored VWAP</h3>
          <span className="muted small-text">from NSE's daily average traded price × volume (exact daily VWAP)</span></div>
        <div className="cards">{Object.entries(v.anchors).map(([k, label]) => (
          <div className="card" key={k}><div className="muted small-text">vs AVWAP from {label}</div>
            <div className={`big ${cls(L[`dist_avwap_${k}_pct`])}`}>{pct(L[`dist_avwap_${k}_pct`])}</div>
            <div className="muted small-text">AVWAP {fmt(L[`avwap_${k}`])}</div></div>))}
          <div className="card"><div className="muted small-text">vs VWAP 20D</div>
            <div className={`big ${cls(L.dist_vwap20_pct)}`}>{pct(L.dist_vwap20_pct)}</div>
            {(L.absorption_up || L.absorption_down) && <div className="small-text warn-inline">absorption {L.absorption_up ? "up" : "down"} today (bar proxy)</div>}</div>
        </div>
        <Legend items={keys.map((k) => ({ label: NAMES[k], color: COLORS[k] }))} />
        <LineChart dates={s.map((r) => r.date)} yFormat={(x) => x.toFixed(x < 100 ? 1 : 0)}
          series={keys.map((k) => ({ key: k, color: COLORS[k], values: s.map((r) => r[k]), width: k === "close" ? 1.8 : 1.1,
            dash: k.startsWith("avwap") ? "4 3" : undefined }))} />
        <div className="muted small-text">An anchored VWAP is the average price paid by everyone who bought since the anchor.
          Price above it means those buyers are, on average, in profit. Anchors use only data available on each day.</div>
      </div>
      <div className="panel">
        <div className="panel-head"><h3>Intraday VWAP & volume profile</h3>
          <span className="muted small-text">{i ? (i.source === "synthetic" ? "demo session (synthetic)" : "Upstox 1-minute candles") : ""}</span></div>
        {iErr && <div className="muted small-text">{iErr}</div>}
        {i && (
          <div className="intraday">
            <div className="intraday-chart">
              <Legend items={[{ label: "Price", color: "var(--text)" }, { label: "VWAP", color: "#0ea5e9" }, { label: "±1σ / ±2σ", color: "#94a3b8" }]} />
              <LineChart dates={i.bars.map((b) => b.ts)} height={220} yFormat={(x) => x.toFixed(1)} series={[
                { key: "u2", color: "#cbd5e1", values: i.bars.map((b) => b.upper2), width: 1, dash: "3 3" },
                { key: "u1", color: "#94a3b8", values: i.bars.map((b) => b.upper1), width: 1 },
                { key: "l1", color: "#94a3b8", values: i.bars.map((b) => b.lower1), width: 1 },
                { key: "l2", color: "#cbd5e1", values: i.bars.map((b) => b.lower2), width: 1, dash: "3 3" },
                { key: "v", color: "#0ea5e9", values: i.bars.map((b) => b.vwap) },
                { key: "c", color: "var(--text)", values: i.bars.map((b) => b.close), width: 1.4 }]} />
            </div>
            <div className="intraday-profile">
              <div className="section-label">Volume profile</div>
              <Profile p={i.profile} last={i.last && i.last.close} />
              <div className="muted small-text">POC {fmt(i.profile.poc)} · value area {fmt(i.profile.val)}–{fmt(i.profile.vah)}</div>
            </div>
          </div>)}
        {i && <div className="muted small-text">{i.note}</div>}
      </div>
    </>
  );
}
