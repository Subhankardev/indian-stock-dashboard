import React, { useCallback, useEffect, useRef, useState } from "react";

const API = "http://localhost:8000";
const POLL_MS = 15_000;
const fmt = (v, d = 2) => (v === null || v === undefined ? "–" : Number(v).toFixed(d));

const KIND_TEXT = {
  forming: "forming",
  confirmed: "confirmed",
  faded: "faded",
  test: "test",
};

function title(a) {
  if (a.kind === "test") return "🔔 Test alert";
  const icon = a.kind === "faded" ? "⚪" : a.side === "BUY" ? "🟢" : "🔴";
  return `${icon} ${a.side} ${KIND_TEXT[a.kind] || a.kind} — ${a.symbol}`;
}

function body(a) {
  if (a.kind === "test" || a.kind === "faded") return a.reason;
  const lv = a.stop && a.target ? ` · stop ${fmt(a.stop)} · target ${fmt(a.target)}` : "";
  const early = a.kind === "forming" ? " (if the day closes here)" : "";
  return `@ ${fmt(a.price)}${lv}${early}\n${a.reason}`;
}

/** A short two-tone chime, so an alert is noticed without looking at the screen. */
function chime() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    [880, 1320].forEach((f, i) => {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.frequency.value = f;
      g.gain.setValueAtTime(0.0001, ctx.currentTime + i * 0.18);
      g.gain.exponentialRampToValueAtTime(0.2, ctx.currentTime + i * 0.18 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + i * 0.18 + 0.16);
      o.connect(g).connect(ctx.destination);
      o.start(ctx.currentTime + i * 0.18);
      o.stop(ctx.currentTime + i * 0.18 + 0.18);
    });
  } catch (e) {
    /* audio blocked until the user interacts with the page: ignore */
  }
}

