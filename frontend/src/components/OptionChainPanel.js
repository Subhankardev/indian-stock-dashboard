import React, { useEffect, useState } from "react";

const API = "http://localhost:8000";
const REFRESH_MS = 30_000;
const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "–" : Number(v).toFixed(d));
const big = (v) => (v === null || v === undefined ? "–" : Math.round(v).toLocaleString("en-IN"));
const signed = (v) => (!v ? "–" : `${v > 0 ? "+" : ""}${big(v)}`);

const BUILDUP_CLASS = {
  "Long build-up": "up",
  "Short covering": "up",
  "Short build-up": "down",
  "Long unwinding": "down",
};

function pcrReading(pcr) {
  if (pcr === null || pcr === undefined) return "";
  if (pcr >= 1.3) return "heavy put writing: strong support, bullish lean (watch for overcrowding)";
  if (pcr >= 1.0) return "more puts than calls written: mildly bullish";
  if (pcr >= 0.7) return "balanced to mildly bearish";
  return "heavy call writing: overhead supply, bearish lean";
}

function OiCell({ side, max, align }) {
  if (!side) return <td colSpan={4} />;
  const pct = max ? Math.min(100, (side.oi / max) * 100) : 0;
  const bar = (
    <div className={`oi-bar ${align}`}>
      <span style={{ width: `${pct}%` }} />
      <b>{big(side.oi)}</b>
    </div>
  );
  const chg = <td className={side.oi_change > 0 ? "up" : side.oi_change < 0 ? "down" : ""}>{signed(side.oi_change)}</td>;
  const tag = side.buildup ? <small className={`tag ${BUILDUP_CLASS[side.buildup] || ""}`}>{side.buildup}</small> : null;
  const cells = [
    chg,
    <td key="oi">{bar}{tag}</td>,
    <td key="iv">{fmt(side.iv, 1)}</td>,
    <td key="ltp"><b>{fmt(side.ltp)}</b></td>,
  ];
  return align === "left" ? <>{cells}</> : <>{cells.slice().reverse()}</>;
}

function OptionChainPanel({ symbol, demo = false }) {
  const [expiry, setExpiry] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [useDemo, setUseDemo] = useState(demo);
  const [updated, setUpdated] = useState(null);
  const [tick, setTick] = useState(0); // bumps to force a refresh
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    setExpiry("");
    setData(null);
  }, [symbol, useDemo]);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      const qs = new URLSearchParams();
      if (useDemo) qs.set("source", "synthetic");
      if (expiry && !useDemo) qs.set("expiry", expiry);
      fetch(`${API}/api/options/${symbol}?${qs}`)
        .then(async (res) => {
          const body = await res.json();
          if (!res.ok) throw new Error(body.detail || res.statusText);
          return body;
        })
        .then((body) => {
          if (cancelled) return;
          setData(body);
          setError(null);
          setUpdated(new Date());
        })
        .catch((e) => !cancelled && setError(e.message));
    };
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [symbol, expiry, useDemo, tick]);

  if (error && !data) {
    return (
      <div className="panel">
        <h3>Option chain — {symbol}</h3>
        <p className="error">{error}</p>
        {!useDemo && <button onClick={() => setUseDemo(true)}>Show demo chain</button>}
      </div>
    );
  }
  if (!data) return <div className="panel">Loading option chain for {symbol}…</div>;

  const maxOi = Math.max(1, ...data.rows.flatMap((r) => [r.CE && r.CE.oi, r.PE && r.PE.oi].filter(Boolean)));
  const spot = data.spot;
  const mpDist = data.max_pain && spot ? ((data.max_pain / spot - 1) * 100) : null;

  return (
    <div className="panel">
      <h3>
        Option chain — {data.symbol}{" "}
        <small>
          {data.source === "synthetic" ? "(demo data)" : `via ${data.source}`}
          {updated && ` · snapshot fetched ${updated.toLocaleTimeString()}`}
          {updated && ` · next refresh in ${Math.max(0, Math.ceil((updated.getTime() + REFRESH_MS - now) / 1000))}s`}{" "}
          <button className="small" onClick={() => setTick((n) => n + 1)}>Refresh now</button>
          {error && <span className="error"> · refresh failed: {error}</span>}
        </small>
      </h3>
      {data.expiries.length > 1 && (
        <label className="expiry">
          Expiry{" "}
          <select value={expiry || data.expiry} onChange={(e) => setExpiry(e.target.value)}>
            {data.expiries.map((e) => <option key={e} value={e}>{e}</option>)}
          </select>
        </label>
      )}

      <div className="cards">
        <div className="card">
          <h4>Spot · ATM</h4>
          <div className="big">{fmt(spot)}</div>
          <p>ATM {fmt(data.atm_strike, 0)} · IV {fmt(data.atm_iv, 1)}%</p>
          <p>Straddle {fmt(data.atm_straddle)} → expected move ±{fmt(data.expected_move_pct, 1)}%</p>
        </div>
        <div className="card">
          <h4>Put-call ratio</h4>
          <div className="big">{fmt(data.PCR)}</div>
          <p>{pcrReading(data.PCR)}</p>
          <p>Volume PCR {fmt(data.pcr_volume)}</p>
        </div>
        <div className="card">
          <h4>OI walls</h4>
          <p>Resistance (max call OI) <b className="down">{fmt(data.resistance, 0)}</b></p>
          <p>Support (max put OI) <b className="up">{fmt(data.support, 0)}</b></p>
          <p className="muted">Where option writers are defending</p>
        </div>
        <div className="card">
          <h4>Max pain</h4>
          <div className="big">{fmt(data.max_pain, 0)}</div>
          <p>{mpDist !== null && `${mpDist > 0 ? "+" : ""}${fmt(mpDist, 1)}% from spot`}</p>
          <p className="muted">Expiry price where writers pay out least</p>
        </div>
      </div>

      <div className="chain-wrap">
        <table className="chain">
          <thead>
            <tr><th colSpan={4} className="calls">CALLS</th><th rowSpan={2}>Strike</th><th colSpan={4} className="puts">PUTS</th></tr>
            <tr>
              <th>OI chg</th><th>OI</th><th>IV</th><th>LTP</th>
              <th>LTP</th><th>IV</th><th>OI</th><th>OI chg</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((r) => {
              const marks = [
                r.strike === data.atm_strike && "atm",
                r.strike === data.resistance && "wall-ce",
                r.strike === data.support && "wall-pe",
              ].filter(Boolean).join(" ");
              return (
                <tr key={r.strike} className={marks}>
                  <OiCell side={r.CE} max={maxOi} align="left" />
                  <td className={`strike ${spot && r.strike < spot ? "itm-ce" : ""} ${spot && r.strike > spot ? "itm-pe" : ""}`}>
                    {fmt(r.strike, r.strike % 1 ? 1 : 0)}
                    {r.strike === data.max_pain && <small className="tag">max pain</small>}
                  </td>
                  <OiCell side={r.PE} max={maxOi} align="right" />
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="legend">
        Bars = open interest · OI chg vs previous day · tags show price/OI build-up per contract ·
        shaded strike = in-the-money side · outlined rows = ATM / OI walls · snapshot from Upstox, refreshed every 30 s (not tick-by-tick)
      </p>
    </div>
  );
}

export default OptionChainPanel;
