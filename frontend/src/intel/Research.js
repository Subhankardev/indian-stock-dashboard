import React, { useEffect, useState } from "react";
import { getJSON, fmt } from "./api";
import { DemoBanner, ErrorBox } from "./Common";

export default function Research() {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = () => getJSON("/api/intel/research").then(setD).catch(setErr);
  useEffect(() => { load(); }, []);
  const run = () => {
    setBusy(true); setErr(null);
    getJSON("/api/intel/research/run", { method: "POST" }).then(setD).catch(setErr).finally(() => setBusy(false));
  };
  if (!d) return err ? <ErrorBox error={err.message} /> : <div className="panel muted">Loading…</div>;
  const h = d.rules.pass_horizon;
  const byName = Object.fromEntries(d.results.map((r) => [r.signal_name, r]));
  return (
    <>
      <DemoBanner demo={d.demo} />
      <div className="panel">
        <div className="panel-head"><h3>Signal research · event studies</h3>
          <button className="primary" onClick={run} disabled={busy}>{busy ? "Running…" : "Re-run all"}</button></div>
        <ErrorBox error={err && err.message} />
        <div className="muted small-text">Each signal is tested on stored history: enter at the next session's open, exit
          after {h} sessions, minus the Nifty 500 and {d.rules.cost_bps_round_trip} bps round-trip costs. A signal passes
          only with ≥{d.rules.pass_min_events} events, a 95% range above zero (events on the same day count once) and
          positive results in ≥{d.rules.pass_min_positive_subperiods} of {d.rules.subperiods} sub-periods. Only passing
          signals may feed the score (Phase 6).</div>
        <table className="grid-table">
          <thead><tr><th>Signal</th><th>Dir</th><th>Events</th><th>Hit rate</th><th>{h}D excess / event-day</th>
            <th>95% range</th><th>Sub-periods</th><th>Verdict</th></tr></thead>
          <tbody>{Object.entries(d.signals).map(([name, meta]) => {
            const r = byName[name];
            const s = r && r.stats.horizons[h];
            return (
              <tr key={name} title={meta.description}>
                <td><b>{name}</b><div className="muted small-text">{meta.description}</div></td>
                <td>{meta.direction}</td>
                <td className="num">{r ? r.stats.events : "–"}</td>
                <td className="num">{s && s.hit_rate !== undefined ? `${(s.hit_rate * 100).toFixed(0)}%` : "–"}</td>
                <td className="num">{s ? `${fmt(s.day_mean_pct)}%` : "–"}</td>
                <td className="num">{s && s.ci95 ? `${fmt(s.ci95[0])} to ${fmt(s.ci95[1])}` : "–"}</td>
                <td className="num">{s && s.subperiod_means ? s.subperiod_means.map((m) => fmt(m, 1)).join(" / ") : "–"}</td>
                <td>{r ? <span className={`pill ${r.passed ? "ok" : "bad"}`}>{r.passed ? "PASS" : "fail"}</span> : <span className="muted">not run</span>}
                  {r && <div className="muted small-text">{r.stats.verdict}</div>}</td>
              </tr>);
          })}</tbody>
        </table>
        {d.results[0] && <div className="provenance">Last run {d.results[0].run_at} UTC · period {d.results[0].period} · rules {d.results[0].rule_version}</div>}
      </div>
    </>
  );
}
