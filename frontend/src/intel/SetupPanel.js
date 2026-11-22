import React, { useEffect, useState } from "react";
import { getJSON, fmt } from "./api";

export default function SetupPanel({ symbol }) {
  const [dir, setDir] = useState("bullish");
  const [s, setS] = useState(null);
  const [msg, setMsg] = useState(null);
  useEffect(() => { setS(null); setMsg(null); getJSON(`/api/intel/stock/${encodeURIComponent(symbol)}/setup?direction=${dir}`).then(setS).catch(() => setS(false)); }, [symbol, dir]);
  if (!s) return null;
  const paper = () => getJSON("/api/intel/paper", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbol, direction: dir }) }).then((r) => setMsg(`Paper trade #${r.id} set (pending until the trigger trades).`)).catch((e) => setMsg(e.message));
  return (
    <div className="panel">
      <div className="panel-head"><h3>Trade setup · paper only</h3>
        <div className="controls">{["bullish", "bearish"].map((d) => (
          <button key={d} className={`chip-btn ${dir === d ? "active" : ""}`} onClick={() => setDir(d)}>{d === "bullish" ? "Long" : "Short"}</button>))}</div></div>
      {!s.valid ? <div className="muted small-text">{s.reason}</div> : (
        <>
          <div className="cards">
            <div className="card"><div className="muted small-text">Trigger ({s.entry_rule === "breakout" ? "trade through next session" : "next open"})</div><div className="big">{fmt(s.trigger)}</div></div>
            <div className="card"><div className="muted small-text">Stop ({s.stop_pct}% · {s.atr14} ATR)</div><div className="big down">{fmt(s.stop)}</div></div>
            <div className="card"><div className="muted small-text">Targets ({s.config.targets_r.join("R, ")}R)</div><div className="big up">{s.targets.map((t) => fmt(t)).join(" / ")}</div></div>
            <div className="card"><div className="muted small-text">Quantity (sized by {s.sized_by})</div><div className="big">{s.qty.toLocaleString("en-IN")}</div>
              <div className="muted small-text">₹{fmt(s.position_value, 0)} · {s.position_pct}% of capital · risk ₹{fmt(s.risk_amount, 0)}</div></div>
            <div className="card"><div className="muted small-text">Costs at target 1 / at stop</div><div className="big">₹{fmt(s.costs_at_target1, 0)} / ₹{fmt(s.costs_at_stop, 0)}</div>
              <div className="muted small-text">net reward:risk at T1 {fmt(s.net_reward_risk_t1)}</div></div>
          </div>
          {s.notes.length > 0 && <div className="warn small-text">Note: {s.notes.join("; ")}</div>}
          {s.limit_problems.length > 0 && <div className="warn small-text">Exposure limits: {s.limit_problems.join("; ")}</div>}
          <div className="controls">
            <button className="primary" onClick={paper} disabled={s.limit_problems.length > 0}>Paper trade this setup</button>
            <span className="muted small-text">Capital ₹{fmt(s.config.capital, 0)} · risk {s.config.risk_per_trade_pct}% per trade · time exit after {s.config.time_exit_sessions} sessions.
              Simulated only — no order is sent anywhere.</span>
          </div>
          {msg && <div className="small-text">{msg}</div>}
        </>)}
    </div>
  );
}
