import React, { useState, useEffect } from "react";
import StockTable from "./components/StockTable";
import StockDetail from "./components/StockDetail";
import WaveOIPanel from "./components/WaveOIPanel";
import OptionChainPanel from "./components/OptionChainPanel";
import './App.css';

function App() {
  const [trending, setTrending] = useState([]);
  const [selectedStock, setSelectedStock] = useState(null);
  const [query, setQuery] = useState("");
  const [demo, setDemo] = useState(false);

  useEffect(() => {
    fetch("http://localhost:8000/api/trending")
      .then(res => res.json())
      .then(data => setTrending(Array.isArray(data) ? data : []))
      .catch(() => setTrending([]));
  }, []);

  const onSearch = (e) => {
    e.preventDefault();
    if (query.trim()) setSelectedStock(query.trim().toUpperCase());
  };

  return (
    <div className="App">
      <h1>Indian Stock Market Analytics Dashboard</h1>
      <form className="search" onSubmit={onSearch}>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="NSE symbol, e.g. RELIANCE" />
        <button type="submit">Analyze</button>
        <label><input type="checkbox" checked={demo} onChange={(e) => setDemo(e.target.checked)} /> demo data</label>
      </form>
      <StockTable stocks={trending} onSelect={setSelectedStock} />
      {selectedStock && (
        <>
          {!demo && <StockDetail symbol={selectedStock} />}
          <WaveOIPanel key={`${selectedStock}-${demo}`} symbol={selectedStock} demo={demo} />
          <OptionChainPanel key={`oc-${selectedStock}-${demo}`} symbol={selectedStock} demo={demo} />
        </>
      )}
    </div>
  );
}

export default App;