import React, { useCallback, useEffect, useState } from "react";
import { getJSON, fmt, pct, cls } from "./api";
import { DataWarning, DemoBanner, ErrorBox, Legend, LineChart } from "./Common";

const BCOL = { "Nifty 50": "#f59e0b", "Nifty 500": "#94a3b8" };

function Metric({ label, v, unit = "", d = 2, tone }) {
  return <div className="card"><div className="muted small-text">{label}</div><div className={`big ${tone || ""}`}>{v === null || v === undefined ? "–" : `${fmt(v, d)}${unit}`}</div></div>;
}

function Report({ r }) {
  const m = r.metrics, s = r.series;
  const benches = Object.keys(s.benchmarks || {});
  const years = Object.keys(m.by_year || {});
  return (
    <>
      <div className="panel">
        <div className="panel-head"><h3>#{r.id} · {r.name}</h3><span className="muted small-text">{m.start} → {m.end} · run {r.run_at}</span></div>
        <div className="cards">
          <Metric label="CAGR" v={m.cagr_pct} unit="%" tone={cls(m.cagr_pct)} />
          <Metric label="Volatility" v={m.volatility_pct} unit="%" />
          <Metric label="Sharpe · Sortino" v={m.sharpe} />
          <Metric label="Max drawdown" v={m.max_drawdown_pct} unit="%" tone="down" />
          <Metric label="Calmar" v={m.calmar} />
          {benches.map((b) => m[`vs_${b}`] && (
            <div className="card" key={b}><div className="muted small-text">vs {b} (CAGR {fmt(m[`vs_${b}`].bench_cagr_pct)}%)</div>
              <div className={`big ${cls(m[`vs_${b}`].excess_cagr_pct)}`}>{pct(m[`vs_${b}`].excess_cagr_pct, 2)}</div>
              <div className="muted small-text">beta {fmt(m[`vs_${b}`].beta)} · IR {fmt(m[`vs_${b}`].information_ratio)}</div></div>))}
          {m.trades !== undefined && <Metric label="Trades" v={m.trades} d={0} />}
          {m.turnover_x_per_year !== undefined && <Metric label="Turnover (× capital / yr)" v={m.turnover_x_per_year} d={1} />}
          {m.exposure_pct !== undefined && <Metric label="Avg exposure" v={m.exposure_pct} unit="%" d={0} />}
          {m.hit_rate !== undefined && <Metric label="Hit rate" v={m.hit_rate * 100} unit="%" d={0} />}
          {m.profit_factor !== undefined && <Metric label="Profit factor" v={m.profit_factor} />}
          {m.expectancy !== undefined && <Metric label="Expectancy / trade" v={m.expectancy} unit=" ₹" d={0} />}
        </div>
        <Legend items={[{ label: "Strategy", color: "var(--primary)" }, ...benches.map((b) => ({ label: b, color: BCOL[b] || "#64748b" }))]} />
        <LineChart dates={s.dates} height={240} yFormat={(v) => v.toFixed(0)} series={[
          ...benches.map((b) => ({ key: b, color: BCOL[b] || "#64748b", values: s.benchmarks[b], width: 1.2 })),
          { key: "eq", color: "var(--primary)", values: s.equity, width: 2 }]} />
        <div className="muted small-text">Drawdown</div>
        <LineChart dates={s.dates} height={110} yFormat={(v) => `${v.toFixed(0)}%`} series={[{ key: "dd", color: "var(--down)", values: s.drawdown }]} />
        {(r.params.warnings || []).map((w) => <div key={w} className="warn small-text">⚠ {w}</div>)}
        <div className="muted small-text">Bias controls: {(r.params.bias_controls || []).join(" · ")}. Costs: full delivery model with
          {` ${r.params.costs.slippage_bps_liquid}/${r.params.costs.slippage_bps_mid}`} bps slippage (liquid/mid), capacity ≤ {r.params.capacity_pct_of_value}% of 20-day traded value.</div>
      </div>
      {years.length > 0 && (
        <div className="panel"><div className="panel-head"><h3>Calendar years</h3></div>
          <table className="grid-table"><thead><tr><th>Year</th><th className="num">Strategy</th>{benches.map((b) => <th key={b} className="num">{b}</th>)}</tr></thead>
            <tbody>{years.map((y) => (
              <tr key={y}><td>{y}</td><td className={`num ${cls(m.by_year[y])}`}>{pct(m.by_year[y])}</td>
                {benches.map((b) => <td key={b} className={`num ${cls(((m[`vs_${b}`] || {}).bench_by_year || {})[y])}`}>{pct(((m[`vs_${b}`] || {}).bench_by_year || {})[y])}</td>)}</tr>))}
            </tbody></table></div>)}
      {m.windows && (
        <div className="panel"><div className="panel-head"><h3>Walk-forward windows</h3><span className="muted small-text">only the test windows are in the equity curve</span></div>
          <table className="grid-table"><thead><tr><th>Train</th><th>Test</th><th>Chosen variant</th><th className="num">In-sample Sharpe</th><th className="num">Out-of-sample return</th></tr></thead>
            <tbody>{m.windows.map((w, i) => (
              <tr key={i}><td>{w.train}</td><td>{w.test}</td><td>{w.chosen}</td><td className="num">{fmt(w.in_sample_sharpe)}</td>
                <td className={`num ${cls(w.oos_return_pct)}`}>{pct(w.oos_return_pct)}</td></tr>))}
            </tbody></table></div>)}
      {m.grid && (
        <div className="panel"><div className="panel-head"><h3>Sensitivity</h3><span className={`pill ${m.fragile ? "bad" : "ok"}`}>{m.verdict}</span></div>
          <table className="grid-table"><thead><tr><th className="num">Top K</th><th className="num">Rebalance</th><th className="num">Cost ×</th>
            <th className="num">CAGR</th><th className="num">Sharpe</th><th className="num">Max DD</th><th className="num">Turnover</th></tr></thead>
            <tbody>{m.grid.map((g, i) => (
              <tr key={i}><td className="num">{g.top_k}</td><td className="num">{g.rebalance}d</td><td className="num">{g.cost_mult}</td>
                <td className={`num ${cls(g.cagr_pct)}`}>{pct(g.cagr_pct)}</td><td className="num">{fmt(g.sharpe)}</td>
                <td className="num">{fmt(g.max_drawdown_pct, 1)}%</td><td className="num">{fmt(g.turnover, 1)}×</td></tr>))}
            </tbody></table></div>)}
      {m.recent_trades && m.recent_trades.length > 0 && (
        <div className="panel"><div className="panel-head"><h3>Trades (last 50)</h3></div>
          <div className="table-wrap"><table className="grid-table"><thead><tr><th>Symbol</th><th>Signal</th><th>Closed</th><th className="num">Entry</th>
            <th className="num">Exit</th><th>Reason</th><th className="num">P&L</th><th className="num">R</th></tr></thead>
            <tbody>{m.recent_trades.map((t, i) => (
              <tr key={i}><td>{t.symbol}</td><td>{String(t.signal_date).slice(0, 10)}</td><td>{String(t.closed).slice(0, 10)}</td>
                <td className="num">{fmt(t.entry)}</td><td className="num">{fmt(t.exit)}</td><td>{t.reason}</td>
                <td className={`num ${cls(t.pnl)}`}>{fmt(t.pnl, 0)}</td><td className="num">{fmt(t.r)}</td></tr>))}
            </tbody></table></div></div>)}
    </>
  );
}

