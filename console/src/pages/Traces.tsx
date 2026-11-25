import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useApp } from "../App";
import { pulse } from "../lib/api";
import { fmtMs, fmtNum, fmtTime, fmtUsd, rangeParams, shortId } from "../lib/format";
import { useFetch } from "../lib/hooks";
import { Badge, Button, Card, Empty, ErrorNote, Input, LevelBadge, PageHeader, Spinner, Table, Tabs, cx } from "../components/ui";
import { ObsIcon, obsLabel } from "../components/obsIcons";

function Facet({ title, items, selected, toggle }: { title: string; items: { value: string; n: number }[]; selected: Set<string>; toggle: (v: string) => void }) {
  if (!items?.length) return null;
  return (
    <div className="mb-4">
      <div className="mb-1.5 text-xs font-semibold text-zinc-600 dark:text-zinc-300">{title}</div>
      {items.map((it) => (
        <label key={it.value} className="flex cursor-pointer items-center justify-between gap-2 rounded px-1 py-0.5 text-xs hover:bg-zinc-50 dark:hover:bg-zinc-800">
          <span className="flex min-w-0 items-center gap-2">
            <input type="checkbox" className="accent-brand-600" checked={selected.size === 0 || selected.has(it.value)} onChange={() => toggle(it.value)} />
            <span className="truncate">{title === "Type" ? obsLabel(it.value) : it.value}</span>
          </span>
          <span className="tabular-nums text-zinc-400">{it.n}</span>
        </label>
      ))}
    </div>
  );
}

