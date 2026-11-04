import React, { useEffect, useRef, useState } from "react";

const WS_BASE = "ws://localhost:8000";
const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "–" : Number(v).toFixed(d));
const big = (v) => (v === null || v === undefined ? "–" : Number(v).toLocaleString("en-IN"));
const sign = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");

const REGIME_TEXT = {
  new_longs: "Price ↑ OI ↑ fresh longs",
  short_covering: "Price ↑ OI ↓ short covering",
  new_shorts: "Price ↓ OI ↑ fresh shorts",
  long_liquidation: "Price ↓ OI ↓ long liquidation",
  neutral: "OI flat",
  "n/a": "no futures OI",
};

const STATUS_TEXT = {
  connecting: "connecting…",
  connected: "live",
  reconnecting: "reconnecting…",
  auth_error: "token rejected",
  closed: "disconnected",
  stopped: "stopped",
  idle: "starting…",
};

/** What the live price means for the levels from the daily analysis. */
function levelAlerts(ltp, levels) {
  if (!ltp || !levels) return [];
  const out = [];
  const { gtiDir, gtiLine, waveDir, waveLabel, stop, target } = levels;
  if (gtiLine) {
    if (gtiDir === 1 && ltp < gtiLine) out.push({ cls: "down", text: `Below the GTI line (${fmt(gtiLine)}): GTI flips DOWN if it closes here` });
    if (gtiDir === -1 && ltp > gtiLine) out.push({ cls: "up", text: `Above the GTI line (${fmt(gtiLine)}): GTI flips UP if it closes here` });
  }
  if (waveLabel && stop && target) {
    const hitStop = waveDir === 1 ? ltp <= stop : ltp >= stop;
    const hitTarget = waveDir === 1 ? ltp >= target : ltp <= target;
    if (hitStop) out.push({ cls: "down", text: `${waveLabel} stop ${fmt(stop)} broken` });
    else if (hitTarget) out.push({ cls: "up", text: `${waveLabel} target ${fmt(target)} reached` });
    else {
      const toTarget = (Math.abs(target - ltp) / ltp) * 100;
      const toStop = (Math.abs(ltp - stop) / ltp) * 100;
      out.push({ cls: "", text: `${waveLabel}: ${fmt(toTarget, 1)}% to target, ${fmt(toStop, 1)}% to stop` });
    }
  }
  return out;
}

function LiveQuote({ symbol, demo, levels, demoBase }) {
  const [status, setStatus] = useState({ status: "connecting", error: null });
  const [error, setError] = useState(null);
  const [legs, setLegs] = useState(null);
  const [eq, setEq] = useState(null);
  const [fut, setFut] = useState(null);
  const [live, setLive] = useState(null);
  const [market, setMarket] = useState({});
  const [flash, setFlash] = useState("");
  const lastLtp = useRef(null);

  useEffect(() => {
    let ws;
    let closedByUs = false;
    let retry = 0;
    let timer;

    const connect = () => {
      const source = demo ? "mock" : "upstox";
      const base = demo && demoBase ? `&base=${demoBase}` : "";
      ws = new WebSocket(`${WS_BASE}/ws/live?symbols=${encodeURIComponent(symbol)}&source=${source}${base}`);
      setStatus({ status: "connecting", error: null });
      ws.onmessage = (ev) => {
        const m = JSON.parse(ev.data);
        if (m.type === "hello") {
          retry = 0;
          setError(null);
          setLegs(m.symbols[0]);
        } else if (m.type === "feed_status") {
          setStatus({ status: m.status, error: m.error });
        } else if (m.type === "market_status") {
          setMarket(m.market_status);
        } else if (m.type === "error") {
          closedByUs = true; // configuration problem: retrying won't help
          setError(m.message);
        } else if (m.type === "tick") {
          if (m.leg === "EQ") setEq(m.tick);
          else setFut(m.tick);
          setLive(m.live);
          if (m.live.ltp !== null && lastLtp.current !== null && m.live.ltp !== lastLtp.current) {
            setFlash(m.live.ltp > lastLtp.current ? "flash-up" : "flash-down");
          }
          lastLtp.current = m.live.ltp;
        }
      };
      ws.onclose = () => {
        if (closedByUs) return;
        setStatus({ status: "closed", error: null });
        retry = Math.min(retry + 1, 6);
        timer = setTimeout(connect, 1000 * 2 ** (retry - 1));
      };
    };
    connect();
    return () => {
      closedByUs = true;
      clearTimeout(timer);
      if (ws) ws.close();
    };
  }, [symbol, demo, demoBase]);

  useEffect(() => {
    if (!flash) return undefined;
    const t = setTimeout(() => setFlash(""), 400);
    return () => clearTimeout(t);
  }, [flash]);

  if (error) {
    return (
      <div className="live">
        <span className="dot off" /> <b>Live feed unavailable:</b> {error}
      </div>
    );
  }

  const px = eq || fut;
  const alerts = levelAlerts(live && live.ltp, levels);
  const st = status.status;
  const dotClass = st === "connected" ? "on" : st === "auth_error" ? "off" : "wait";
  const seg = market.NSE_EQ || market.NSE_FO;

  return (
    <div className="live">
      <div className="live-head">
        <span className={`dot ${dotClass}`} /> {STATUS_TEXT[st] || st}
        {demo && " (mock ticks)"}
        {seg && <span className="muted"> · market {seg.replace(/_/g, " ").toLowerCase()}</span>}
        {status.error && <span className="error"> · {status.error}</span>}
      </div>
      {!px ? (
        <div className="muted">Waiting for the first tick… (outside market hours there may be none)</div>
      ) : (
        <div className="live-grid">
          <div>
            <div className={`ltp ${flash}`}>₹{fmt(live.ltp)}</div>
            <div className={sign(live.change_pct)}>{live.change_pct > 0 ? "+" : ""}{fmt(live.change_pct)}%</div>
          </div>
          <div className="kv">
            <span>Bid / Ask</span><b>{fmt(px.bid)} / {fmt(px.ask)}</b>
            <span>Day O/H/L</span><b>{px.day ? `${fmt(px.day.open)} / ${fmt(px.day.high)} / ${fmt(px.day.low)}` : "–"}</b>
            <span>Volume</span><b>{big(px.volume)}</b>
          </div>
          <div className="kv">
            <span>{(legs && legs.fut_symbol) || "Future"}</span>
            <b>{fut ? `₹${fmt(fut.ltp)}` : "–"}{fut && eq ? ` (basis ${fmt(fut.ltp - eq.ltp)})` : ""}</b>
            <span>Open interest</span>
            <b>{big(live.oi)}{live.oi_change_pct !== null && (
              <span className={sign(live.oi_change_pct)}> ({live.oi_change_pct > 0 ? "+" : ""}{fmt(live.oi_change_pct)}% vs prev day)</span>
            )}</b>
            <span>Live OI read</span><b>{REGIME_TEXT[live.regime] || live.regime}</b>
          </div>
        </div>
      )}
      {alerts.length > 0 && (
        <ul className="alerts">
          {alerts.map((a, i) => <li key={i} className={a.cls}>{a.text}</li>)}
        </ul>
      )}
    </div>
  );
}

export default LiveQuote;
