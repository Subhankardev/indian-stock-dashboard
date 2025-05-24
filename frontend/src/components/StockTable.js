import React from "react";

function StockTable({ stocks, onSelect }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Symbol</th>
          <th>Signal</th>
          <th>Reason</th>
          <th>Latest Close</th>
        </tr>
      </thead>
      <tbody>
        {stocks.map((s) => (
          <tr key={s.symbol} onClick={() => onSelect(s.symbol)}>
            <td>{s.symbol}</td>
            <td>{s.signal}</td>
            <td>{s.reason}</td>
            <td>{s.latest_close}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default StockTable;