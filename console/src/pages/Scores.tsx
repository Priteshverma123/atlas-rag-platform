import { Link } from "react-router-dom";
import { pulse } from "../lib/api";
import { fmtAgo } from "../lib/format";
import { useFetch } from "../lib/hooks";
import { Badge, Card, Empty, ErrorNote, PageHeader, Spinner, Stat, Table } from "../components/ui";

export default function Scores() {
  const s = useFetch(() => pulse.get<any>("/api/scores?limit=200"), [], 15000);
  return (
    <div className="space-y-4">
      <PageHeader title="Scores" subtitle="Quality signals: automatic (citation support, abstentions), human annotations and user feedback." />
      <ErrorNote error={s.error} />
      {s.loading && !s.data ? <Spinner /> : <>
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {s.data?.summary.map((x: any) => (
            <Stat key={x.name + x.source} label={`${x.name} · ${x.source}`} value={x.avg != null ? Number(x.avg).toFixed(3) : "—"} hint={`n=${x.n} · min ${Number(x.min ?? 0).toFixed(2)} · max ${Number(x.max ?? 0).toFixed(2)}`} />
          ))}
        </div>
        <Card pad={false}>
          <Table head={["Name", "Value", "Source", "Trace", "Comment", "When"]}>
            {s.data?.data.map((r: any) => (
              <tr key={r.id}>
                <td className="px-3 py-2">{r.name}</td><td className="px-3 py-2 font-mono text-xs">{r.value != null ? Number(r.value).toFixed(3) : r.string_value}</td>
                <td className="px-3 py-2"><Badge tone={r.source === "eval" ? "violet" : r.source === "annotation" ? "brand" : "gray"}>{r.source}</Badge></td>
                <td className="px-3 py-2 text-xs"><Link className="text-brand-600 hover:underline" to={`/traces/${r.trace_id}${r.observation_id ? `?obs=${r.observation_id}` : ""}`}>{r.trace_name ?? r.trace_id.slice(0, 8)}</Link></td>
                <td className="px-3 py-2 text-xs text-zinc-500">{r.comment ?? ""}</td><td className="px-3 py-2 text-xs text-zinc-500">{fmtAgo(r.created_at)}</td>
              </tr>
            ))}
          </Table>
          {s.data && !s.data.data.length && <Empty title="No scores yet" />}
        </Card>
      </>}
    </div>
  );
}
