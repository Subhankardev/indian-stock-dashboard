import React, { useCallback, useEffect, useRef, useState } from "react";

const API = "http://localhost:8000";
const POLL_MS = 15_000;
const fmt = (v, d = 2) => (v === null || v === undefined ? "–" : Number(v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d }));

const KIND_LABEL = { forming: "Forming", confirmed: "Confirmed", faded: "Faded", test: "Test" };

function notificationTitle(a) {
  if (a.kind === "test") return "🔔 Test alert";
  const icon = a.kind === "faded" ? "⚪" : a.side === "BUY" ? "🟢" : "🔴";
  return `${icon} ${a.side} ${a.kind} — ${a.symbol}`;
}

function notificationBody(a) {
  if (a.kind === "test" || a.kind === "faded") return a.reason;
  const lv = a.stop && a.target ? ` · stop ${fmt(a.stop)} · target ${fmt(a.target)}` : "";
  return `@ ${fmt(a.price)}${lv}${a.kind === "forming" ? " (if the day closes here)" : ""}\n${a.reason}`;
}

function ago(ts) {
  const s = Math.max(0, (Date.now() - new Date(ts).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(ts).toLocaleDateString([], { day: "numeric", month: "short" });
}

/** Merge new alerts into the list by id (newest first) - never duplicates. */
function merge(prev, incoming) {
  const byId = new Map(prev.map((a) => [a.id, a]));
  incoming.forEach((a) => byId.set(a.id, a));
  return [...byId.values()].sort((a, b) => b.id - a.id).slice(0, 200);
}

/** A short two-tone chime, so an alert is noticed without looking at the screen. */
function chime() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    [880, 1320].forEach((f, i) => {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      const t0 = ctx.currentTime + i * 0.18;
      o.frequency.value = f;
      g.gain.setValueAtTime(0.0001, t0);
      g.gain.exponentialRampToValueAtTime(0.2, t0 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.16);
      o.connect(g).connect(ctx.destination);
      o.start(t0);
      o.stop(t0 + 0.18);
    });
  } catch (e) {
    /* audio is blocked until the user interacts with the page */
  }
}

function AlertCard({ a, onSelect, onDismiss }) {
  const side = a.kind === "test" ? "test" : a.side === "BUY" ? "buy" : "sell";
  return (
    <li className={`alert-card ${a.kind} ${side}`}>
      <div className="alert-top">
        {a.kind === "test" ? (
          <span className="badge test">TEST</span>
        ) : (
          <span className={`badge ${side}`}>{a.side}</span>
        )}
        {a.kind !== "test" && (
          <button className="sym" onClick={() => onSelect && onSelect(a.symbol)}>{a.symbol}</button>
        )}
        <span className={`kind ${a.kind}`}>{KIND_LABEL[a.kind] || a.kind}</span>
        <span className="when" title={new Date(a.ts).toLocaleString()}>{ago(a.ts)}</span>
        <button className="dismiss" title="Dismiss" onClick={() => onDismiss(a.id)}>×</button>
      </div>
      {a.kind === "test" || a.kind === "faded" ? (
        <div className="alert-reason">{a.reason}</div>
      ) : (
        <>
          <div className="alert-levels">
            <span>@ <b>{fmt(a.price)}</b></span>
            {a.stop !== null && a.stop !== undefined && <span>Stop <b className="down">{fmt(a.stop)}</b></span>}
            {a.target !== null && a.target !== undefined && <span>Target <b className="up">{fmt(a.target)}</b></span>}
          </div>
          <div className="alert-reason">
            {a.kind === "forming" && <em>If the day closes here · </em>}
            {a.reason}
          </div>
        </>
      )}
    </li>
  );
}

