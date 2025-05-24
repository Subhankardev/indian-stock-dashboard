import React, { useState, useEffect } from "react";
import StockTable from "./components/StockTable";
import StockDetail from "./components/StockDetail";
import './App.css';

function App() {
  const [trending, setTrending] = useState([]);
  const [selectedStock, setSelectedStock] = useState(null);

  useEffect(() => {
    fetch("http://localhost:8000/api/trending")
      .then(res => res.json())
      .then(data => setTrending(data));
  }, []);

  return (
    <div className="App">
      <h1>Indian Stock Market Analytics Dashboard</h1>
      <StockTable stocks={trending} onSelect={setSelectedStock} />
      {selectedStock && <StockDetail symbol={selectedStock} />}
    </div>
  );
}

export default App;