function AlertsPanel({ currentSymbol, onSelect }) {
  const [alerts, setAlerts] = useState([]);
  const [watchlist, setWatchlist] = useState([]);
  const [scanner, setScanner] = useState(null);
  const [telegram, setTelegram] = useState(null);
  const [error, setError] = useState(null);
  const [add, setAdd] = useState("");
  const [perm, setPerm] = useState(typeof Notification === "undefined" ? "unsupported" : Notification.permission);
  const [sound, setSound] = useState(true);
  const lastId = useRef(null);
  const soundRef = useRef(sound);
  soundRef.current = sound;

  const notify = useCallback((list) => {
    list.slice().reverse().forEach((a) => {
      if (typeof Notification !== "undefined" && Notification.permission === "granted") {
        const n = new Notification(title(a), { body: body(a), tag: `alert-${a.id}` });
        n.onclick = () => {
          window.focus();
          if (a.symbol !== "TEST" && onSelect) onSelect(a.symbol);
        };
      }
    });
    if (list.length && soundRef.current) chime();
  }, [onSelect]);

  const poll = useCallback(() => {
    const since = lastId.current ?? 0;
    fetch(`${API}/api/alerts?since=${since}&limit=${since ? 100 : 30}`)
      .then((r) => r.json())
      .then((d) => {
        setError(null);
        setWatchlist(d.watchlist);
        setScanner(d.scanner);
        setTelegram(d.telegram || null);
        if (lastId.current === null) {
          setAlerts(d.alerts); // history on first load: show, don't notify
        } else if (d.alerts.length) {
          setAlerts((prev) => [...d.alerts, ...prev].slice(0, 100));
          notify(d.alerts);
        }
        lastId.current = d.last_id;
      })
      .catch(() => setError("Backend not reachable"));
  }, [notify]);

  useEffect(() => {
    poll();
    const t = setInterval(poll, POLL_MS);
    return () => clearInterval(t);
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
        setTimeout(poll, 300);
        return d;
      })
      .catch((e) => {
        setError(e.message);
        setTimeout(poll, 300); // refresh status (e.g. Telegram last error) after a failure too
      });

  const watch = (sym) => sym && call("POST", `/api/alerts/watchlist/${encodeURIComponent(sym.trim().toUpperCase())}`);
  const unwatch = (sym) => call("DELETE", `/api/alerts/watchlist/${encodeURIComponent(sym)}`);
  const askPermission = () => Notification.requestPermission().then(setPerm);

  const errors = scanner && scanner.errors ? Object.entries(scanner.errors) : [];
  const lastRun = scanner && scanner.last_run ? new Date(scanner.last_run).toLocaleTimeString() : null;

  return (
    <div className="panel alerts-panel">
      <h3>
        🔔 Signal alerts{" "}
        <small>
          {scanner && (scanner.phase === "open" ? "market open · scanning every minute" : "market closed · scanning every 10 min")}
          {lastRun && ` · last scan ${lastRun}`}
        </small>
      </h3>

      <div className="alerts-controls">
        <span className="muted">Watching:</span>
        {watchlist.length === 0 && <span className="muted">nothing yet</span>}
        {watchlist.map((s) => (
          <span key={s} className={`chip ${errors.some(([k]) => k === s) ? "chip-err" : ""}`}>
            <button className="link" onClick={() => onSelect && onSelect(s)}>{s}</button>
            <button className="x" title={`Stop watching ${s}`} onClick={() => unwatch(s)}>×</button>
          </span>
        ))}
        {currentSymbol && !watchlist.includes(currentSymbol) && (
          <button className="small" onClick={() => watch(currentSymbol)}>+ Watch {currentSymbol}</button>
        )}
        <form onSubmit={(e) => { e.preventDefault(); watch(add); setAdd(""); }} className="inline">
          <input value={add} onChange={(e) => setAdd(e.target.value)} placeholder="add symbol" size={10} />
        </form>
        <span className="spacer" />
        {perm === "default" && <button className="small" onClick={askPermission}>Enable desktop notifications</button>}
        {perm === "denied" && <span className="warn-inline">Notifications blocked in browser settings</span>}
        {perm === "granted" && <span className="muted">desktop notifications on</span>}
        <label className="muted"><input type="checkbox" checked={sound} onChange={(e) => setSound(e.target.checked)} /> sound</label>
        <button className="small" onClick={() => call("POST", "/api/alerts/test")}>Send test alert</button>
        {telegram && telegram.configured && (
          <button className="small" onClick={() => call("POST", "/api/alerts/telegram/test")}>Telegram test</button>
        )}
        <button className="small" onClick={() => call("POST", "/api/alerts/scan")}>Scan now</button>
      </div>

      {telegram && (
        <p className="tg-status">
          {telegram.configured ? (
            <>
              <span className={`dot ${telegram.last_error ? "off" : "on"}`} /> Telegram alerts on → chat {telegram.chat_id}
              <span className="muted"> · {telegram.kinds.join(", ")} · sent {telegram.sent}</span>
              {telegram.last_error && <span className="error"> · last send failed: {telegram.last_error}</span>}
            </>
          ) : (
            <span className="muted">
              Telegram alerts off. To get alerts on your phone, set <code>TELEGRAM_BOT_TOKEN</code> and{" "}
              <code>TELEGRAM_CHAT_ID</code> on the backend (see README → Telegram alerts).
            </span>
          )}
        </p>
      )}
      {error && <p className="error">{error}</p>}
      {errors.length > 0 && (
        <p className="warn" title={errors.map(([s, e]) => `${s}: ${e}`).join("\n\n")}>
          Couldn't scan {errors.map(([s]) => s).join(", ")} on the last run
          {" "}({errors[0][1].split(":")[0]}{errors.length > 1 ? ", …" : ""}) — hover for details
        </p>
      )}

      {alerts.length === 0 ? (
        <p className="muted">
          No alerts yet. Add symbols to the watchlist; you'll be alerted when a Wave · GTI · OI signal is
          forming during market hours and again when it is confirmed at the close.
        </p>
      ) : (
        <ul className="alert-list">
          {alerts.slice(0, 15).map((a) => (
            <li key={a.id} className={`alert ${a.kind} ${a.side === "BUY" ? "buy" : "sell"}`}>
              <span className="when">{new Date(a.ts).toLocaleString([], { dateStyle: "short", timeStyle: "short" })}</span>
              <b className="what">{title(a)}</b>
              <span className="detail">{body(a).replace("\n", " · ")}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default AlertsPanel;
