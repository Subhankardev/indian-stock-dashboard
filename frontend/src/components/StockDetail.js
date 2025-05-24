import React, { useState, useEffect } from "react";

function StockDetail({ symbol }) {
  const [stock, setStock] = useState(null);
  const [options, setOptions] = useState(null);

  useEffect(() => {
    fetch(`http://localhost:8000/api/stock/${symbol}`)
      .then(res => res.json())
      .then(setStock);
    fetch(`http://localhost:8000/api/options/${symbol}`)
      .then(res => res.json())
      .then(setOptions);
  }, [symbol]);

  if (!stock) return <div>Loading {symbol}...</div>;

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
      {options && (
        <>
          <h3>Option Analytics</h3>
          <p>PCR: {options.PCR && options.PCR.toFixed(2)}</p>
          <p>Max CE OI (Resistance): {options.max_CE_OI_strike}</p>
          <p>Max PE OI (Support): {options.max_PE_OI_strike}</p>
        </>
      )}
    </div>
  );
}

export default StockDetail;