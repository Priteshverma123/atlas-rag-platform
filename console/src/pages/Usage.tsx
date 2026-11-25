import { useApp } from "../App";
import { api } from "../lib/api";
import { fmtMs, fmtNum, fmtUsd } from "../lib/format";
import { useFetch } from "../lib/hooks";
import { Card, Empty, ErrorNote, PageHeader, Spinner, Stat, Table } from "../components/ui";

export default function Usage() {
  const { rangeHours } = useApp();
  const days = Math.max(1, Math.ceil(rangeHours / 24));
  const u = useFetch(() => api.get<any>(`/v1/usage?days=${days}`), [days], 20000);
  return (
    <div className="space-y-4">
      <PageHeader title="Usage & cost" subtitle="From the gateway's usage ledger: every LLM call, priced from a versioned price table, including what caches saved." />
      <ErrorNote error={u.error} />
      {u.loading && !u.data ? <Spinner /> : <>
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <Stat label={`Spend (${days}d)`} value={fmtUsd(u.data.total_cost_usd)} />
          <Stat label="Saved by caches" value={fmtUsd(u.data.total_saved_usd)} hint="exact + semantic + provider prompt cache" />
          <Stat label="LLM calls" value={fmtNum(u.data.rows.reduce((a: number, r: any) => a + Number(r.calls), 0))} />
          <Stat label="Tokens" value={fmtNum(u.data.rows.reduce((a: number, r: any) => a + Number(r.input ?? 0) + Number(r.output ?? 0), 0))} />
        </div>
        <Card pad={false}>
          <Table head={["Day", "Route", "Model", "Calls", "Input", "Output", "Cache read", "Cost", "Saved", "p95", "Cache hit"]}>
            {u.data.rows.map((r: any, i: number) => (
              <tr key={i}>
                <td className="px-3 py-2 font-mono text-xs">{String(r.day).slice(0, 10)}</td><td className="px-3 py-2">{r.route}</td><td className="px-3 py-2 font-mono text-xs">{r.model}</td>
                <td className="px-3 py-2">{r.calls}</td><td className="px-3 py-2 font-mono text-xs">{fmtNum(r.input)}</td><td className="px-3 py-2 font-mono text-xs">{fmtNum(r.output)}</td>
                <td className="px-3 py-2 font-mono text-xs">{fmtNum(r.cache_read)}</td><td className="px-3 py-2 font-mono text-xs">{fmtUsd(Number(r.cost))}</td>
                <td className="px-3 py-2 font-mono text-xs">{fmtUsd(Number(r.saved))}</td><td className="px-3 py-2 font-mono text-xs">{fmtMs(r.p95_ms)}</td>
                <td className="px-3 py-2 font-mono text-xs">{(100 * Number(r.cache_hit_rate)).toFixed(0)}%</td>
              </tr>
            ))}
          </Table>
          {!u.data.rows.length && <Empty title="No LLM calls in this window" />}
        </Card>
      </>}
    </div>
  );
}
