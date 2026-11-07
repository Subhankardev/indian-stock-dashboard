import React, { useEffect, useState } from "react";

const API = "http://localhost:8000";
const fmt = (v, d = 2) => (v === null || v === undefined ? "–"
  : Number(v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d }));
const day = (iso) => new Date(iso).toLocaleDateString([], { day: "numeric", month: "short" });

/** Which trade the panel should translate into an option: today's signal, else the active wave setup. */
export function optionContext(data) {
  const { latest, last_signal: ls } = data;
  if (latest.signal_today !== "NONE" && ls && ls.bars_ago === 0 && ls.stop && ls.target) {
    return { side: latest.signal_today, target: ls.target, stop: ls.stop, why: `today's ${latest.signal_today} signal` };
  }
  const w = latest.wave;
  if (["W3", "W5"].includes(w.label) && w.target && w.stop && w.direction) {
    const up = w.direction === 1;
    const price = latest.close;
    if ((up && price >= w.target) || (!up && price <= w.target)) {
      return { note: `Price ${price.toFixed(2)} has already reached the ${w.label} target ${w.target.toFixed(2)} - no new entry. Wait for a fresh setup.` };
    }
    return { side: up ? "BUY" : "SELL", target: w.target, stop: w.stop, why: `the active ${w.label} setup` };
  }
  return null;
}

function OptionIdea({ symbol, demo, spot, ctx }) {
  const [idea, setIdea] = useState(null);
  const [error, setError] = useState(null);
  const ctxKey = ctx ? `${ctx.side}|${ctx.target}|${ctx.stop}` : "";

  useEffect(() => {
    if (!ctx || ctx.note) return;
    setIdea(null);
    setError(null);
    const qs = new URLSearchParams({ side: ctx.side, target: ctx.target, stop: ctx.stop });
    if (demo) { qs.set("source", "synthetic"); qs.set("spot", spot); }
    fetch(`${API}/api/options/${symbol}/suggest?${qs}`)
      .then(async (r) => {
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || r.statusText);
        return d;
      })
      .then(setIdea)
      .catch((e) => setError(e.message));
  }, [symbol, demo, spot, ctxKey]);

  if (!ctx) return null;
  if (ctx.note) {
    return (
      <div className="option-idea muted-idea">
        <h4 className="opt-head">Option idea <span className="muted">for an option buyer</span></h4>
        <p className="small-text">{ctx.note}</p>
      </div>
    );
  }
  const head = (
    <h4 className="opt-head">
      Option idea <span className="muted">for an option buyer, from {ctx.why}</span>
    </h4>
  );
  if (error) return <div className="option-idea muted-idea">{head}<p className="small-text">No option idea: {error}</p></div>;
  if (!idea) return <div className="option-idea">{head}<p className="muted small-text">Loading option chain…</p></div>;

  const [main, ...alts] = idea.ideas;
  const call = main.type === "CE";
  return (
    <div className="option-idea">
      {head}
      <div className="opt-main">
        <div className="opt-pick">
          <span className={`badge ${call ? "buy" : "sell"}`}>BUY {call ? "CALL" : "PUT"}</span>
          <div className="opt-contract">{symbol} {Number(main.strike)} {main.type}</div>
          <div className="muted small-text">expiry {day(main.expiry)} · {idea.trading_days_to_expiry} trading days left</div>
        </div>
        <div className="opt-levels">
          <div><span>Entry (ask)</span><b>₹{fmt(main.premium)}</b></div>
          <div><span>Target</span><b className="up">₹{fmt(main.target_premium)}</b></div>
          <div><span>Stop</span><b className="down">₹{fmt(main.stop_premium)}</b></div>
          <div><span>Reward : risk</span><b>{main.reward_risk ?? "–"}</b></div>
        </div>
        <div className="opt-levels">
          <div><span>Delta</span><b>{main.delta}</b></div>
          <div><span>IV</span><b>{main.iv_pct}%{idea.realized_vol_pct ? <small className="muted"> vs {idea.realized_vol_pct}% realised</small> : null}</b></div>
          {main.lot_size && <div><span>Lot · cost</span><b>{main.lot_size} · ₹{fmt(main.cost_per_lot, 0)}</b></div>}
          {main.lot_size && <div><span>Max loss / lot</span><b className="down">₹{fmt(main.max_loss_per_lot, 0)}</b></div>}
        </div>
      </div>
      <div className="muted small-text opt-under">
        Underlying target {fmt(idea.underlying_target)} · stop {fmt(idea.underlying_stop)} (spot {fmt(idea.spot)})
      </div>
      {idea.warnings.length > 0 && (
        <ul className="opt-warnings">{idea.warnings.map((w, i) => <li key={i}>⚠ {w}</li>)}</ul>
      )}
      {alts.length > 0 && (
        <table className="opt-alts">
          <thead><tr><th>Alternative</th><th className="num">Strike</th><th className="num">Premium</th><th className="num">Delta</th><th className="num">Target</th><th className="num">Stop</th><th className="num">R:R</th></tr></thead>
          <tbody>
            {alts.map((a) => (
              <tr key={a.strike}>
                <td className="muted">{a.role}</td>
                <td className="num">{Number(a.strike)} {a.type}</td>
                <td className="num">₹{fmt(a.premium)}</td>
                <td className="num">{a.delta}</td>
                <td className="num up">₹{fmt(a.target_premium)}</td>
                <td className="num down">₹{fmt(a.stop_premium)}</td>
                <td className="num">{a.reward_risk ?? "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="disclaimer">{idea.assumptions} Educational tool, not investment advice.</p>
    </div>
  );
}

export default OptionIdea;
