import { Link } from "react-router-dom";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApp } from "../App";
import { pulse } from "../lib/api";
import { fmtMs, fmtNum, fmtUsd, rangeParams } from "../lib/format";
import { useFetch } from "../lib/hooks";
import { Card, Empty, ErrorNote, PageHeader, Spinner, Stat, Table } from "../components/ui";

const LEVEL_COLORS: Record<string, string> = { DEFAULT: "#14b896", WARNING: "#f59e0b", ERROR: "#ef4444", DEBUG: "#a1a1aa" };
const tick = { fontSize: 11, fill: "#71717a" };

function pivot(rows: any[], key: string, val: string) {
  const out: Record<string, any> = {};
  const series = new Set<string>();
  for (const r of rows) {
    const t = new Date(r.bucket).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    out[t] ??= { t };
    out[t][r[key]] = (out[t][r[key]] ?? 0) + Number(r[val]);
    series.add(r[key]);
  }
  return { data: Object.values(out), series: [...series] };
}

export default function Home() {
  const { rangeHours } = useApp();
  const { data, error, loading } = useFetch(() => pulse.get<any>(`/api/metrics/overview?${rangeParams(rangeHours)}`), [rangeHours], 15000);

  if (loading && !data) return <Spinner />;
  if (error) return <ErrorNote error={`Pulse unavailable: ${error}`} />;
  const t = data.totals;
  const obs = pivot(data.observations_by_time, "level", "n");
  const cost = pivot(data.cost_by_time, "model", "cost");

  return (
    <div className="space-y-4">
      <PageHeader title="Home" subtitle="Everything Atlas did in the selected window, from Pulse traces." />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-6">
        <Stat label="Traces" value={fmtNum(t.traces)} hint={`${fmtNum(t.observations)} observations`} />
        <Stat label="Model cost" value={fmtUsd(t.cost)} />
        <Stat label="Tokens" value={fmtNum(t.tokens)} />
        <Stat label="Latency p50" value={fmtMs(t.p50_ms)} />
        <Stat label="Latency p95" value={fmtMs(t.p95_ms)} />
        <Stat label="Errors" value={fmtNum(t.errors)} hint={t.traces ? `${((100 * t.errors) / t.traces).toFixed(1)}% of traces` : undefined} />
      </div>

      {!t.traces && (
        <Card><Empty title="No traces yet">Upload a document in <Link className="text-brand-600 underline" to="/knowledge">Collections</Link> or ask a question in <Link className="text-brand-600 underline" to="/chat">Chat</Link>. Every step shows up here within a few seconds.</Empty></Card>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Traces by name">
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={data.traces_by_name} layout="vertical" margin={{ left: 8, right: 16 }}>
              <XAxis type="number" tick={tick} allowDecimals={false} />
              <YAxis type="category" dataKey="name" tick={tick} width={90} />
              <Tooltip />
              <Bar dataKey="n" name="traces" fill="#818cf8" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Card>
        <Card title="Model costs" pad={false}>
          <Table head={["Model", "Calls", "Tokens", "USD"]}>
            {data.model_costs.map((m: any) => (
              <tr key={m.model}><td className="px-3 py-2 font-mono text-xs">{m.model}</td><td className="px-3 py-2 tabular-nums">{m.calls}</td>
                <td className="px-3 py-2 tabular-nums">{fmtNum(m.tokens)}</td><td className="px-3 py-2 tabular-nums">{fmtUsd(m.cost)}</td></tr>
            ))}
          </Table>
          {!data.model_costs.length && <Empty title="No model calls" />}
        </Card>
        <Card title="Scores">
          {data.scores.length ? (
            <div className="space-y-2">
              {data.scores.map((s: any) => (
                <div key={s.name + s.source} className="flex items-center justify-between text-sm">
                  <span>{s.name} <span className="text-xs text-zinc-400">({s.source})</span></span>
                  <span className="font-mono tabular-nums">{Number(s.avg).toFixed(3)} <span className="text-xs text-zinc-400">n={s.n}</span></span>
                </div>
              ))}
            </div>
          ) : <Empty title="No data">Scores come from citation checks, evals and user feedback.</Empty>}
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Observations by time (level)">
          <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={obs.data}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e4e4e7" />
              <XAxis dataKey="t" tick={tick} /><YAxis tick={tick} allowDecimals={false} /><Tooltip /><Legend />
              {obs.series.map((s) => <Area key={s} dataKey={s} stackId="1" stroke={LEVEL_COLORS[s] ?? "#71717a"} fill={LEVEL_COLORS[s] ?? "#71717a"} fillOpacity={0.25} />)}
            </AreaChart>
          </ResponsiveContainer>
        </Card>
        <Card title="Cost by model over time">
          <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={cost.data}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e4e4e7" />
              <XAxis dataKey="t" tick={tick} /><YAxis tick={tick} tickFormatter={(v) => `$${v}`} /><Tooltip formatter={(v: any) => fmtUsd(Number(v))} /><Legend />
              {cost.series.map((s, i) => <Area key={s} dataKey={s} stackId="1" stroke={["#6366f1", "#14b896", "#f59e0b", "#ec4899"][i % 4]} fill={["#6366f1", "#14b896", "#f59e0b", "#ec4899"][i % 4]} fillOpacity={0.2} />)}
            </AreaChart>
          </ResponsiveContainer>
        </Card>
      </div>

      <Card title="Slowest steps (p95 latency)" pad={false}>
        <Table head={["Observation", "Count", "p50", "p95"]}>
          {data.latency_by_name.map((r: any) => (
            <tr key={r.name}><td className="px-3 py-2 font-mono text-xs">{r.name}</td><td className="px-3 py-2">{r.n}</td>
              <td className="px-3 py-2 tabular-nums">{fmtMs(r.p50_ms)}</td><td className="px-3 py-2 tabular-nums">{fmtMs(r.p95_ms)}</td></tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}
