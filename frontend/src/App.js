import React, { useCallback, useEffect, useState } from "react";
import StockTable from "./components/StockTable";
import StockDetail from "./components/StockDetail";
import WaveOIPanel from "./components/WaveOIPanel";
import OptionChainPanel from "./components/OptionChainPanel";
import AlertsPanel from "./components/AlertsPanel";
import './App.css';

const API = "http://localhost:8000";
const QUICK_PICKS = ["NIFTY", "BANKNIFTY", "RELIANCE", "HDFCBANK", "INFY", "TCS"];

const UPSTOX_LABEL = {
  ok: ["ok", "Upstox connected"],
  limited: ["warn", "Upstox: limited token"],
  expired: ["bad", "Upstox token expired"],
  invalid: ["bad", "Upstox token invalid"],
  missing: ["off", "Upstox: no token"],
  unknown: ["off", "Upstox: unchecked"],
};

const loginUrl = `${API}/api/upstox/login`;

/** Top-bar pills (Upstox token health, Telegram, market) + a banner when Upstox needs a login. */
function useStatus() {
  const [cfg, setCfg] = useState(null);
  const [upstox, setUpstox] = useState(null);
  const [phase, setPhase] = useState(null);

  const load = useCallback((refresh = false) => {
    fetch(`${API}/api/config`).then((r) => r.json()).then(setCfg).catch(() => setCfg(false));
    fetch(`${API}/api/upstox/status${refresh ? "?refresh=true" : ""}`).then((r) => r.json()).then(setUpstox).catch(() => {});
    fetch(`${API}/api/alerts?limit=1`).then((r) => r.json())
      .then((d) => setPhase(d.scanner && d.scanner.phase)).catch(() => {});
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(() => load(), 60_000);
    const onFocus = () => load(true); // e.g. back from the Upstox login tab
    window.addEventListener("focus", onFocus);
    return () => { clearInterval(t); window.removeEventListener("focus", onFocus); };
  }, [load]);
  return { cfg, upstox, phase };
}

function StatusPills({ cfg, upstox, phase }) {
  if (cfg === false) return <div className="pills"><span className="pill bad">Backend offline</span></div>;
  if (!cfg || !cfg.settings) return null;
  const s = cfg.settings;
  const telegram = s.TELEGRAM_BOT_TOKEN.set && s.TELEGRAM_CHAT_ID.set ? "ok" : "off";
  const [cls, label] = upstox ? UPSTOX_LABEL[upstox.state] || ["off", "Upstox"] : ["off", "Upstox: checking…"];
  const until = upstox && upstox.state === "ok" && upstox.expires_at
    ? ` · valid until ${new Date(upstox.expires_at).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}` : "";
  return (
    <div className="pills">
      <span className={`pill ${cls}`} title={(upstox && upstox.message) + until}>{label}</span>
      <span className={`pill ${telegram}`} title="Telegram alerts (backend/.env)">Telegram {telegram === "ok" ? "on" : "off"}</span>
      {phase && <span className={`pill ${phase === "open" ? "ok" : "off"}`}>Market {phase}</span>}
    </div>
  );
}

function UpstoxBanner({ upstox }) {
  if (!upstox || ["ok", "unknown"].includes(upstox.state)) return null;
  return (
    <div className={`banner ${upstox.state === "limited" ? "warn-banner" : "bad-banner"}`}>
      <div>
        <b>{UPSTOX_LABEL[upstox.state][1]}.</b> {upstox.message}
        {upstox.state !== "missing" && " Daily prices still load; the live feed, option chain and longer OI history need a working token."}
      </div>
      {upstox.login_available ? (
        <a className="primary-link" href={loginUrl} target="_blank" rel="noreferrer">Log in with Upstox</a>
      ) : (
        <span className="small-text">
          To enable one-click login, add <code>UPSTOX_API_KEY</code> and <code>UPSTOX_API_SECRET</code> to backend/.env
          and register <code>{upstox.redirect_uri}</code> as your Upstox app's redirect URL.
        </span>
      )}
    </div>
  );
}

function Welcome({ onPick }) {
  return (
    <div className="panel welcome">
      <h2>Analyse a stock or index</h2>
      <p className="muted">
        Search an NSE symbol to see its Elliott-wave structure, Ghost Trade Indicator, open-interest read,
        live price and option chain. Add it to your watchlist on the right to get BUY/SELL alerts.
      </p>
      <div className="quick">
        {QUICK_PICKS.map((s) => <button key={s} className="chip-btn" onClick={() => onPick(s)}>{s}</button>)}
      </div>
    </div>
  );
}

function App() {
  const status = useStatus();
  const [trending, setTrending] = useState([]);
  const [selectedStock, setSelectedStock] = useState(null);
  const [query, setQuery] = useState("");
  const [demo, setDemo] = useState(false);

  useEffect(() => {
    fetch(`${API}/api/trending`)
      .then(res => res.json())
      .then(data => setTrending(Array.isArray(data) ? data : []))
      .catch(() => setTrending([]));
  }, []);

  const pick = useCallback((s) => {
    setDemo(false);
    setQuery(s);
    setSelectedStock(s);
  }, []);

  const onSearch = (e) => {
    e.preventDefault();
    if (query.trim()) setSelectedStock(query.trim().toUpperCase());
  };

  return (
    <div className="App">
      <header className="topbar">
        <div className="brand"><span className="logo">◆</span> Indian Market Analytics</div>
        <form className="search" onSubmit={onSearch}>
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search symbol — e.g. NIFTY, RELIANCE" />
          <button type="submit" className="primary">Analyze</button>
          <label className="toggle" title="Use simulated data (works without Upstox)">
            <input type="checkbox" checked={demo} onChange={(e) => setDemo(e.target.checked)} /> Demo data
          </label>
        </form>
        <StatusPills {...status} />
      </header>
      <UpstoxBanner upstox={status.upstox} />

      <main className="layout">
        <section className="content">
          {selectedStock ? (
            <>
              <div className="symbol-head">
                <h1>{selectedStock}</h1>
                {demo && <span className="pill off">demo data</span>}
              </div>
              {!demo && <StockDetail symbol={selectedStock} />}
              <WaveOIPanel key={`${selectedStock}-${demo}`} symbol={selectedStock} demo={demo} />
              <OptionChainPanel key={`oc-${selectedStock}-${demo}`} symbol={selectedStock} demo={demo} />
            </>
          ) : (
            <Welcome onPick={pick} />
          )}
          {trending.length > 0 && <StockTable stocks={trending} onSelect={pick} />}
        </section>
        <aside className="sidebar">
          <AlertsPanel currentSymbol={demo ? null : selectedStock} onSelect={pick} />
        </aside>
      </main>
    </div>
  );
}

export default App;
