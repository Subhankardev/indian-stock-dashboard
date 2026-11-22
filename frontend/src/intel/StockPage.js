import React, { useEffect, useState } from "react";
import { getJSON, fmt, pct, cls } from "./api";
import { BarChart, DemoBanner, ErrorBox, Legend, LineChart } from "./Common";
import OptionChainPanel from "../components/OptionChainPanel";
import EvidencePanel from "./EvidencePanel";
import FuturesPanel from "./FuturesPanel";
import VwapPanel from "./VwapPanel";
import FundamentalsPanel from "./FundamentalsPanel";
import ScorePanel from "./ScorePanel";

const RANGES = [["3M", 63], ["6M", 126], ["1Y", 250], ["2Y", 500]];

function Stat({ label, value, tone, hint }) {
  return <div className="card" title={hint}><div className="muted small-text">{label}</div><div className={`big ${tone || ""}`}>{value}</div></div>;
}

export default function StockPage({ symbol, onOpen }) {
  const [days, setDays] = useState(250);
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    setD(null); setErr(null);
    getJSON(`/api/intel/stock/${encodeURIComponent(symbol)}?days=${days}`).then(setD).catch(setErr);
  }, [symbol, days]);
  if (err) return <ErrorBox error={err.message} />;
  if (!d) return <div className="panel muted">Loading {symbol}…</div>;
  const s = d.series, L = d.latest;
  const dates = s.map((r) => r.date);
  const col = (k) => s.map((r) => r[k]);
  const trend = L.close > L.dma200 ? (L.close > L.dma50 ? "Above 50 & 200 DMA" : "Above 200 DMA") : "Below 200 DMA";
  return (
    <>
      <DemoBanner demo={d.demo} />
      <div className="panel">
        <div className="panel-head">
          <h3>{d.symbol} <span className="muted small-text">{L.industry}</span></h3>
          <div className="controls">{RANGES.map(([l, n]) => (
            <button key={l} className={`chip-btn ${days === n ? "active" : ""}`} onClick={() => setDays(n)}>{l}</button>))}</div>
        </div>
        <div className="cards">
          <Stat label="Close" value={fmt(L.close)} />
          <Stat label="1 day" value={pct(L.ret_1d)} tone={cls(L.ret_1d)} />
          <Stat label="RS rank (0–100)" value={fmt(L.rs_rank, 0)} tone={L.rs_rank >= 80 ? "up" : L.rs_rank <= 20 ? "down" : ""}
            hint="Percentile of 6-month return vs Nifty 50 among liquid stocks on this date" />
          <Stat label="RS 6M vs Nifty 50" value={pct(L.rs_6m)} tone={cls(L.rs_6m)} />
          <Stat label="RVOL (20D)" value={`${fmt(L.rvol20)}×`} />
          <Stat label="Delivery % (20D avg)" value={`${fmt(L.deliv_pct, 1)} (${fmt(L.deliv_pct_avg20, 1)})`} />
          <Stat label="RSI 14 · ADX 14" value={`${fmt(L.rsi14, 0)} · ${fmt(L.adx, 0)}`} />
          <Stat label="Trend" value={trend} tone={L.close > L.dma200 ? "up" : "down"} />
          <Stat label="Avg traded value" value={`₹${fmt(L.avg_value_cr, 1)} cr`} tone={L.liquid ? "" : "down"}
            hint={L.liquid ? "Passes liquidity filters" : "Fails at least one liquidity filter"} />
        </div>
      </div>
      <ScorePanel symbol={symbol} />
      <div className="panel">
        <div className="panel-head"><h3>Price & moving averages</h3>
          <Legend items={[{ label: "Close", color: "var(--text)" }, { label: "EMA 20", color: "#8b5cf6" },
            { label: "50 DMA", color: "#0ea5e9" }, { label: "200 DMA", color: "#f59e0b" }]} /></div>
        <LineChart dates={dates} yFormat={(v) => v.toFixed(v < 100 ? 1 : 0)} series={[
          { key: "c", color: "var(--text)", values: col("close") },
          { key: "e", color: "#8b5cf6", values: col("ema20"), width: 1 },
          { key: "50", color: "#0ea5e9", values: col("dma50"), width: 1 },
          { key: "200", color: "#f59e0b", values: col("dma200"), width: 1 }]} />
        <div className="muted small-text">Volume</div>
        <BarChart dates={dates} values={col("volume")} />
      </div>
      <div className="panel">
        <div className="panel-head"><h3>Relative strength vs Nifty 50</h3>
          <Legend items={[{ label: "RS line (rebased 100)", color: "var(--primary)" }]} /></div>
        <LineChart dates={dates} height={170} yFormat={(v) => v.toFixed(0)}
          series={[{ key: "rs", color: "var(--primary)", values: col("rs_ratio") }]} />
        <div className="muted small-text">A rising line means the stock is outperforming the index. This is relative strength, not RSI.</div>
      </div>
      <div className="panel">
        <div className="panel-head"><h3>Delivery % (daily)</h3></div>
        <BarChart dates={dates} values={col("deliv_pct")} ref100={L.deliv_pct_avg20 || undefined} color="#94a3b8" height={110} />
        <div className="muted small-text">Share of traded quantity marked for delivery (NSE). The line is the 20-day average.
          High delivery is a clue, not proof, of institutional buying (evidence class D).</div>
      </div>
      <VwapPanel symbol={symbol} days={days} />
      <EvidencePanel symbol={symbol} />
      <FuturesPanel symbol={symbol} />
      <FundamentalsPanel symbol={symbol} onOpen={onOpen} />
      {d.source && (
        <div className="provenance">Latest price row from <b>{d.source.dataset}</b> file for {d.source.as_of},
          fetched {d.source.fetched_at} · {d.source.url}</div>
      )}
      {!d.demo && <OptionChainPanel key={`oc-${symbol}`} symbol={symbol} />}
    </>
  );
}