function AlertsPanel({ currentSymbol, onSelect }) {
  const [alerts, setAlerts] = useState([]);
  const [watchlist, setWatchlist] = useState([]);
  const [scanner, setScanner] = useState(null);
  const [telegram, setTelegram] = useState(null);
  const [error, setError] = useState(null);
  const [add, setAdd] = useState("");
  const [tab, setTab] = useState("signals");
  const [perm, setPerm] = useState(typeof Notification === "undefined" ? "unsupported" : Notification.permission);
  const [sound, setSound] = useState(true);
  const [, setTick] = useState(0); // re-render every minute so "5m ago" stays fresh
  const lastId = useRef(null);
  const inFlight = useRef(false);
  const soundRef = useRef(sound);
  soundRef.current = sound;

  const notify = useCallback((list) => {
    if (typeof Notification !== "undefined" && Notification.permission === "granted") {
      list.slice().reverse().forEach((a) => {
        const n = new Notification(notificationTitle(a), { body: notificationBody(a), tag: `alert-${a.id}` });
        n.onclick = () => {
          window.focus();
          if (a.symbol !== "TEST" && onSelect) onSelect(a.symbol);
        };
      });
    }
    if (list.length && soundRef.current) chime();
  }, [onSelect]);

  const poll = useCallback(() => {
    if (inFlight.current) return; // never run two polls at once (that caused duplicates)
    inFlight.current = true;
    const since = lastId.current ?? 0;
    fetch(`${API}/api/alerts?since=${since}&limit=${since ? 100 : 50}`)
      .then((r) => r.json())
      .then((d) => {
        setError(null);
        setWatchlist(d.watchlist);
        setScanner(d.scanner);
        setTelegram(d.telegram || null);
        const fresh = d.alerts.filter((a) => a.id > (lastId.current ?? 0));
        setAlerts((prev) => merge(prev, d.alerts));
        if (lastId.current !== null && fresh.length) notify(fresh); // no notifications for history
        lastId.current = Math.max(lastId.current ?? 0, d.last_id);
      })
      .catch(() => setError("Backend not reachable — is uvicorn running?"))
      .finally(() => { inFlight.current = false; });
  }, [notify]);

  useEffect(() => {
    poll();
    const t = setInterval(poll, POLL_MS);
    const m = setInterval(() => setTick((n) => n + 1), 60_000);
    return () => { clearInterval(t); clearInterval(m); };
  }, [poll]);

  const call = (method, path, payload) =>
    fetch(`${API}${path}`, {
      method,
      headers: payload ? { "Content-Type": "application/json" } : undefined,
      body: payload ? JSON.stringify(payload) : undefined,
    })
      .then(async (r) => {
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || r.statusText);
        return d;
      })
      .then((d) => {
        if (d.watchlist) setWatchlist(d.watchlist);
        setError(null);
        return d;
      })
      .catch((e) => { setError(e.message); })
      .finally(() => setTimeout(poll, 300));

  const watch = (sym) => sym && sym.trim() && call("POST", `/api/alerts/watchlist/${encodeURIComponent(sym.trim().toUpperCase())}`);
  const unwatch = (sym) => call("DELETE", `/api/alerts/watchlist/${encodeURIComponent(sym)}`);
  const dismiss = (id) => {
    setAlerts((prev) => prev.filter((a) => a.id !== id));
    call("DELETE", `/api/alerts/${id}`);
  };
  const clearKind = (kind) => {
    setAlerts((prev) => (kind === "all" ? [] : prev.filter((a) => a.kind !== kind)));
    call("DELETE", `/api/alerts?kind=${kind}`);
  };
  const askPermission = () => Notification.requestPermission().then(setPerm);

  const errors = scanner && scanner.errors ? Object.entries(scanner.errors) : [];
  const signals = alerts.filter((a) => a.kind !== "test");
  const tests = alerts.length - signals.length;
  const shown = tab === "signals" ? signals : alerts;
  const lastRun = scanner && scanner.last_run ? new Date(scanner.last_run).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : null;

  return (
    <div className="panel alerts-panel">
      <div className="panel-head">
        <h3>🔔 Signal alerts</h3>
        {scanner && <span className={`pill ${scanner.phase === "open" ? "ok" : "off"}`}>{scanner.phase === "open" ? "Live · every 1 min" : "Market closed"}</span>}
      </div>
      {lastRun && <div className="muted small-text">Last scan {lastRun}{errors.length ? "" : " · all OK"}</div>}

      <div className="section-label">Watchlist</div>
      <div className="watchlist">
        {watchlist.map((s) => (
          <span key={s} className={`chip ${errors.some(([k]) => k === s) ? "chip-err" : ""}`}
                title={(errors.find(([k]) => k === s) || [])[1] || `Open ${s}`}>
            <button className="link" onClick={() => onSelect && onSelect(s)}>{s}</button>
            <button className="x" title={`Stop watching ${s}`} onClick={() => unwatch(s)}>×</button>
          </span>
        ))}
        <form onSubmit={(e) => { e.preventDefault(); watch(add); setAdd(""); }}>
          <input className="chip-input" value={add} onChange={(e) => setAdd(e.target.value)} placeholder="+ add symbol" />
        </form>
      </div>
      {currentSymbol && !watchlist.includes(currentSymbol) && (
        <button className="primary block" onClick={() => watch(currentSymbol)}>+ Watch {currentSymbol}</button>
      )}
      {watchlist.length === 0 && !currentSymbol && (
        <p className="muted small-text">Add symbols to get alerted when a signal forms or is confirmed.</p>
      )}

      {errors.length > 0 && (
        <p className="warn small-text" title={errors.map(([s, e]) => `${s}: ${e}`).join("\n\n")}>
          Couldn't scan {errors.map(([s]) => s).join(", ")}: {errors[0][1].slice(0, 120)}
        </p>
      )}
      {error && <p className="error small-text">{error}</p>}

      <div className="section-label">Delivery</div>
      <div className="channels">
        <div className="channel">
          <span className={`dot ${perm === "granted" ? "on" : perm === "denied" ? "off" : "wait"}`} />
          Desktop{" "}
          {perm === "default" && <button className="link-btn" onClick={askPermission}>enable</button>}
          {perm === "denied" && <span className="muted">· blocked in browser settings</span>}
          <label className="muted sound"><input type="checkbox" checked={sound} onChange={(e) => setSound(e.target.checked)} /> sound</label>
        </div>
        <div className="channel">
          <span className={`dot ${telegram && telegram.configured ? (telegram.last_error ? "off" : "on") : "wait"}`} />
          Telegram{" "}
          {telegram && telegram.configured
            ? <span className="muted">· on · {telegram.sent} sent</span>
            : <span className="muted">· off (set in backend/.env)</span>}
        </div>
        {telegram && telegram.last_error && <p className="error small-text">Telegram: {telegram.last_error}</p>}
        <div className="channel-actions">
          <button className="ghost" onClick={() => call("POST", "/api/alerts/test")}>Test desktop</button>
          {telegram && telegram.configured && <button className="ghost" onClick={() => call("POST", "/api/alerts/telegram/test")}>Test Telegram</button>}
          <button className="ghost" onClick={() => call("POST", "/api/alerts/scan")}>Scan now</button>
        </div>
      </div>

      <div className="tabs">
        <button className={tab === "signals" ? "active" : ""} onClick={() => setTab("signals")}>Signals ({signals.length})</button>
        <button className={tab === "all" ? "active" : ""} onClick={() => setTab("all")}>All ({alerts.length})</button>
        <span className="spacer" />
        {tests > 0 && <button className="link-btn" onClick={() => clearKind("test")}>Clear tests</button>}
        {tab === "all" && alerts.length > 0 && <button className="link-btn" onClick={() => clearKind("all")}>Clear all</button>}
      </div>

      {shown.length === 0 ? (
        <p className="empty muted">
          {tab === "signals"
            ? "No signals yet. Forming signals appear during market hours; confirmed ones after the 15:30 close."
            : "No alerts."}
        </p>
      ) : (
        <ul className="alert-feed">
          {shown.slice(0, 50).map((a) => <AlertCard key={a.id} a={a} onSelect={onSelect} onDismiss={dismiss} />)}
        </ul>
      )}
    </div>
  );
}

export default AlertsPanel;
