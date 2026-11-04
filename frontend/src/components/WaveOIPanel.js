import React, { useEffect, useMemo, useState } from "react";

const API = "http://localhost:8000";
const fmt = (v, d = 2) => (v === null || v === undefined ? "–" : Number(v).toFixed(d));
const dirText = (d) => (d === 1 ? "▲ Up" : d === -1 ? "▼ Down" : "–");
const dirClass = (d) => (d === 1 ? "up" : d === -1 ? "down" : "");

const WAVE_TEXT = {
  W3: "Riding wave 3 (wave 2 pullback complete)",
  W5: "Riding wave 5 (wave 4 pullback complete)",
  ABC: "5-wave impulse complete – expecting A-B-C correction",
};

const OI_TEXT = {
  new_longs: "Price ↑ OI ↑ – fresh longs",
  short_covering: "Price ↑ OI ↓ – short covering (weaker)",
  new_shorts: "Price ↓ OI ↑ – fresh shorts",
  long_liquidation: "Price ↓ OI ↓ – long liquidation (weaker)",
  neutral: "OI flat",
  "n/a": "No OI data",
};

// Overlapping impulses share pivots; keep one label per pivot (latest count wins).
function waveLabels(impulses) {
  const labels = {};
  impulses.forEach((imp) =>
    imp.dates.forEach((d, j) => {
      if (j > 0) labels[d] = { n: j, price: imp.prices[j], top: imp.prices[j] > imp.prices[j - 1] };
    })
  );
  return labels;
}

function Chart({ data }) {
  const W = 900, H = 320, OI_H = 80, PAD = 40;
  const { chart, pivots, latest } = data;
  const n = chart.dates.length;

  const geom = useMemo(() => {
    const levels = [latest.wave.target, latest.wave.stop].filter((v) => v !== null);
    const vals = [...chart.close, ...chart.gti_line.filter((v) => v !== null), ...levels];
    const lo = Math.min(...vals), hi = Math.max(...vals);
    const x = (i) => PAD + (i / Math.max(1, n - 1)) * (W - 2 * PAD);
    const y = (v) => 10 + (1 - (v - lo) / (hi - lo || 1)) * (H - 30);
    const pos = Object.fromEntries(chart.dates.map((d, i) => [d, i]));
    return { lo, hi, x, y, pos };
  }, [chart, latest, n]);

  const { x, y, pos, lo, hi } = geom;
  const path = (arr) =>
    arr.map((v, i) => (v === null ? "" : `${i === 0 || arr[i - 1] === null ? "M" : "L"}${x(i)},${y(v)}`)).join(" ");

  // GTI line split into coloured runs by direction
  const gtiRuns = [];
  chart.gti_line.forEach((v, i) => {
    const d = chart.gti_dir[i];
    const last = gtiRuns[gtiRuns.length - 1];
    if (!last || last.d !== d) gtiRuns.push({ d, pts: i > 0 ? [[i - 1, chart.gti_line[i - 1]]] : [] });
    gtiRuns[gtiRuns.length - 1].pts.push([i, v]);
  });

  const oi = chart.open_interest || [];
  const oiVals = oi.filter((v) => v !== null);
  const oiLo = Math.min(...oiVals), oiHi = Math.max(...oiVals);
  const oiY = (v) => H + 10 + (1 - (v - oiLo) / (oiHi - oiLo || 1)) * (OI_H - 20);

  return (
    <svg viewBox={`0 0 ${W} ${H + (oiVals.length ? OI_H : 0)}`} className="wave-chart">
      <text x={4} y={14} className="axis">{fmt(hi)}</text>
      <text x={4} y={H - 20} className="axis">{fmt(lo)}</text>
      <path d={path(chart.close)} className="price" />
      {gtiRuns.map((r, k) => (
        <polyline key={k} className={`gti ${dirClass(r.d)}`}
          points={r.pts.filter(([, v]) => v !== null).map(([i, v]) => `${x(i)},${y(v)}`).join(" ")} />
      ))}
      {latest.wave.target !== null && (
        <g><line x1={PAD} x2={W - PAD} y1={y(latest.wave.target)} y2={y(latest.wave.target)} className="level target" />
          <text x={W - PAD} y={y(latest.wave.target) - 3} className="axis" textAnchor="end">target {fmt(latest.wave.target)}</text></g>
      )}
      {latest.wave.stop !== null && (
        <g><line x1={PAD} x2={W - PAD} y1={y(latest.wave.stop)} y2={y(latest.wave.stop)} className="level stop" />
          <text x={W - PAD} y={y(latest.wave.stop) + 11} className="axis" textAnchor="end">stop {fmt(latest.wave.stop)}</text></g>
      )}
      {pivots.filter((p) => p.date in pos).map((p, k) => (
        <circle key={k} cx={x(pos[p.date])} cy={y(p.price)} r={3} className={`pivot ${p.kind}`} />
      ))}
      {Object.entries(waveLabels(data.impulses)).filter(([d]) => d in pos).map(([d, l]) => (
        <text key={d} x={x(pos[d])} y={y(l.price) + (l.top ? -6 : 14)} className="wave-num" textAnchor="middle">{l.n}</text>
      ))}
      {chart.signals.filter((s) => s.date in pos).map((s, k) => {
        const cx = x(pos[s.date]), cy = y(s.price);
        return s.action === "BUY"
          ? <polygon key={k} points={`${cx},${cy + 6} ${cx - 6},${cy + 16} ${cx + 6},${cy + 16}`} className="sig buy" />
          : <polygon key={k} points={`${cx},${cy - 6} ${cx - 6},${cy - 16} ${cx + 6},${cy - 16}`} className="sig sell" />;
      })}
      {oiVals.length > 0 && (
        <g>
          <text x={4} y={H + 14} className="axis">OI</text>
          <path d={oi.map((v, i) => (v === null ? "" : `${i === 0 || oi[i - 1] === null ? "M" : "L"}${x(i)},${oiY(v)}`)).join(" ")} className="oi" />
        </g>
      )}
    </svg>
  );
}

