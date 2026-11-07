import React, { useCallback, useEffect, useState } from "react";
import StockTable from "./components/StockTable";
import StockDetail from "./components/StockDetail";
import WaveOIPanel from "./components/WaveOIPanel";
import OptionChainPanel from "./components/OptionChainPanel";
import AlertsPanel from "./components/AlertsPanel";
import './App.css';

const API = "http://localhost:8000";
const QUICK_PICKS = ["NIFTY", "BANKNIFTY", "RELIANCE", "HDFCBANK", "INFY", "TCS"];

/** Top-bar pills: is each data source / channel configured, and is the market open? */
function StatusPills() {
  const [cfg, setCfg] = useState(null);
  const [phase, setPhase] = useState(null);

  useEffect(() => {
    const load = () => {
      fetch(`${API}/api/config`).then((r) => r.json()).then(setCfg).catch(() => setCfg(false));
      fetch(`${API}/api/alerts?limit=1`).then((r) => r.json())
        .then((d) => setPhase(d.scanner && d.scanner.phase)).catch(() => {});
    };
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, []);

  if (cfg === false) return <div className="pills"><span className="pill bad">Backend offline</span></div>;
  if (!cfg || !cfg.settings) return null;
  const s = cfg.settings;
  const problem = (k) => s[k] && s[k].problem;
  const upstox = problem("UPSTOX_ACCESS_TOKEN") ? "bad" : s.UPSTOX_ACCESS_TOKEN.set ? "ok" : "off";
  const telegram = s.TELEGRAM_BOT_TOKEN.set && s.TELEGRAM_CHAT_ID.set ? "ok" : "off";
  return (
    <div className="pills">
      <span className={`pill ${upstox}`} title={problem("UPSTOX_ACCESS_TOKEN") || "Upstox access token (backend/.env)"}>
        Upstox {upstox === "ok" ? "connected" : upstox === "bad" ? "token invalid" : "no token"}
      </span>
      <span className={`pill ${telegram}`} title="Telegram alerts (backend/.env)">Telegram {telegram === "ok" ? "on" : "off"}</span>
      {phase && <span className={`pill ${phase === "open" ? "ok" : "off"}`}>Market {phase}</span>}
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
        <StatusPills />
      </header>

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
