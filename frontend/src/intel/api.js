export const API = process.env.REACT_APP_API || "http://localhost:8000";

export async function getJSON(path, opts) {
  const r = await fetch(`${API}${path}`, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `${r.status} ${r.statusText}`);
  return body;
}

export const fmt = (v, d = 2) => (v === null || v === undefined ? "–" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }));
export const pct = (v, d = 1) => (v === null || v === undefined ? "–" : `${v > 0 ? "+" : ""}${Number(v).toFixed(d)}%`);
export const cls = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
