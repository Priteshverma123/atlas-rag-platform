import { Telescope } from "lucide-react";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, streamSSE } from "../lib/api";
import { fmtAgo, fmtUsd } from "../lib/format";
import { useFetch } from "../lib/hooks";
import { Badge, Button, Card, Empty, ErrorNote, Input, PageHeader, Select, Spinner, Tabs, cx } from "../components/ui";

export default function Research() {
  const { jobId } = useParams();
  const nav = useNavigate();
  const colls = useFetch(() => api.get<any[]>("/v1/collections"), []);
  const jobs = useFetch(() => api.get<any[]>("/v1/research"), [], 5000);
  const [cid, setCid] = useState("");
  const [q, setQ] = useState("Summarise the company's main risks, policies on security incidents and the KD-200 maintenance rules.");
  const [effort, setEffort] = useState<"low" | "medium" | "high">("low");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (!cid && colls.data?.length) setCid(colls.data[0].id); }, [colls.data]);

  const start = async () => {
    setBusy(true); setErr(null);
    try { const r = await api.post<any>("/v1/research", { question: q, collection_ids: [cid], effort }); jobs.reload(); nav(`/research/${r.job_id}`); }
    catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };

  return (
    <div className="space-y-4">
      <PageHeader title="Deep research" subtitle="Planner → parallel researchers → writer → reviewer, with a budget. Progress streams live." />
      <Card>
        <div className="space-y-2">
          <Input value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="flex flex-wrap items-center gap-2">
            <Select className="w-56" value={cid} onChange={(e) => setCid(e.target.value)} options={(colls.data ?? []).map((c) => ({ value: c.id, label: c.name }))} />
            <Tabs value={effort} onChange={setEffort} items={[{ value: "low", label: "Low · $0.05" }, { value: "medium", label: "Medium · $0.25" }, { value: "high", label: "High · $1" }]} />
            <Button variant="primary" onClick={start} loading={busy} disabled={!cid || q.length < 5}><Telescope className="h-4 w-4" />Start research</Button>
          </div>
          <ErrorNote error={err} />
        </div>
      </Card>
      <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
        <Card title="Jobs" pad={false}>
          {jobs.data?.map((j) => (
            <Link key={j.job_id} to={`/research/${j.job_id}`} className={cx("block border-b border-zinc-100 px-4 py-2 dark:border-zinc-800", j.job_id === jobId ? "bg-zinc-50 dark:bg-zinc-800/60" : "hover:bg-zinc-50 dark:hover:bg-zinc-800/40")}>
              <div className="line-clamp-2 text-sm">{j.question}</div>
              <div className="mt-1 flex items-center gap-2 text-[11px] text-zinc-500"><Badge tone={j.status === "succeeded" ? "green" : j.status === "failed" ? "red" : "blue"}>{j.status}</Badge>{j.effort} · {fmtAgo(j.created_at)}</div>
            </Link>
          ))}
          {jobs.data && !jobs.data.length && <Empty title="No research jobs yet" />}
        </Card>
        {jobId ? <JobView key={jobId} jobId={jobId} /> : <Card><Empty title="Start a research job or pick one" /></Card>}
      </div>
    </div>
  );
}

function JobView({ jobId }: { jobId: string }) {
  const job = useFetch(() => api.get<any>(`/v1/research/${jobId}`), [jobId], 4000);
  const [events, setEvents] = useState<any[]>([]);
  useEffect(() => {
    const ctrl = new AbortController();
    setEvents([]);
    streamSSE(`/v1/research/${jobId}/events`, null, (type, data) => setEvents((e) => [...e, { type, ...data }]), ctrl.signal, "GET").catch(() => undefined);
    return () => ctrl.abort();
  }, [jobId]);
  if (!job.data) return <Card><Spinner /></Card>;
  const j = job.data;
  return (
    <div className="space-y-4">
      <Card title="Live progress" actions={<Badge tone={j.status === "succeeded" ? "green" : j.status === "failed" ? "red" : "blue"}>{j.status}</Badge>}>
        <ol className="space-y-1.5 text-sm">
          {events.map((e, i) => (
            <li key={i} className="flex gap-2"><Badge tone="violet">{e.type}</Badge>
              <span className="text-zinc-600 dark:text-zinc-300">
                {e.type === "plan" && `${e.sub_questions?.length} sub-questions: ${e.sub_questions?.join(" · ")}`}
                {e.type === "evidence" && `${e.findings} findings for "${e.sub_question}"`}
                {e.type === "draft" && `draft written (${e.chars} chars)`}
                {e.type === "review" && (e.approved ? "approved" : `gaps: ${(e.gaps ?? []).join("; ")}`)}
                {e.type === "done" && `done · spent ${fmtUsd(e.spent?.usd, 4)} of ${fmtUsd(e.spent?.budget_usd, 2)}`}
                {e.type === "error" && e.message}
              </span></li>
          ))}
          {!events.length && <li className="text-zinc-400">Waiting for the worker…</li>}
        </ol>
      </Card>
      {j.report?.markdown && (
        <Card title="Report" actions={j.report.partial ? <Badge tone="amber">partial (budget)</Badge> : null}>
          <div className="prose-answer text-sm"><ReactMarkdown remarkPlugins={[remarkGfm]}>{j.report.markdown}</ReactMarkdown></div>
        </Card>
      )}
      {j.error && <ErrorNote error={j.error} />}
    </div>
  );
}