export default function Traces() {
  const { rangeHours } = useApp();
  const nav = useNavigate();
  const [view, setView] = useState<"traces" | "observations">("traces");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [types, setTypes] = useState<Set<string>>(new Set());
  const [names, setNames] = useState<Set<string>>(new Set());
  const [levels, setLevels] = useState<Set<string>>(new Set());
  const limit = 50;

  const toggle = (set: Set<string>, setter: (s: Set<string>) => void, all: string[]) => (v: string) => {
    const cur = set.size === 0 ? new Set(all) : new Set(set);
    cur.has(v) ? cur.delete(v) : cur.add(v);
    setter(cur.size === all.length ? new Set() : cur);
    setPage(0);
  };

  const qs = `${rangeParams(rangeHours)}&limit=${limit}&offset=${page * limit}${query ? `&search=${encodeURIComponent(query)}` : ""}`;
  const traces = useFetch(() => pulse.get<any>(`/api/traces?${qs}${levels.size === 1 ? `&level=${[...levels][0]}` : ""}`), [qs, levels, view], 10000);
  const obsQs = `${qs}${types.size ? `&type=${[...types].join(",")}` : ""}${names.size ? `&name=${encodeURIComponent([...names].join(","))}` : ""}${levels.size ? `&level=${[...levels].join(",")}` : ""}`;
  const observations = useFetch(() => (view === "observations" ? pulse.get<any>(`/api/observations?${obsQs}`) : Promise.resolve(null)), [obsQs, view], 10000);

  const active = view === "traces" ? traces : observations;
  const facets = observations.data?.facets;
  const total = active.data?.total ?? 0;

  return (
    <div>
      <PageHeader title="Tracing" subtitle="Every request, ingestion job and agent run, as a tree of observations."
        actions={<>
          <Tabs value={view} onChange={(v) => { setView(v); setPage(0); }} items={[{ value: "traces", label: "Traces" }, { value: "observations", label: "Observations" }]} />
          <Button size="sm" variant="ghost" onClick={() => active.reload()}><RefreshCw className="h-3.5 w-3.5" /></Button>
        </>} />
      <form onSubmit={(e) => { e.preventDefault(); setQuery(search); setPage(0); }} className="mb-4">
        <Input placeholder="Search name, input, output or trace id… (Enter)" value={search} onChange={(e) => setSearch(e.target.value)} />
      </form>
      <div className="flex gap-4">
        {view === "observations" && (
          <Card className="w-56 shrink-0 self-start" title="Filters">
            {facets ? <>
              <Facet title="Type" items={facets.type.map((f: any) => ({ value: f.value, n: f.n }))} selected={types} toggle={toggle(types, setTypes, facets.type.map((f: any) => f.value))} />
              <Facet title="Level" items={facets.level.map((f: any) => ({ value: f.value, n: f.n }))} selected={levels} toggle={toggle(levels, setLevels, facets.level.map((f: any) => f.value))} />
              <Facet title="Name" items={facets.name.map((f: any) => ({ value: f.value, n: f.n }))} selected={names} toggle={toggle(names, setNames, facets.name.map((f: any) => f.value))} />
            </> : <Spinner />}
          </Card>
        )}
        <Card className="min-w-0 flex-1" pad={false}>
          <ErrorNote error={active.error} />
          {active.loading && !active.data ? <Spinner /> : view === "traces" ? (
            <Table head={["Start time", "Name", "Input", "Output", "Latency", "Tokens", "Cost", "User", "Session", ""]}>
              {traces.data?.data.map((t: any) => (
                <tr key={t.trace_id} onClick={() => nav(`/traces/${t.trace_id}`)} className="cursor-pointer hover:bg-zinc-50 dark:hover:bg-zinc-800/50">
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">{fmtTime(t.start_time)}</td>
                  <td className="px-3 py-2"><span className="font-medium">{t.name}</span>{t.tags?.length ? <div className="mt-0.5 flex gap-1">{t.tags.map((g: string) => <Badge key={g}>{g}</Badge>)}</div> : null}</td>
                  <td className="max-w-[260px] truncate px-3 py-2 text-xs text-zinc-600 dark:text-zinc-400" title={t.input}>{t.input ?? "—"}</td>
                  <td className="max-w-[260px] truncate px-3 py-2 text-xs text-zinc-600 dark:text-zinc-400" title={t.output}>{t.output ?? "—"}</td>
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">{fmtMs(t.latency_ms)}</td>
                  <td className="px-3 py-2 font-mono text-xs">{fmtNum((t.input_tokens ?? 0) + (t.output_tokens ?? 0))}</td>
                  <td className="px-3 py-2 font-mono text-xs">{fmtUsd(t.total_cost)}</td>
                  <td className="px-3 py-2 font-mono text-xs">{shortId(t.user_id, 14)}</td>
                  <td className="px-3 py-2 font-mono text-xs">{shortId(t.session_id)}</td>
                  <td className="px-3 py-2"><LevelBadge level={t.level} /></td>
                </tr>
              ))}
            </Table>
          ) : (
            <Table head={["Start time", "Type", "Name", "Trace", "Input", "Output", "Latency", "Model", "Cost", ""]}>
              {observations.data?.data.map((o: any) => (
                <tr key={o.span_id} onClick={() => nav(`/traces/${o.trace_id}?obs=${o.span_id}`)} className="cursor-pointer hover:bg-zinc-50 dark:hover:bg-zinc-800/50">
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">{fmtTime(o.start_time)}</td>
                  <td className="px-3 py-2"><ObsIcon type={o.type} /></td>
                  <td className="px-3 py-2 text-sm">{o.name}</td>
                  <td className="px-3 py-2 text-xs text-zinc-500">{o.trace_name}</td>
                  <td className="max-w-[220px] truncate px-3 py-2 text-xs text-zinc-600 dark:text-zinc-400" title={o.input}>{o.input ?? "—"}</td>
                  <td className="max-w-[220px] truncate px-3 py-2 text-xs text-zinc-600 dark:text-zinc-400" title={o.output}>{o.output ?? "—"}</td>
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">{fmtMs(o.latency_ms)}</td>
                  <td className="px-3 py-2 font-mono text-xs">{o.model ?? ""}</td>
                  <td className="px-3 py-2 font-mono text-xs">{o.cost != null ? fmtUsd(o.cost, 6) : ""}</td>
                  <td className="px-3 py-2"><LevelBadge level={o.level} /></td>
                </tr>
              ))}
            </Table>
          )}
          {active.data && !active.data.data.length && <Empty title="Nothing here yet">Traces appear a few seconds after you use Atlas.</Empty>}
          <div className="flex items-center justify-between border-t border-zinc-100 px-4 py-2 text-xs text-zinc-500 dark:border-zinc-800">
            <span>Total {total}</span>
            <div className="flex items-center gap-2">
              <span>Page {page + 1} / {Math.max(1, Math.ceil(total / limit))}</span>
              <Button size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>‹</Button>
              <Button size="sm" disabled={(page + 1) * limit >= total} onClick={() => setPage(page + 1)}>›</Button>
            </div>
          </div>
        </Card>
      </div>
      <p className={cx("mt-2 text-xs text-zinc-400")}>Auto-refreshes every 10s.</p>
    </div>
  );
}
