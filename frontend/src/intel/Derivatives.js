import React, { useEffect, useState } from "react";
import { getJSON, fmt, pct, cls } from "./api";
import { ClassTag, DemoBanner, ErrorBox, Legend, LineChart, Sources } from "./Common";

const LABEL = { long_buildup: "Long build-up", short_buildup: "Short build-up", short_covering: "Short covering", long_unwinding: "Long unwinding" };
const PCOL = { FII: "#e11d48", DII: "#2563eb", Pro: "#f59e0b", Client: "#64748b" };

export default function Derivatives({ onOpen }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => { getJSON("/api/intel/derivatives?limit=12").then(setD).catch(setErr); }, []);
  if (err) return <ErrorBox error={err.message} />;
  if (!d) return <div className="panel muted">Loading…</div>;
  const dates = [...new Set(d.participants.map((p) => p.date))];
  const byType = (t) => dates.map((dt) => { const r = d.participants.find((p) => p.date === dt && p.client_type === t); return r ? r.idx_fut_long_pct : null; });
  const last = dates[dates.length - 1];
  const lastRows = d.participants.filter((p) => p.date === last);
  const nifty = d.indices.filter((x) => x.symbol === "NIFTY");
  return (
    <>
      <DemoBanner demo={d.demo} />
      <div className="panel">
        <div className="panel-head"><h3>Index-futures positioning by participant <ClassTag c="C" /></h3>
          <span className="muted small-text">NSE participant-wise OI · market-wide, never stock-specific</span></div>
        {dates.length === 0 ? <div className="empty">No participant OI. <code>python -m intel.ingest --days 60 --datasets participant_oi</code></div> : (
          <>
            <div className="cards">{lastRows.map((r) => (
              <div className="card" key={r.client_type}><div className="muted small-text">{r.client_type} · long share of index futures</div>
                <div className="big">{fmt(r.idx_fut_long_pct, 1)}%</div>
                <div className={`small-text ${cls(r.idx_fut_net_chg)}`}>net {fmt(r.idx_fut_net, 0)} ({r.idx_fut_net_chg > 0 ? "+" : ""}{fmt(r.idx_fut_net_chg, 0)} d/d)</div></div>))}
            </div>
            <Legend items={Object.entries(PCOL).map(([k, c]) => ({ label: k, color: c }))} />
            <LineChart dates={dates} height={170} yFormat={(v) => `${v.toFixed(0)}%`}
              series={Object.entries(PCOL).map(([k, c]) => ({ key: k, color: c, values: byType(k) }))} />
          </>)}
      </div>
      {nifty.length > 0 && (
        <div className="panel">
          <div className="panel-head"><h3>NIFTY options</h3><span className="muted small-text">near expiry · as of {nifty[nifty.length - 1].date}</span></div>
          <div className="cards">
            <div className="card"><div className="muted small-text">PCR (OI)</div><div className="big">{fmt(nifty[nifty.length - 1].pcr_oi)}</div></div>
            <div className="card"><div className="muted small-text">ATM IV</div><div className="big">{fmt(nifty[nifty.length - 1].atm_iv, 1)}%</div></div>
            <div className="card"><div className="muted small-text">Max call OI strike</div><div className="big">{fmt(nifty[nifty.length - 1].max_ce_oi_strike, 0)}</div></div>
            <div className="card"><div className="muted small-text">Max put OI strike</div><div className="big">{fmt(nifty[nifty.length - 1].max_pe_oi_strike, 0)}</div></div>
          </div>
          <Legend items={[{ label: "PCR (OI)", color: "var(--primary)" }]} />
          <LineChart dates={nifty.map((x) => x.date)} height={140} yFormat={(v) => v.toFixed(2)}
            series={[{ key: "pcr", color: "var(--primary)", values: nifty.map((x) => x.pcr_oi) }]} />
        </div>)}
      <div className="panel">
        <div className="panel-head"><h3>Stock futures build-ups · last 5 sessions <ClassTag c="F" /></h3>
          <span className="muted small-text">{Object.entries(d.counts).map(([k, v]) => `${LABEL[k] || k}: ${v}`).join(" · ")}</span></div>
        <div className="muted small-text">{d.note}</div>
        <div className="two-col">{Object.keys(LABEL).map((b) => (
          <div key={b}>
            <div className="section-label">{LABEL[b]}</div>
            <table className="grid-table"><thead><tr><th>Symbol</th><th className="num">Price 5D</th><th className="num">OI 5D</th>
              <th className="num">Basis</th><th className="num">PCR</th><th className="num">IV</th></tr></thead>
              <tbody>{(d.buildups[b] || []).map((r) => (
                <tr key={r.symbol} className="clickable" onClick={() => onOpen(r.symbol)}>
                  <td><b>{r.symbol}</b></td><td className={`num ${cls(r.f_ret_pct)}`}>{pct(r.f_ret_pct)}</td>
                  <td className="num">{pct(r.f_oi_chg_pct)}</td><td className="num">{fmt(r.f_basis_pct)}%</td>
                  <td className="num">{fmt(r.f_pcr_oi)}</td><td className="num">{fmt(r.f_atm_iv, 0)}</td></tr>))}
              </tbody></table>
            {(d.buildups[b] || []).length === 0 && <div className="muted small-text">None today.</div>}
          </div>))}
        </div>
        <Sources sources={d.sources} asOf={d.as_of} />
      </div>
    </>
  );
}
