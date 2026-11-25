import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { fmtMs } from "../lib/format";
import { useFetch } from "../lib/hooks";
import { Badge, Button, Card, Empty, ErrorNote, Input, PageHeader, Select, Tabs } from "../components/ui";

export default function Search() {
  const colls = useFetch(() => api.get<any[]>("/v1/collections"), []);
  const [cid, setCid] = useState("");
  const [query, setQuery] = useState("E-1042 compass");
  const [mode, setMode] = useState<"hybrid" | "dense" | "sparse">("hybrid");
  const [rerank, setRerank] = useState(true);
  const [res, setRes] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (!cid && colls.data?.length) setCid(colls.data[0].id); }, [colls.data]);

  const run = async () => {
    setBusy(true); setErr(null);
    try { setRes(await api.post("/v1/search", { query, collection_ids: [cid], top_k: 10, mode, rerank, debug: true })); }
    catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  const timings = res?.timings_ms ?? {};
  const totalMs = Math.max(1, timings.total ?? 1);

  return (
    <div className="space-y-4">
      <PageHeader title="Search playground" subtitle="Hit testing: see how keyword (BM25) and meaning (dense) search rank each chunk, how they fuse, and what the reranker changes." />
      <Card>
        <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); run(); }}>
          <Select className="w-56" value={cid} onChange={(e) => setCid(e.target.value)} options={(colls.data ?? []).map((c) => ({ value: c.id, label: c.name }))} />
          <Input className="min-w-[280px] flex-1" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Query…" />
          <Tabs value={mode} onChange={setMode} items={[{ value: "hybrid", label: "Hybrid" }, { value: "dense", label: "Dense" }, { value: "sparse", label: "BM25" }]} />
          <label className="flex items-center gap-1.5 text-sm"><input type="checkbox" className="accent-brand-600" checked={rerank} onChange={(e) => setRerank(e.target.checked)} />Rerank</label>
          <Button variant="primary" type="submit" loading={busy} disabled={!cid}>Search</Button>
        </form>
        <div className="mt-2"><ErrorNote error={err} /></div>
      </Card>

      {res && (
        <>
          <Card title="Latency breakdown" actions={<span className="text-xs text-zinc-500">{res.reranked ? "reranked" : "rerank skipped (retrievers agreed or disabled)"}</span>}>
            <div className="flex h-6 overflow-hidden rounded-md">
              {["encode", "search", "rerank"].map((k, i) => timings[k] ? (
                <div key={k} title={`${k}: ${fmtMs(timings[k])}`} style={{ width: `${(100 * timings[k]) / totalMs}%` }}
                  className={["bg-teal-400", "bg-indigo-400", "bg-amber-400"][i] + " flex items-center justify-center text-[10px] text-white"}>{k}</div>
              ) : null)}
            </div>
            <div className="mt-2 flex gap-4 font-mono text-xs text-zinc-500">{Object.entries(timings).map(([k, v]) => <span key={k}>{k} {fmtMs(v as number)}</span>)}</div>
          </Card>
          <Card title={`Results (${res.results.length})`} pad={false}>
            {res.results.length ? res.results.map((r: any, i: number) => (
              <div key={r.chunk_id} className="border-b border-zinc-100 px-4 py-3 last:border-0 dark:border-zinc-800">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-xs text-zinc-400">#{i + 1}</span><span className="text-sm font-medium">{r.title}</span>
                  <span className="text-xs text-zinc-500">{r.section_path}</span>
                  <span className="ml-auto flex gap-1">
                    {r.ranks.dense && <Badge tone="blue" mono>dense #{r.ranks.dense}</Badge>}
                    {r.ranks.sparse && <Badge tone="amber" mono>bm25 #{r.ranks.sparse}</Badge>}
                    {r.scores.rerank != null && <Badge tone="violet" mono>rerank {r.scores.rerank.toFixed(3)}</Badge>}
                    <Badge mono>rrf {r.fused_score.toFixed(4)}</Badge>
                  </span>
                </div>
                {r.context && <div className="mt-1 text-xs italic text-zinc-500">{r.context}</div>}
                <p className="mt-1 line-clamp-3 text-sm text-zinc-700 dark:text-zinc-300">{r.text}</p>
              </div>
            )) : <Empty title="No results" />}
          </Card>
        </>
      )}
    </div>
  );
}
