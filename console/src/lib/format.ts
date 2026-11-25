export const fmtMs = (ms?: number | null) =>
  ms == null ? "—" : ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(2)}s`;

export const fmtUsd = (v?: number | null, digits = 4) =>
  v == null ? "—" : v === 0 ? "$0" : v < 0.0001 ? `$${v.toExponential(1)}` : `$${v.toFixed(digits)}`;

export const fmtNum = (v?: number | null) =>
  v == null ? "—" : v >= 1_000_000 ? `${(v / 1_000_000).toFixed(2)}M` : v >= 1000 ? `${(v / 1000).toFixed(2)}K` : String(Math.round(v));

export const fmtTime = (iso?: string | null) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${d.toLocaleTimeString([], { hour12: false })}`;
};

export const fmtAgo = (iso?: string | null) => {
  if (!iso) return "—";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};

export const shortId = (id?: string | null, n = 8) => (id ? id.slice(0, n) : "—");

export function tryParseJson(s?: string | null): unknown {
  if (s == null) return null;
  const t = s.trim();
  if (!(t.startsWith("{") || t.startsWith("["))) return s;
  try { return JSON.parse(t); } catch { return s; }
}

export const RANGES = [
  { label: "Past 1 hour", short: "1h", hours: 1 },
  { label: "Past 1 day", short: "1d", hours: 24 },
  { label: "Past 7 days", short: "7d", hours: 24 * 7 },
  { label: "Past 30 days", short: "30d", hours: 24 * 30 },
];

export const rangeParams = (hours: number) => {
  const to = new Date(Date.now() + 60_000);
  const from = new Date(to.getTime() - hours * 3600_000);
  return `from_ts=${encodeURIComponent(from.toISOString())}&to_ts=${encodeURIComponent(to.toISOString())}`;
};
