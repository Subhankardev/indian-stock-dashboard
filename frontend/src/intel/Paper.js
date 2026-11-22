import React, { useCallback, useEffect, useState } from "react";
import { getJSON, fmt, cls } from "./api";
import { DemoBanner, ErrorBox, Legend, LineChart } from "./Common";

export default function Paper({ onOpen }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  const [msg, setMsg] = useState(null);
  const load = useCallback(() => getJSON("/api/intel/paper").then(setD).catch(setErr), []);
  useEffect(() => { load(); }, [load]);
  const act = (path, label) => getJSON(path, { method: "POST" }).then((r) => { setMsg(`${label}: ${JSON.stringify(r.opened ? { opened: r.opened.length, skipped: r.skipped.length, message: r.message } : r)}`); load(); }).catch((e) => setMsg(e.message));
  if (err) return <ErrorBox error={err.message} />;
  if (!d) return <div className="panel muted">Loading…</div>;
  const r = d.report;
  return (
    <>
      <DemoBanner demo={d.demo} />
      <div className="panel">
        <div className="panel-head"><h3>Paper trading</h3>
          <div className="controls">
            <button onClick={() => act("/api/intel/paper/update", "Update")}>Process new bars</button>
            <button onClick={() => act("/api/intel/paper/auto", "Auto")}>Auto-open top ranked</button></div></div>
        <div className="muted small-text">Simulated trades only — nothing is sent to a broker. Fills are conservative: breakout entries fill only if the
          next sessions trade through the trigger; a bar touching both stop and target counts as a stop; P&L is after the full cost model.
          Auto-open uses the Rankings (tested factors only) and the exposure limits.</div>
        {msg && <div className="small-text">{msg}</div>}
        <div className="cards">
          <div className="card"><div className="muted small-text">Closed trades</div><div className="big">{r.trades || 0}</div></div>
          <div className="card"><div className="muted small-text">Win rate</div><div className="big">{r.win_rate === undefined ? "–" : `${fmt(r.win_rate * 100, 0)}%`}</div></div>
          <div className="card"><div className="muted small-text">Total P&L (after costs)</div><div className={`big ${cls(r.total_pnl)}`}>₹{fmt(r.total_pnl || 0, 0)}</div>
            <div className="muted small-text">costs ₹{fmt(r.total_costs || 0, 0)}</div></div>
          <div className="card"><div className="muted small-text">Average R · expectancy</div><div className="big">{fmt(r.avg_r)}R</div>
            <div className="muted small-text">₹{fmt(r.expectancy, 0)} per trade</div></div>
          <div className="card"><div className="muted small-text">Max drawdown</div><div className="big down">{r.max_drawdown_pct === undefined ? "–" : `${fmt(r.max_drawdown_pct, 1)}%`}</div></div>
          <div className="card"><div className="muted small-text">Open exposure</div><div className="big">₹{fmt(r.open_exposure, 0)}</div></div>
        </div>
        {r.equity && r.equity.length > 1 && (<>
          <Legend items={[{ label: "Paper equity (closed trades)", color: "var(--primary)" }]} />
          <LineChart dates={r.equity.map((x) => x.date)} height={150} yFormat={(v) => `${(v / 1e5).toFixed(2)}L`}
            series={[{ key: "eq", color: "var(--primary)", values: r.equity.map((x) => x.equity) }]} /></>)}
      </div>
      <div className="panel">
        <div className="panel-head"><h3>Trade log</h3></div>
        {d.trades.length === 0 ? <div className="empty">No paper trades yet. Open one from a stock's Trade setup panel.</div> : (
          <div className="table-wrap"><table className="grid-table">
            <thead><tr><th>#</th><th>Symbol</th><th>Side</th><th>Status</th><th>Set</th><th className="num">Trigger</th><th>Filled</th>
              <th className="num">Entry</th><th className="num">Stop</th><th className="num">Qty</th><th>Closed</th><th className="num">Exit</th>
              <th>Reason</th><th className="num">P&L</th><th className="num">R</th><th></th></tr></thead>
            <tbody>{d.trades.map((t) => (
              <tr key={t.id}>
                <td className="muted">{t.id}</td><td className="clickable" onClick={() => onOpen(t.symbol)}><b>{t.symbol}</b></td>
                <td>{t.direction === "bullish" ? "Long" : "Short"}</td><td><span className={`pill ${t.status === "open" ? "ok" : t.status === "pending" ? "warn" : "off"}`}>{t.status}</span></td>
                <td>{t.opened}</td><td className="num">{fmt(t.trigger)}</td><td>{t.filled || "–"}</td><td className="num">{fmt(t.entry)}</td>
                <td className="num">{fmt(t.stop)}</td><td className="num">{fmt(t.qty, 0)}</td><td>{t.closed || "–"}</td><td className="num">{fmt(t.exit_price)}</td>
                <td>{t.exit_reason || ""}</td><td className={`num ${cls(t.pnl)}`}>{t.pnl === null ? "–" : fmt(t.pnl, 0)}</td>
                <td className={`num ${cls(t.r_multiple)}`}>{t.r_multiple === null ? "–" : fmt(t.r_multiple)}</td>
                <td>{["pending", "open"].includes(t.status) && <button className="link-btn" onClick={() => act(`/api/intel/paper/${t.id}/close`, "Close")}>{t.status === "pending" ? "cancel" : "close"}</button>}</td>
              </tr>))}
            </tbody></table></div>)}
      </div>
    </>
  );
}
