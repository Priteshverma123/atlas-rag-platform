import { ArrowLeft, CheckCircle2, Circle, Loader2, RotateCw, XCircle } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../lib/api";
import { fmtMs } from "../lib/format";
import { useFetch } from "../lib/hooks";
import { Badge, Button, Card, Empty, ErrorNote, PageHeader, Spinner, StateBadge, cx } from "../components/ui";

const STAGES = ["fetching", "parsing", "chunking", "embedding", "indexing", "enriching"];

export default function DocumentPage() {
  const { did } = useParams();
  const doc = useFetch(() => api.get<any>(`/v1/documents/${did}`), [did], 2500);
  const chunks = useFetch(() => api.get<any[]>(`/v1/documents/${did}/chunks`), [did, doc.data?.state]);
  const [open, setOpen] = useState<string | null>(null);
  if (doc.loading && !doc.data) return <Spinner />;
  if (doc.error) return <ErrorNote error={doc.error} />;
  const d = doc.data;
  const byStage = Object.fromEntries(d.stages.map((s: any) => [s.stage, s]));
  const leaves = (chunks.data ?? []).filter((c) => c.kind !== "parent");

  return (
    <div className="space-y-4">
      <Link to={`/knowledge/${d.collection_id}`} className="flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-800"><ArrowLeft className="h-4 w-4" />Collection</Link>
      <PageHeader title={d.title || d.source_uri} subtitle={<span className="font-mono text-xs">{d.source_uri}</span>}
        actions={<><StateBadge state={d.state} /><Badge mono>v{d.version}</Badge>
          <Button size="sm" onClick={async () => { await api.post(`/v1/documents/${did}/reprocess`); doc.reload(); }}><RotateCw className="h-3.5 w-3.5" />Re-process</Button></>} />
      {d.error && <ErrorNote error={d.error} />}

      <Card title="Processing pipeline">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
          {STAGES.map((name) => {
            const s = byStage[name];
            const icon = !s ? <Circle className="h-5 w-5 text-zinc-300" /> : s.status === "done" ? <CheckCircle2 className="h-5 w-5 text-emerald-500" />
              : s.status === "failed" ? <XCircle className="h-5 w-5 text-red-500" /> : <Loader2 className="h-5 w-5 animate-spin text-sky-500" />;
            return (
              <div key={name} className={cx("rounded-md border p-3", s?.status === "failed" ? "border-red-200 dark:border-red-900" : "border-zinc-200 dark:border-zinc-800")}>
                <div className="flex items-center gap-2">{icon}<span className="text-sm font-medium capitalize">{name}</span></div>
                <div className="mt-1 font-mono text-[11px] text-zinc-500">{s?.duration_ms != null ? fmtMs(s.duration_ms) : name === "enriching" ? "background" : "—"}</div>
                {s?.stats && Object.keys(s.stats).length > 0 && (
                  <div className="mt-1 space-y-0.5 text-[11px] text-zinc-500">
                    {Object.entries(s.stats).slice(0, 4).map(([k, v]) => <div key={k} className="truncate">{k}: <span className="font-mono">{String(v)}</span></div>)}
                  </div>
                )}
                {s?.error && <div className="mt-1 text-[11px] text-red-600">{s.error}</div>}
              </div>
            );
          })}
        </div>
        {d.summary && <div className="mt-4 rounded-md bg-zinc-50 p-3 text-sm dark:bg-zinc-950"><div className="mb-1 text-xs font-semibold text-zinc-500">Summary (summary index)</div>{d.summary}</div>}
      </Card>

      <Card title={`Chunks (${leaves.length})`} actions={<span className="text-xs text-zinc-500">What the index actually contains</span>} pad={false}>
        {chunks.loading && !chunks.data ? <Spinner /> : leaves.length ? (
          <div className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {leaves.map((c) => (
              <div key={c.id} className="px-4 py-3">
                <button className="flex w-full items-center gap-2 text-left" onClick={() => setOpen(open === c.id ? null : c.id)}>
                  <Badge mono>#{c.ordinal}</Badge><Badge tone={c.kind === "table" ? "violet" : "gray"}>{c.kind}</Badge>
                  <span className="truncate text-xs text-zinc-500">{c.section_path || "—"}</span>
                  <span className="ml-auto shrink-0 font-mono text-[11px] text-zinc-400">{c.page_start ? `p.${c.page_start}${c.page_end !== c.page_start ? `–${c.page_end}` : ""} · ` : ""}{c.tokens} tok</span>
                </button>
                {c.context && <div className="mt-2 rounded border-l-2 border-brand-400 bg-brand-50/50 px-2 py-1 text-xs text-zinc-600 dark:bg-brand-700/10 dark:text-zinc-300"><b>Context:</b> {c.context}</div>}
                <pre className={cx("mt-2 whitespace-pre-wrap break-words font-sans text-sm text-zinc-700 dark:text-zinc-300", open !== c.id && "line-clamp-3")}>{c.text}</pre>
              </div>
            ))}
          </div>
        ) : <Empty title="No chunks yet">They appear once the document reaches the indexing stage.</Empty>}
      </Card>
    </div>
  );
}