export default function Backtests() {
  const [meta, setMeta] = useState(null);
  const [err, setErr] = useState(null);
  const [form, setForm] = useState({ kind: "ranked", factors: ["relative_strength", "momentum"], signal: "breakout_volume", top_k: "", rebalance: "" });
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState(null);
  const load = useCallback(() => getJSON("/api/intel/backtests").then(setMeta).catch(setErr), []);
  useEffect(() => { load(); }, [load]);
  const open = (id) => getJSON(`/api/intel/backtests/${id}`).then(setReport).catch(setErr);
  const run = () => {
    setBusy(true); setErr(null);
    const body = { kind: form.kind, factors: ["ranked", "sensitivity"].includes(form.kind) ? form.factors : null,
      signal: form.kind === "signal" ? form.signal : null, top_k: form.top_k ? Number(form.top_k) : null, rebalance: form.rebalance ? Number(form.rebalance) : null };
    getJSON("/api/intel/backtests", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then((r) => { setReport(r); load(); }).catch(setErr).finally(() => setBusy(false));
  };
  if (!meta) return err ? <ErrorBox error={err.message} /> : <div className="panel muted">Loading…</div>;
  const toggle = (f) => setForm({ ...form, factors: form.factors.includes(f) ? form.factors.filter((x) => x !== f) : [...form.factors, f] });
  return (
    <>
      <DemoBanner demo={meta.demo} />
      <DataWarning text={meta.warning} />
      <div className="panel">
        <div className="panel-head"><h3>Portfolio backtests</h3></div>
        <div className="muted small-text">Decisions at the close, trades at the next open; survivorship-free daily universe; point-in-time data;
          full costs with tiered slippage and a capacity cap. Results vs Nifty 50 and Nifty 500. Past results on this data are not a forecast.</div>
        <div className="bt-form">
          <label>Mode <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
            <option value="ranked">Ranked portfolio (top-K by factor score)</option>
            <option value="signal">Signal + setup (stops, targets, sizing)</option>
            <option value="walk_forward">Walk-forward over factor variants</option>
            <option value="sensitivity">Sensitivity grid</option></select></label>
          {["ranked", "sensitivity"].includes(form.kind) && (
            <div className="factor-chips">{meta.factors.map((f) => (
              <button key={f} className={`chip-btn ${form.factors.includes(f) ? "active" : ""}`} onClick={() => toggle(f)}>{f}</button>))}</div>)}
          {form.kind === "signal" && <label>Signal <select value={form.signal} onChange={(e) => setForm({ ...form, signal: e.target.value })}>
            {meta.signals.map((s) => <option key={s}>{s}</option>)}</select></label>}
          {form.kind === "ranked" && (<>
            <label>Top K <input type="number" placeholder={meta.defaults.top_k} value={form.top_k} onChange={(e) => setForm({ ...form, top_k: e.target.value })} /></label>
            <label>Rebalance (sessions) <input type="number" placeholder={meta.defaults.rebalance_sessions} value={form.rebalance} onChange={(e) => setForm({ ...form, rebalance: e.target.value })} /></label></>)}
          {form.kind === "walk_forward" && <div className="muted small-text">Variants (from config): {Object.entries(meta.variants).map(([k, v]) => `${k} = ${v.join("+")}`).join(" · ")};
            train {meta.defaults.train_sessions} / test {meta.defaults.test_sessions} sessions.</div>}
          <button className="primary" onClick={run} disabled={busy}>{busy ? "Running…" : "Run backtest"}</button>
        </div>
        {err && <div className="error small-text">{err.message}</div>}
      </div>
      {report && <Report r={report} />}
      <div className="panel">
        <div className="panel-head"><h3>Saved runs</h3></div>
        {meta.runs.length === 0 ? <div className="empty">No backtests yet. <code>python -m intel.backtest --factors relative_strength,momentum</code></div> : (
          <table className="grid-table"><thead><tr><th>#</th><th>Run</th><th>When</th><th className="num">CAGR</th><th className="num">Sharpe</th>
            <th className="num">Max DD</th><th className="num">Excess vs Nifty 500</th></tr></thead>
            <tbody>{meta.runs.map((r) => (
              <tr key={r.id} className="clickable" onClick={() => open(r.id)}><td className="muted">{r.id}</td><td>{r.name}</td><td>{r.run_at.slice(0, 16)}</td>
                <td className={`num ${cls(r.cagr_pct)}`}>{pct(r.cagr_pct)}</td><td className="num">{fmt(r.sharpe)}</td>
                <td className="num">{fmt(r.max_drawdown_pct, 1)}%</td><td className={`num ${cls(r.excess_vs_nifty500)}`}>{pct(r.excess_vs_nifty500)}</td></tr>))}
            </tbody></table>)}
      </div>
    </>
  );
}