function WaveOIPanel({ symbol, demo = false }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [useDemo, setUseDemo] = useState(demo);

  useEffect(() => {
    setData(null);
    setError(null);
    const qs = useDemo ? "?source=synthetic" : "";
    fetch(`${API}/api/wave-oi/${symbol}${qs}`)
      .then(async (res) => {
        const body = await res.json();
        if (!res.ok) throw new Error(body.detail || res.statusText);
        return body;
      })
      .then(setData)
      .catch((e) => setError(e.message));
  }, [symbol, useDemo]);

  if (error) {
    return (
      <div className="panel">
        <h3>Wave · GTI · OI</h3>
        <p className="error">{error}</p>
        {!useDemo && <button onClick={() => setUseDemo(true)}>Show with demo data</button>}
      </div>
    );
  }
  if (!data) return <div className="panel">Loading wave / OI analysis for {symbol}…</div>;

  const { latest, last_signal: ls, backtest } = data;
  const m = backtest.metrics;
  return (
    <div className="panel">
      <h3>
        Wave · GTI · OI — {data.symbol} <small>as of {data.as_of}{data.source === "synthetic" ? " (demo data)" : ""}</small>
      </h3>
      {data.warning && <p className="warn">{data.warning}</p>}

      <div className="cards">
        <div className="card">
          <h4>Wave structure</h4>
          <div className={`big ${dirClass(latest.wave.direction)}`}>{latest.wave.label || "No active setup"}</div>
          <p>{WAVE_TEXT[latest.wave.label] || "Waiting for a valid wave-2/wave-4 pullback"}</p>
          {latest.wave.label && (
            <p>Target {fmt(latest.wave.target)} · Stop {fmt(latest.wave.stop)} · Invalid {fmt(latest.wave.invalidation)}
              <br />Fib quality {fmt(latest.wave.score)}</p>
          )}
        </div>
        <div className="card">
          <h4>Ghost Trade Indicator</h4>
          <div className={`big ${dirClass(latest.gti.direction)}`}>{dirText(latest.gti.direction)}</div>
          <p>Trail line {fmt(latest.gti.line)} (close {fmt(latest.close)})</p>
          <p>Ghost build-up: {latest.oi.ghost_today ? "today" : latest.oi.ghost_bias_recent !== 0
            ? (latest.oi.ghost_bias_recent > 0 ? "recent, bullish" : "recent, bearish") : "none recent"}</p>
        </div>
        <div className="card">
          <h4>Open interest</h4>
          <div className="big">{latest.oi.regime}</div>
          <p>{OI_TEXT[latest.oi.regime]}</p>
          <p>OI change {fmt(latest.oi.change_pct)}%{latest.oi.divergence !== 0 &&
            ` · ${latest.oi.divergence > 0 ? "bullish" : "bearish"} OI divergence`}</p>
        </div>
        <div className="card">
          <h4>Signal</h4>
          <div className={`big ${latest.signal_today === "BUY" ? "up" : latest.signal_today === "SELL" ? "down" : ""}`}>
            {latest.signal_today === "NONE" ? "No new signal" : latest.signal_today}
          </div>
          {ls && (
            <p>Last: <b>{ls.action}</b> {ls.date} ({ls.bars_ago} bars ago) @ {fmt(ls.price)}
              <br />Stop {fmt(ls.stop)} · Target {fmt(ls.target)}<br /><small>{ls.reason}</small></p>
          )}
        </div>
      </div>

      <Chart data={data} />
      <p className="legend">
        <span className="k price" /> close <span className="k gti-up" /> GTI up <span className="k gti-down" /> GTI down
        · ● pivots · 1–5 completed impulses · ▲▼ signals · dashed = current target / stop
      </p>

      <h4>Backtest (same rules, next-bar entry, {data.bars} bars)</h4>
      <table className="metrics">
        <tbody>
          <tr>
            <td>Trades <b>{m.trades}</b></td>
            <td>Win rate <b>{m.win_rate === null ? "–" : fmt(m.win_rate * 100, 0) + "%"}</b></td>
            <td>Return <b>{fmt(m.total_return_pct)}%</b></td>
            <td>Buy &amp; hold <b>{fmt(m.buy_hold_return_pct)}%</b></td>
            <td>Profit factor <b>{fmt(m.profit_factor)}</b></td>
            <td>Avg R <b>{fmt(m.avg_r)}</b></td>
            <td>Max DD <b>{fmt(m.max_drawdown_pct)}%</b></td>
          </tr>
        </tbody>
      </table>
      {backtest.trades.length > 0 && (
        <table>
          <thead>
            <tr><th>Side</th><th>Entry</th><th>Exit</th><th>Entry ₹</th><th>Exit ₹</th><th>R</th><th>Exit reason</th></tr>
          </thead>
          <tbody>
            {backtest.trades.slice().reverse().map((t, i) => (
              <tr key={i}>
                <td className={t.side === "LONG" ? "up" : "down"}>{t.side}</td>
                <td>{t.entry_date.slice(0, 10)}</td><td>{t.exit_date.slice(0, 10)}</td>
                <td>{fmt(t.entry)}</td><td>{fmt(t.exit)}</td>
                <td className={t.r_multiple >= 0 ? "up" : "down"}>{fmt(t.r_multiple)}</td>
                <td>{t.exit_reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="disclaimer">Educational tool, not investment advice. Wave counts are probabilistic; check data against your broker before trading.</p>
    </div>
  );
}

export default WaveOIPanel;
