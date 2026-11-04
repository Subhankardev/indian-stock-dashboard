import React, { useState, useEffect } from "react";

function StockDetail({ symbol }) {
  const [stock, setStock] = useState(null);

  useEffect(() => {
    fetch(`http://localhost:8000/api/stock/${symbol}`)
      .then(res => res.json())
      .then(setStock);
  }, [symbol]);

  if (!stock) return <div>Loading {symbol}...</div>;
  if (!stock.indicators) return null; // backend error; the panels below show their own messages

  return (
    <div>
      <h2>{symbol} — Signal: {stock.signal}</h2>
      <p>{stock.reason}</p>
      <h3>Indicators</h3>
      <ul>
        {Object.entries(stock.indicators).map(([k, v]) => (
          <li key={k}>{k}: {v.toFixed(2)}</li>
        ))}
      </ul>
    </div>
  );
}

export default StockDetail;
