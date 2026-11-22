import React, { useEffect, useState } from "react";
import { getJSON } from "./api";
import { ClassTag, DemoBanner, ErrorBox, Legend, LineChart } from "./Common";
import { fmt, pct, cls } from "./api";

const NAMES = { cm: "Cash market + delivery (sec_bhavdata_full)", indices: "Index closes (ind_close_all)",
  fo: "F&O bhavcopy (UDiFF)", participant_oi: "Participant-wise OI", demo: "Synthetic demo data" };

export default function Overview({ onOpen }) {
  const [s, setS] = useState(null);
  const [err, setErr] = useState(null);
  const [of, setOf] = useState(null);
  const [mk, setMk] = useState(null);
  useEffect(() => { getJSON("/api/intel/status").then(setS).catch(setErr); getJSON("/api/intel/orderflow").then(setOf).catch(() => {}); getJSON("/api/intel/market").then(setMk).catch(() => {}); }, []);
  if (err) return <ErrorBox error={`Backend not reachable: ${err.message}`} />;
  if (!s) return <div className="panel muted">Loading…</div>;
  const today = new Date();
  const age = (d) => Math.round((today - new Date(d)) / 864e5);
  return (
    <>
      <DemoBanner demo={s.demo} />
      {mk && mk.regime && (
        <div className="panel">
          <div className="panel-head"><h3>Market regime <ClassTag c="C" /></h3><span className="muted small-text">as of {mk.as_of}</span></div>
          <div className="cards">
            <div className="card"><div className="muted small-text">Regime</div>
              <div className={`big ${mk.regime.label === "uptrend" ? "up" : mk.regime.label === "downtrend" ? "down" : ""}`}>{mk.regime.label}</div>
              <div className="muted small-text">{mk.regime.reasons.join(" · ")}</div></div>
            {mk.breadth.length > 0 && (() => { const b = mk.breadth[mk.breadth.length - 1]; return (
              <>
                <div className="card"><div className="muted small-text">Above 200-DMA / 50-DMA</div><div className="big">{fmt(b.pct_above_200dma, 0)}% / {fmt(b.pct_above_50dma, 0)}%</div></div>
                <div className="card"><div className="muted small-text">Advancers / decliners</div><div className="big">{b.advancers} / {b.decliners}</div></div>
                <div className="card"><div className="muted small-text">New 52W highs / lows</div><div className="big">{b.new_highs} / {b.new_lows}</div></div>
              </>); })()}
            {mk.signal_counts && <div className="card"><div className="muted small-text">Today: possible accumulation / distribution</div>
              <div className="big">{mk.signal_counts.possible_accumulation ?? "–"} / {mk.signal_counts.possible_distribution ?? "–"}</div>
              <div className="muted small-text">institutional deal buys {mk.signal_counts.a_inst_buy ?? 0} · sells {mk.signal_counts.a_inst_sell ?? 0}</div></div>}
          </div>
          {mk.breadth.length > 0 && (<>
            <Legend items={[{ label: "% above 200-DMA", color: "var(--primary)" }, { label: "% above 50-DMA", color: "#0ea5e9" }]} />
            <LineChart dates={mk.breadth.map((b) => b.date)} height={140} yFormat={(v) => `${v.toFixed(0)}%`} series={[
              { key: "200", color: "var(--primary)", values: mk.breadth.map((b) => b.pct_above_200dma) },
              { key: "50", color: "#0ea5e9", values: mk.breadth.map((b) => b.pct_above_50dma), width: 1 }]} /></>)}
        </div>)}
      {mk && mk.sectors && mk.sectors.length > 0 && (
        <div className="panel">
          <div className="panel-head"><h3>Sector strength</h3><span className="muted small-text">median 6-month RS vs Nifty 50 of liquid stocks</span></div>
          <table className="grid-table"><thead><tr><th>Industry</th><th className="num">Stocks</th><th className="num">RS 6M</th><th className="num">RS 3M</th>
            <th className="num">Above 200-DMA</th><th className="num">Rank</th><th className="num">Rank Δ 20D</th></tr></thead>
            <tbody>{mk.sectors.map((x) => (
              <tr key={x.industry}><td>{x.industry}</td><td className="num">{x.sector_stocks}</td>
                <td className={`num ${cls(x.sector_rs_6m)}`}>{pct(x.sector_rs_6m)}</td><td className={`num ${cls(x.sector_rs_3m)}`}>{pct(x.sector_rs_3m)}</td>
                <td className="num">{fmt(x.sector_pct_above_200dma, 0)}%</td><td className="num">{fmt(x.sector_rank_pct, 0)}</td>
                <td className={`num ${cls(x.rank_change_20d)}`}>{x.rank_change_20d === null || x.rank_change_20d === undefined ? "–" : fmt(x.rank_change_20d, 0)}</td></tr>))}
            </tbody></table>
        </div>)}
      <div className="panel">
        <div className="panel-head"><h3>Data freshness</h3><span className="muted small-text">{s.database}</span></div>
        {s.datasets.length === 0 ? (
          <div className="empty">No data loaded yet. On your Mac run<br />
            <code>cd backend && python -m intel.ingest --lists --from 2023-01-01</code></div>
        ) : (
          <table className="grid-table">
            <thead><tr><th>Dataset</th><th>Latest session</th><th>Files</th><th>Age</th></tr></thead>
            <tbody>{s.datasets.map((d) => (
              <tr key={d.dataset}><td>{NAMES[d.dataset] || d.dataset}</td><td>{d.latest}</td><td className="num">{d.files}</td>
                <td className={age(d.latest) > 4 ? "down" : ""}>{age(d.latest)} days{age(d.latest) > 4 ? " · stale" : ""}</td></tr>))}
            </tbody>
          </table>
        )}
        {s.recent_errors.length > 0 && (
          <div className="warn small-text">Recent download errors: {s.recent_errors.map((e) => `${e.dataset} ${e.as_of}: ${e.error}`).join("; ")}</div>
        )}
      </div>
      <div className="panel">
        <div className="panel-head"><h3>Source registry</h3></div>
        <table className="grid-table">
          <thead><tr><th>Source</th><th>Auth</th><th>Cost</th><th>Rate limit</th><th>Status</th></tr></thead>
          <tbody>{s.registry.map((r) => (
            <tr key={r.source}><td>{r.source}</td><td>{r.auth}</td><td>{r.cost}</td><td>{r.rate_limit}</td><td>{r.status}</td></tr>))}
          </tbody>
        </table>
      </div>
      {of && (
        <div className="panel">
          <div className="panel-head"><h3>Order-flow features</h3>
            <span className={`pill ${of.enabled ? "ok" : "off"}`}>{of.enabled ? `provider: ${of.provider}` : "disabled"}</span></div>
          <table className="grid-table">
            <thead><tr><th>Feature</th><th>Needs</th><th>Why the free data can't do it</th><th>Status</th></tr></thead>
            <tbody>{of.features.map((f) => (
              <tr key={f.key}><td>{f.label}</td><td>{f.needs}</td><td className="muted">{f.why}</td><td className="muted">{f.reason}</td></tr>))}
            </tbody>
          </table>
          <div className="muted small-text">Available instead: {of.available_instead.join(" · ")}. Possible sources:
            {" "}{of.providers.map((p) => `${p.name} (${p.cost})`).join("; ")}.</div>
        </div>)}
      <div className="panel">
        <div className="panel-head"><h3>Active configuration</h3>
          <span className="muted small-text">Edit backend/intel_config.json — no code changes needed</span></div>
        <pre className="config">{JSON.stringify({ universe: s.config.universe, liquidity: s.config.liquidity,
          research: s.config.research }, null, 2)}</pre>
      </div>
    </>
  );
}
