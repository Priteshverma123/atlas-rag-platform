import { ArrowLeft, ChevronDown, ChevronRight, MessageSquarePlus } from "lucide-react";
import { type ReactNode, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { pulse } from "../lib/api";
import { fmtMs, fmtNum, fmtTime, fmtUsd } from "../lib/format";
import { useFetch } from "../lib/hooks";
import { Badge, Button, Card, CopyButton, ErrorNote, Input, KeyValue, LevelBadge, Payload, Select, Spinner, Tabs, cx } from "../components/ui";
import { ObsIcon } from "../components/obsIcons";

type Obs = any & { children?: Obs[]; depth?: number };

function buildTree(observations: Obs[]): Obs[] {
  const byId = new Map<string, Obs>();
  observations.forEach((o) => byId.set(o.span_id, { ...o, children: [] }));
  const roots: Obs[] = [];
  byId.forEach((o) => {
    const p = o.parent_span_id && byId.get(o.parent_span_id);
    if (p) p.children!.push(o);
    else roots.push(o);
  });
  const sort = (xs: Obs[]) => { xs.sort((a, b) => +new Date(a.start_time) - +new Date(b.start_time)); xs.forEach((x) => sort(x.children!)); };
  sort(roots);
  return roots;
}

function flatten(nodes: Obs[], depth = 0, collapsed: Set<string>, out: Obs[] = []): Obs[] {
  for (const n of nodes) {
    out.push({ ...n, depth });
    if (!collapsed.has(n.span_id)) flatten(n.children ?? [], depth + 1, collapsed, out);
  }
  return out;
}

function Metric({ children }: { children: ReactNode }) {
  return <span className="rounded border border-zinc-200 px-1.5 py-0.5 font-mono text-[11px] text-zinc-600 dark:border-zinc-700 dark:text-zinc-300">{children}</span>;
}

export default function TraceDetail() {
  const { traceId } = useParams();
  const [params, setParams] = useSearchParams();
  const [internal, setInternal] = useState(false);
  const [view, setView] = useState<"tree" | "timeline">("tree");
  const [tab, setTab] = useState<"preview" | "metadata" | "scores">("preview");
  const [raw, setRaw] = useState(false);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const { data, error, loading, reload } = useFetch(() => pulse.get<any>(`/api/traces/${traceId}?include_internal=${internal}`), [traceId, internal], 5000);

  const tree = useMemo(() => (data ? buildTree(data.observations) : []), [data]);
  const rows = useMemo(() => flatten(tree, 0, collapsed), [tree, collapsed]);
  if (loading && !data) return <Spinner />;
  if (error) return <ErrorNote error={error} />;
  const t = data.trace;
  const selectedId = params.get("obs");
  const selected: Obs | null = selectedId ? data.observations.find((o: Obs) => o.span_id === selectedId) : null;
  const t0 = +new Date(t.start_time);
  const total = Math.max(1, t.latency_ms ?? 1);
  const scoresFor = (sid: string | null) => data.scores.filter((s: any) => (sid ? s.observation_id === sid : true));

  return (
    <div className="flex h-full flex-col">
      <div className="mb-3 flex items-center gap-2 text-sm text-zinc-500">
        <Link to="/traces" className="flex items-center gap-1 hover:text-zinc-800"><ArrowLeft className="h-4 w-4" />Traces</Link>
      </div>
      <div className="mb-3">
        <div className="flex items-center gap-2">
          <h1 className="font-mono text-base font-semibold">{t.trace_id}</h1><CopyButton text={t.trace_id} /><LevelBadge level={t.level} />
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-2">
          <Metric>{fmtMs(t.latency_ms)}</Metric><Metric>{fmtUsd(t.total_cost, 6)}</Metric>
          <Metric>{fmtNum((t.input_tokens ?? 0) + (t.output_tokens ?? 0))} tokens</Metric>
          {t.user_id && <Metric>user: {t.user_id}</Metric>}
          {t.session_id && <Link to={`/sessions/${encodeURIComponent(t.session_id)}`}><Metric>session: {t.session_id.slice(0, 12)}…</Metric></Link>}
          {t.tags?.map((g: string) => <Badge key={g}>{g}</Badge>)}
          {t.services?.map((s: string) => <Badge key={s} tone="blue">{s}</Badge>)}
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(320px,420px)_1fr] gap-4">
        <Card pad={false} className="min-h-0 overflow-auto">
          <div className="flex items-center justify-between border-b border-zinc-100 px-3 py-2 dark:border-zinc-800">
            <Tabs value={view} onChange={setView} items={[{ value: "tree", label: "Tree" }, { value: "timeline", label: "Timeline" }]} />
            <label className="flex items-center gap-1.5 text-xs text-zinc-500"><input type="checkbox" className="accent-brand-600" checked={internal} onChange={(e) => setInternal(e.target.checked)} />HTTP spans</label>
          </div>
          <div className="py-1">
            <button onClick={() => setParams({})} className={cx("flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm", !selected && "bg-zinc-100 dark:bg-zinc-800")}>
              <ObsIcon type="span" /><span className="font-medium">{t.name}</span><span className="ml-auto font-mono text-[11px] text-zinc-400">trace</span>
            </button>
            {rows.map((o) => {
              const left = ((+new Date(o.start_time) - t0) / total) * 100;
              const width = Math.max(0.8, ((o.latency_ms ?? 0) / total) * 100);
              const hasKids = (o.children?.length ?? 0) > 0;
              return (
                <div key={o.span_id} onClick={() => setParams({ obs: o.span_id })}
                  className={cx("cursor-pointer px-3 py-1 hover:bg-zinc-50 dark:hover:bg-zinc-800/60", selectedId === o.span_id && "bg-zinc-100 dark:bg-zinc-800")}>
                  <div className="flex items-center gap-1.5" style={{ paddingLeft: o.depth! * 14 }}>
                    {hasKids ? (
                      <button onClick={(e) => { e.stopPropagation(); const c = new Set(collapsed); c.has(o.span_id) ? c.delete(o.span_id) : c.add(o.span_id); setCollapsed(c); }} className="text-zinc-400">
                        {collapsed.has(o.span_id) ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                      </button>
                    ) : <span className="w-3.5" />}
                    <ObsIcon type={o.type} className="h-3.5 w-3.5" />
                    <span className="truncate text-sm">{o.name}</span>
                    <LevelBadge level={o.level} />
                  </div>
                  {view === "tree" ? (
                    <div className="mt-0.5 flex gap-2 font-mono text-[11px] text-zinc-400" style={{ paddingLeft: o.depth! * 14 + 34 }}>
                      <span>{fmtMs(o.latency_ms)}</span>{o.cost != null && <span>{fmtUsd(o.cost, 6)}</span>}{o.total_tokens ? <span>{fmtNum(o.total_tokens)} tok</span> : null}
                    </div>
                  ) : (
                    <div className="relative mt-1 h-2 rounded bg-zinc-100 dark:bg-zinc-800">
                      <div className={cx("absolute h-2 rounded", o.level === "ERROR" ? "bg-red-400" : o.type === "generation" ? "bg-fuchsia-400" : "bg-sky-400")}
                        style={{ left: `${Math.min(99, left)}%`, width: `${Math.min(100 - left, width)}%` }} title={fmtMs(o.latency_ms)} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </Card>

        <Card pad={false} className="min-h-0 overflow-auto">
          <div className="border-b border-zinc-100 px-4 py-3 dark:border-zinc-800">
            <div className="flex items-center gap-2">
              <ObsIcon type={selected?.type ?? "span"} /><span className="text-base font-semibold">{selected?.name ?? t.name}</span>
              {selected && <LevelBadge level={selected.level} />}{selected?.model && <Badge tone="violet" mono>{selected.model}</Badge>}
            </div>
            <div className="mt-1.5 flex flex-wrap gap-2">
              <Metric>{fmtTime(selected?.start_time ?? t.start_time)}</Metric>
              <Metric>{fmtMs(selected?.latency_ms ?? t.latency_ms)}</Metric>
              {(selected ? selected.cost : t.total_cost) != null && <Metric>{fmtUsd(selected ? selected.cost : t.total_cost, 6)}</Metric>}
              {selected?.total_tokens ? <Metric>{selected.input_tokens} → {selected.output_tokens} tokens{selected.cache_read_tokens ? ` (${selected.cache_read_tokens} cached)` : ""}</Metric> : null}
              {selected?.service && <Badge tone="blue">{selected.service}</Badge>}
            </div>
            {selected?.status_message && <div className="mt-2"><ErrorNote error={selected.status_message} /></div>}
          </div>
          <div className="flex items-center justify-between border-b border-zinc-100 px-4 py-2 dark:border-zinc-800">
            <Tabs value={tab} onChange={setTab} items={[{ value: "preview", label: "Preview" }, { value: "metadata", label: "Metadata" }, { value: "scores", label: `Scores (${scoresFor(selected?.span_id ?? null).length})` }]} />
            <Tabs value={raw ? "raw" : "fmt"} onChange={(v) => setRaw(v === "raw")} items={[{ value: "fmt", label: "Formatted" }, { value: "raw", label: "Raw" }]} />
          </div>
          <div className="space-y-4 p-4">
            {tab === "preview" && <>
              <section><h4 className="mb-1.5 text-sm font-semibold">Input</h4><Payload value={selected ? selected.input : t.input} raw={raw} /></section>
              <section><h4 className="mb-1.5 text-sm font-semibold">Output</h4><Payload value={selected ? selected.output : t.output} raw={raw} /></section>
            </>}
            {tab === "metadata" && <KeyValue data={selected ? { ...selected.metadata, service: selected.service, span_id: selected.span_id, type: selected.type } :
              { environment: t.environment, release: t.release, user_id: t.user_id, session_id: t.session_id, observations: t.observation_count, services: t.services }} />}
            {tab === "scores" && <ScoresPanel traceId={t.trace_id} observationId={selected?.span_id} scores={scoresFor(selected?.span_id ?? null)} onSaved={reload} />}
          </div>
        </Card>
      </div>
    </div>
  );
}

function ScoresPanel({ traceId, observationId, scores, onSaved }: { traceId: string; observationId?: string; scores: any[]; onSaved: () => void }) {
  const [name, setName] = useState("quality");
  const [value, setValue] = useState("1");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      await pulse.post("/api/scores", { trace_id: traceId, observation_id: observationId ?? null, name, value: Number(value), comment: comment || null, source: "annotation", author: "console" });
      setComment("");
      onSaved();
    } finally { setBusy(false); }
  };
  return (
    <div className="space-y-3">
      {scores.length ? scores.map((s) => (
        <div key={s.id} className="flex items-center justify-between rounded-md border border-zinc-200 px-3 py-2 text-sm dark:border-zinc-800">
          <span>{s.name} <Badge tone={s.source === "eval" ? "violet" : s.source === "annotation" ? "brand" : "gray"}>{s.source}</Badge></span>
          <span className="font-mono">{s.value != null ? Number(s.value).toFixed(3) : s.string_value}{s.comment ? <span className="ml-2 text-xs text-zinc-500">“{s.comment}”</span> : null}</span>
        </div>
      )) : <div className="text-sm text-zinc-400">No scores on this {observationId ? "observation" : "trace"} yet.</div>}
      <div className="rounded-md border border-dashed border-zinc-300 p-3 dark:border-zinc-700">
        <div className="mb-2 flex items-center gap-1.5 text-sm font-medium"><MessageSquarePlus className="h-4 w-4" />Annotate</div>
        <div className="grid grid-cols-[1fr_110px] gap-2">
          <Select value={name} onChange={(e) => setName(e.target.value)} options={["quality", "correctness", "groundedness", "helpfulness"].map((v) => ({ value: v, label: v }))} />
          <Select value={value} onChange={(e) => setValue(e.target.value)} options={[{ value: "1", label: "1 · good" }, { value: "0.5", label: "0.5 · partial" }, { value: "0", label: "0 · bad" }]} />
        </div>
        <Input className="mt-2" placeholder="Comment (optional)" value={comment} onChange={(e) => setComment(e.target.value)} />
        <Button className="mt-2" size="sm" variant="primary" onClick={save} loading={busy}>Save score</Button>
      </div>
    </div>
  );
}
