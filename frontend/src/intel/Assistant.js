import React, { useEffect, useRef, useState } from "react";
import { getJSON } from "./api";

const EXAMPLES = [
  "What is the market regime today, and which sectors are strongest?",
  "Which liquid stocks show the most accumulation evidence, and what class is it?",
  "Give me the full picture on RELIANCE: trend, RS, delivery, deals, futures and fundamentals.",
  "Which signals have passed their tests, and how did the best backtest do against Nifty 500?",
];

function Markdownish({ text }) {
  // minimal rendering: paragraphs, bullet lines, **bold**, `code`; no HTML injection
  const inline = (s, k) => s.split(/(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*)/g).map((part, i) =>
    part.startsWith("**") ? <b key={`${k}-${i}`}>{part.slice(2, -2)}</b>
      : part.startsWith("`") ? <code key={`${k}-${i}`}>{part.slice(1, -1)}</code>
      : /^\*[^*]/.test(part) && part.endsWith("*") && part.length > 2 ? <i key={`${k}-${i}`}>{part.slice(1, -1)}</i> : part);
  return <div className="md">{text.split("\n").map((line, i) => {
    if (/^\s*[-*] /.test(line)) return <div key={i} className="md-li">• {inline(line.replace(/^\s*[-*] /, ""), i)}</div>;
    if (/^#{1,4} /.test(line)) return <div key={i} className="md-h">{inline(line.replace(/^#{1,4} /, ""), i)}</div>;
    if (/^\|/.test(line)) return <div key={i} className="md-table">{line}</div>;
    return line.trim() ? <p key={i}>{inline(line, i)}</p> : <div key={i} className="md-gap" />;
  })}</div>;
}

export default function Assistant() {
  const [status, setStatus] = useState(null);
  const [turns, setTurns] = useState([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const end = useRef(null);
  useEffect(() => { getJSON("/api/intel/assistant/status").then(setStatus).catch(() => setStatus(false)); }, []);
  useEffect(() => { end.current && end.current.scrollIntoView({ behavior: "smooth", block: "end" }); }, [turns, busy]);
  const send = (question) => {
    const text = (question || q).trim();
    if (!text || busy) return;
    const history = turns.flatMap((t) => [{ role: "user", content: t.q }, { role: "assistant", content: t.a.answer }]);
    setBusy(true); setErr(null); setQ("");
    getJSON("/api/intel/assistant", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: text, history }) })
      .then((a) => setTurns((t) => [...t, { q: text, a }])).catch((e) => { setErr(e.message); setQ(text); }).finally(() => setBusy(false));
  };
  return (
    <div className="panel assistant">
      <div className="panel-head"><h3>Research assistant</h3>
        {status && <span className={`pill ${status.configured ? "ok" : "off"}`}>{status.configured ? status.model : "not configured"}</span>}
        {turns.length > 0 && <button className="link-btn" onClick={() => setTurns([])}>New conversation</button>}</div>
      <div className="muted small-text">Answers come only from this system's data through read-only tools ({status ? status.tools.length : "…"} tools:
        screener, stock reports, rankings, deals, derivatives, research results, backtests, paper trades). Each answer lists the tools it used.
        It cannot browse the web or place orders. Research, not investment advice.</div>
      {status && !status.configured && <div className="warn small-text">Add <code>ANTHROPIC_API_KEY=…</code> to backend/.env (key from console.anthropic.com) and restart the backend.</div>}
      <div className="chat">
        {turns.length === 0 && (
          <div className="examples">{EXAMPLES.map((e) => <button key={e} className="chip-btn" onClick={() => send(e)} disabled={busy || (status && !status.configured)}>{e}</button>)}</div>)}
        {turns.map((t, i) => (
          <div key={i} className="turn">
            <div className="bubble user">{t.q}</div>
            <div className="bubble bot">
              <Markdownish text={t.a.answer} />
              <div className="trace small-text muted">
                {t.a.tool_calls.length ? `Tools: ${t.a.tool_calls.map((c) => `${c.name}${c.input && c.input.symbol ? `(${c.input.symbol})` : ""}${c.ok ? "" : " ✗"}`).join(", ")}` : "No tools used"}
                {" · "}{t.a.usage.input_tokens + t.a.usage.cache_read_input_tokens} in / {t.a.usage.output_tokens} out tokens
                {t.a.usage.cache_read_input_tokens ? ` (${t.a.usage.cache_read_input_tokens} cached)` : ""}
              </div>
            </div>
          </div>))}
        {busy && <div className="bubble bot muted">Looking it up in the database…</div>}
        <div ref={end} />
      </div>
      {err && <div className="error small-text">{err}</div>}
      <form className="ask" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <textarea value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask about a stock, a signal, a backtest, the market…" rows={2}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} disabled={status === false} />
        <button className="primary" type="submit" disabled={busy || !q.trim()}>Ask</button>
      </form>
    </div>
  );
}
