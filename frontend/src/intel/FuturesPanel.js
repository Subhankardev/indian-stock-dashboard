import React, { useEffect, useState } from "react";
import { getJSON, fmt } from "./api";
import { ClassTag, Legend, LineChart } from "./Common";

export default function FuturesPanel({ symbol }) {
  const [d, setD] = useState(null);
  useEffect(() => { setD(null); getJSON(`/api/intel/stock/${encodeURIComponent(symbol)}/derivatives?days=120`).then(setD).catch(() => setD(false)); }, [symbol]);
  if (!d || !d.has_fo) return null;
  const s = d.series, L = s[s.length - 1];
  const dates = s.map((r) => r.date);
  return (
    <div className="panel">
      <div className="panel-head"><h3>Futures & options <ClassTag c="F" /></h3>
        <span className="muted small-text">as of {L.date} · near expiry {L.near_expiry} ({L.days_to_expiry} days)</span></div>
      <div className="cards">
        <div className="card"><div className="muted small-text">Futures OI (all expiries)</div><div className="big">{fmt(L.fut_oi, 0)}</div></div>
        <div className="card"><div className="muted small-text">Basis (near future vs spot)</div><div className="big">{fmt(L.basis_pct)}%</div></div>
        <div className="card"><div className="muted small-text">Rollover to next expiries</div><div className="big">{fmt(L.rollover_pct, 0)}%</div></div>
        <div className="card"><div className="muted small-text">PCR (OI, near expiry)</div><div className="big">{fmt(L.pcr_oi)}</div></div>
        <div className="card"><div className="muted small-text">ATM IV</div><div className="big">{fmt(L.atm_iv, 1)}%</div></div>
        <div className="card"><div className="muted small-text">Max call / put OI strike</div><div className="big">{fmt(L.max_ce_oi_strike, 0)} / {fmt(L.max_pe_oi_strike, 0)}</div></div>
      </div>
      <Legend items={[{ label: "Futures OI", color: "#e11d48" }]} />
      <LineChart dates={dates} height={150} yFormat={(v) => (v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v.toFixed(0))}
        series={[{ key: "oi", color: "#e11d48", values: s.map((r) => r.fut_oi) }]} />
      <div className="muted small-text">Open interest is summed across expiries, so monthly rollover is not mistaken for unwinding.
        Rising OI with rising price is a long build-up — positioning in this contract, not proof of institutional buying.</div>
    </div>
  );
}
