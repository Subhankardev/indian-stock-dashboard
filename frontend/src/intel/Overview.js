import React, { useEffect, useState } from "react";
import { getJSON } from "./api";
import { DemoBanner, ErrorBox } from "./Common";

const NAMES = { cm: "Cash market + delivery (sec_bhavdata_full)", indices: "Index closes (ind_close_all)",
  fo: "F&O bhavcopy (UDiFF)", participant_oi: "Participant-wise OI", demo: "Synthetic demo data" };

export default function Overview() {
  const [s, setS] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => { getJSON("/api/intel/status").then(setS).catch(setErr); }, []);
  if (err) return <ErrorBox error={`Backend not reachable: ${err.message}`} />;
  if (!s) return <div className="panel muted">Loading…</div>;
  const today = new Date();
  const age = (d) => Math.round((today - new Date(d)) / 864e5);
  return (
    <>
      <DemoBanner demo={s.demo} />
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
      <div className="panel">
        <div className="panel-head"><h3>Active configuration</h3>
          <span className="muted small-text">Edit backend/intel_config.json — no code changes needed</span></div>
        <pre className="config">{JSON.stringify({ universe: s.config.universe, liquidity: s.config.liquidity,
          research: s.config.research }, null, 2)}</pre>
      </div>
    </>
  );
}
