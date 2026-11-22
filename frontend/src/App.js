import React, { useState } from "react";
import Overview from "./intel/Overview";
import Screener from "./intel/Screener";
import StockPage from "./intel/StockPage";
import Research from "./intel/Research";
import Institutional from "./intel/Institutional";
import Derivatives from "./intel/Derivatives";
import "./App.css";

const TABS = [["overview", "Overview"], ["screener", "Screener"], ["institutional", "Institutional"], ["derivatives", "Derivatives"], ["stock", "Stock"], ["research", "Research"]];

function App() {
  const [tab, setTab] = useState("screener");
  const [symbol, setSymbol] = useState("");
  const [input, setInput] = useState("");
  const open = (s) => { setSymbol(s.toUpperCase()); setTab("stock"); };
  return (
    <div>
      <header className="topbar">
        <div className="brand"><span className="logo">◆</span> Institutional Intelligence</div>
        <nav className="nav">{TABS.map(([k, l]) => (
          <button key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)} disabled={k === "stock" && !symbol}>
            {k === "stock" && symbol ? symbol : l}</button>))}</nav>
        <form className="search" onSubmit={(e) => { e.preventDefault(); if (input.trim()) open(input.trim()); }}>
          <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Open a stock, e.g. RELIANCE" />
        </form>
      </header>
      <main className="page">
        {tab === "overview" && <Overview />}
        {tab === "screener" && <Screener onOpen={open} />}
        {tab === "stock" && symbol && <StockPage symbol={symbol} onOpen={open} />}
        {tab === "institutional" && <Institutional onOpen={open} />}
        {tab === "derivatives" && <Derivatives onOpen={open} />}
        {tab === "research" && <Research />}
        <p className="disclaimer">Research tool, not investment advice. Signals are tested on history before use; no trades are placed.</p>
      </main>
    </div>
  );
}

export default App;
