import React, { useState, useEffect } from "react";

const LABELS = {
  SMA_5: "SMA 5", SMA_20: "SMA 20", RSI_14: "RSI 14", MACD: "MACD", MACD_signal: "MACD signal",
  BB_UPPER: "Bollinger upper", BB_MID: "Bollinger mid", BB_LOWER: "Bollinger lower",
};

function StockDetail({ symbol }) {
  const [stock, setStock] = useState(null);

  useEffect(() => {
    setStock(null);
    fetch(`http://localhost:8000/api/stock/${symbol}`)
      .then(res => res.json())
      .then(setStock)
      .catch(() => setStock({}));
  }, [symbol]);

  if (!stock || !stock.indicators) return null; // loading, or an error the panels below explain

  const cls = stock.signal === "BUY" ? "up" : stock.signal === "SELL" ? "down" : "";
  return (
    <div className="panel technicals">
      <div className="panel-head">
        <h3>Classic technicals</h3>
        <span className={`signal-pill ${cls}`}>{stock.signal}</span>
        <span className="muted small-text">{stock.reason}</span>
      </div>
      <div className="indicator-grid">
        {Object.entries(stock.indicators).map(([k, v]) => (
          <div key={k} className="indicator">
            <span>{LABELS[k] || k}</span>
            <b>{Number(v).toFixed(2)}</b>
          </div>
        ))}
      </div>
    </div>
  );
}

export default StockDetail;
