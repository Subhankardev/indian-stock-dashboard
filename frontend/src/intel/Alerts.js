import React, { useCallback, useEffect, useState } from "react";
import { getJSON } from "./api";
import { DemoBanner, ErrorBox } from "./Common";

const KIND = { special: "Special situation", ranking_entry: "New in rankings", paper_open: "Paper trade set",
  paper_fill: "Paper fill", paper_exit: "Paper exit", stale_data: "Stale data", test: "Test" };

export default function Alerts({ onOpen }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  const [msg, setMsg] = useState(null);
  const load = useCallback(() => getJSON("/api/intel/alerts").then(setD).catch(setErr), []);
  useEffect(() => { load(); }, [load]);
  const post = (path) => getJSON(path, { method: "POST" }).then((r) => { setMsg(JSON.stringify(r.generated || r)); load(); }).catch((e) => setMsg(e.message));
  if (err) return <ErrorBox error={err.message} />;
  if (!d) return <div className="panel muted">Loading…</div>;
  const tg = d.telegram;
  return (
    <>
      <DemoBanner demo={d.demo} />
      <div className="panel">
        <div className="panel-head"><h3>Alerts</h3>
          <span className={`pill ${tg.configured ? "ok" : "off"}`}>Telegram {tg.configured ? `on · ${tg.sent || 0} sent` : "off"}</span>
          <div className="controls"><button onClick={() => post("/api/intel/alerts/run")}>Generate & send now</button>
            <button onClick={() => post("/api/intel/alerts/test")}>Telegram test</button></div></div>
        <div className="muted small-text">Run daily with <code>python -m intel.daily</code> (download → paper update → alerts). Telegram needs
          <code> TELEGRAM_BOT_TOKEN</code> and <code>TELEGRAM_CHAT_ID</code> in backend/.env. Kinds sent: {d.kinds.join(", ")}.
          {tg.last_error && <span className="down"> Last error: {tg.last_error}</span>}</div>
        {msg && <div className="small-text">{msg}</div>}
        {d.alerts.length === 0 ? <div className="empty">No alerts yet.</div> : (
          <ul className="alert-feed">{d.alerts.map((a) => (
            <li key={a.id} className="alert-card">
              <div className="alert-top"><span className={`badge ${a.direction === "bullish" ? "up" : a.direction === "bearish" ? "down" : ""}`}>{KIND[a.kind] || a.kind}</span>
                <span className="muted small-text">{a.date} {a.sent ? "· sent" : ""}</span></div>
              <div>{a.symbol ? <b className="clickable" onClick={() => onOpen(a.symbol)}>{a.title}</b> : <b>{a.title}</b>}</div>
              {a.detail && <div className="muted small-text">{a.detail}</div>}
            </li>))}
          </ul>)}
      </div>
    </>
  );
}
