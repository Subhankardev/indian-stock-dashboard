import React from "react";

function StockTable({ stocks, onSelect }) {
  return (
    <div className="panel">
      <div className="panel-head"><h3>Trending (classic technicals)</h3></div>
      <table className="data">
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Signal</th>
            <th>Reason</th>
            <th className="num">Latest close</th>
          </tr>
        </thead>
        <tbody>
          {stocks.map((s) => (
            <tr key={s.symbol} className="clickable" onClick={() => onSelect(s.symbol)}>
              <td><b>{s.symbol}</b></td>
              <td><span className={`signal-pill ${s.signal === "BUY" ? "up" : s.signal === "SELL" ? "down" : ""}`}>{s.signal}</span></td>
              <td className="muted">{s.reason}</td>
              <td className="num">{Number(s.latest_close).toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default StockTable;
