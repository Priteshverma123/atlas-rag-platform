import { Link, useParams } from "react-router-dom";
import { useApp } from "../App";
import { pulse } from "../lib/api";
import { fmtAgo, fmtMs, fmtNum, fmtTime, fmtUsd, rangeParams } from "../lib/format";
import { useFetch } from "../lib/hooks";
import { Card, Empty, ErrorNote, LevelBadge, PageHeader, Spinner, Table } from "../components/ui";

export default function Sessions() {
  const { sessionId } = useParams();
  const { rangeHours } = useApp();
  const list = useFetch(() => pulse.get<any>(`/api/sessions?${rangeParams(Math.max(rangeHours, 24 * 7))}`), [rangeHours], 15000);
  if (sessionId) return <SessionDetail id={sessionId} />;
  return (
    <div>
      <PageHeader title="Sessions" subtitle="Traces grouped by conversation (chat), document (ingestion) or research job." />
      <Card pad={false}>
        <ErrorNote error={list.error} />
        {list.loading && !list.data ? <Spinner /> : (
          <Table head={["Session", "Traces", "Users", "Tokens", "Cost", "Last activity"]}>
            {list.data?.data.map((s: any) => (
              <tr key={s.session_id} className="hover:bg-zinc-50 dark:hover:bg-zinc-800/50">
                <td className="px-3 py-2 font-mono text-xs"><Link to={`/sessions/${encodeURIComponent(s.session_id)}`} className="hover:text-brand-600">{s.session_id}</Link></td>
                <td className="px-3 py-2">{s.traces}</td><td className="px-3 py-2 font-mono text-xs">{(s.users ?? []).join(", ")}</td>
                <td className="px-3 py-2 font-mono text-xs">{fmtNum(s.tokens)}</td><td className="px-3 py-2 font-mono text-xs">{fmtUsd(s.cost)}</td>
                <td className="px-3 py-2 text-xs text-zinc-500">{fmtAgo(s.last_seen)}</td>
              </tr>
            ))}
          </Table>
        )}
        {list.data && !list.data.data.length && <Empty title="No sessions yet" />}
      </Card>
    </div>
  );
}

function SessionDetail({ id }: { id: string }) {
  const s = useFetch(() => pulse.get<any>(`/api/sessions/${encodeURIComponent(id)}`), [id]);
  return (
    <div>
      <Link to="/sessions" className="text-sm text-zinc-500 hover:text-zinc-800">← Sessions</Link>
      <PageHeader title={<span className="font-mono text-base">{id}</span>} subtitle={`${s.data?.traces.length ?? 0} traces`} />
      {s.loading && !s.data ? <Spinner /> : (
        <div className="space-y-3">
          {s.data?.traces.map((t: any) => (
            <Card key={t.trace_id} title={<span className="flex items-center gap-2">{t.name}<LevelBadge level={t.level} /></span>}
              actions={<Link to={`/traces/${t.trace_id}`} className="text-xs text-brand-600 hover:underline">open trace</Link>}>
              <div className="grid gap-3 md:grid-cols-2">
                <div><div className="mb-1 text-xs font-semibold text-zinc-500">Input</div><div className="whitespace-pre-wrap text-sm">{t.input ?? "—"}</div></div>
                <div><div className="mb-1 text-xs font-semibold text-zinc-500">Output</div><div className="line-clamp-6 whitespace-pre-wrap text-sm">{t.output ?? "—"}</div></div>
              </div>
              <div className="mt-2 font-mono text-[11px] text-zinc-400">{fmtTime(t.start_time)} · {fmtMs(t.latency_ms)} · {fmtUsd(t.total_cost, 6)} · {fmtNum((t.input_tokens ?? 0) + (t.output_tokens ?? 0))} tokens</div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
