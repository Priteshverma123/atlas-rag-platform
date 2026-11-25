import { FileText, FolderPlus, Globe, Trash2, Type, UploadCloud } from "lucide-react";
import { useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../lib/api";
import { fmtAgo } from "../lib/format";
import { useFetch } from "../lib/hooks";
import { Button, Card, Empty, ErrorNote, Input, PageHeader, Spinner, StateBadge, Table, Tabs, cx } from "../components/ui";

export default function Knowledge() {
  const { cid } = useParams();
  const nav = useNavigate();
  const colls = useFetch(() => api.get<any[]>("/v1/collections"), [], 15000);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const current = cid ?? colls.data?.[0]?.id;

  const create = async () => {
    setCreating(true);
    setErr(null);
    try {
      const c = await api.post<any>("/v1/collections", { name });
      setName("");
      await colls.reload();
      nav(`/knowledge/${c.id}`);
    } catch (e: any) { setErr(e.message); } finally { setCreating(false); }
  };

  return (
    <div>
      <PageHeader title="Collections" subtitle="Upload documents; Atlas parses, chunks, embeds and indexes them (keyword + meaning)." />
      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        <div className="space-y-3">
          <Card pad={false}>
            {colls.loading && !colls.data ? <Spinner /> : colls.data?.map((c) => (
              <Link key={c.id} to={`/knowledge/${c.id}`}
                className={cx("block border-b border-zinc-100 px-4 py-3 last:border-0 dark:border-zinc-800", current === c.id ? "bg-zinc-50 dark:bg-zinc-800/60" : "hover:bg-zinc-50 dark:hover:bg-zinc-800/40")}>
                <div className="text-sm font-medium">{c.name}</div>
                <div className="text-xs text-zinc-500">{c.documents} documents</div>
              </Link>
            ))}
            {colls.data && !colls.data.length && <Empty title="No collections" />}
          </Card>
          <Card title="New collection">
            <div className="flex gap-2"><Input placeholder="e.g. HR handbook" value={name} onChange={(e) => setName(e.target.value)} />
              <Button onClick={create} disabled={!name} loading={creating}><FolderPlus className="h-4 w-4" /></Button></div>
            <div className="mt-2"><ErrorNote error={err} /></div>
          </Card>
        </div>
        {current ? <CollectionView key={current} cid={current} onDeleted={() => { colls.reload(); nav("/knowledge"); }} /> : <Card><Empty title="Create a collection to start" /></Card>}
      </div>
    </div>
  );
}

function CollectionView({ cid, onDeleted }: { cid: string; onDeleted: () => void }) {
  const coll = useFetch(() => api.get<any>(`/v1/collections/${cid}`), [cid]);
  const docs = useFetch(() => api.get<any[]>(`/v1/collections/${cid}/documents`), [cid], 3000);
  const [mode, setMode] = useState<"file" | "url" | "text">("file");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [acl, setAcl] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const run = async (fn: () => Promise<string>) => {
    setBusy(true); setErr(null); setMsg(null);
    try { setMsg(await fn()); docs.reload(); } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  const uploadFiles = (files: FileList | File[]) => run(async () => {
    const results = [];
    for (const f of Array.from(files)) {
      const fd = new FormData();
      fd.append("file", f);
      if (acl) fd.append("acl_groups", acl);
      const r = await api.post<any>(`/v1/collections/${cid}/documents`, fd);
      results.push(`${f.name}: ${r.queued ? "queued" : "unchanged (already indexed)"}`);
    }
    return results.join(" · ");
  });

  return (
    <div className="space-y-4">
      <Card title={coll.data?.name ?? "…"} actions={<Button size="sm" variant="danger" onClick={async () => {
        if (!confirm("Delete this collection and all its documents?")) return;
        await api.del(`/v1/collections/${cid}`); onDeleted();
      }}><Trash2 className="h-3.5 w-3.5" />Delete</Button>}>
        <div className="mb-3 flex items-center justify-between">
          <Tabs value={mode} onChange={setMode} items={[
            { value: "file", label: <span className="flex items-center gap-1"><UploadCloud className="h-3.5 w-3.5" />Files</span> },
            { value: "url", label: <span className="flex items-center gap-1"><Globe className="h-3.5 w-3.5" />Web page</span> },
            { value: "text", label: <span className="flex items-center gap-1"><Type className="h-3.5 w-3.5" />Text</span> }]} />
          <Input className="h-8 max-w-[260px] text-xs" placeholder="Access groups (optional, comma-separated)" value={acl} onChange={(e) => setAcl(e.target.value)} />
        </div>
        {mode === "file" && (
          <div onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
            onDrop={(e) => { e.preventDefault(); setDrag(false); uploadFiles(e.dataTransfer.files); }}
            onClick={() => fileRef.current?.click()}
            className={cx("flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed p-8 text-center transition",
              drag ? "border-brand-500 bg-brand-50 dark:bg-brand-700/10" : "border-zinc-300 hover:border-brand-400 dark:border-zinc-700")}>
            <UploadCloud className="mb-2 h-8 w-8 text-zinc-400" />
            <div className="text-sm font-medium">Drop files or click to upload</div>
            <div className="text-xs text-zinc-500">PDF, DOCX, PPTX, HTML, Markdown, TXT · up to 50 MB</div>
            <input ref={fileRef} type="file" multiple hidden accept=".pdf,.docx,.pptx,.html,.htm,.md,.markdown,.txt"
              onChange={(e) => e.target.files && uploadFiles(e.target.files)} />
          </div>
        )}
        {mode === "url" && (
          <div className="flex gap-2"><Input placeholder="https://…" value={url} onChange={(e) => setUrl(e.target.value)} />
            <Button variant="primary" loading={busy} disabled={!url} onClick={() => run(async () => {
              await api.post(`/v1/collections/${cid}/documents/url`, { url, acl_groups: acl ? acl.split(",") : null }); setUrl(""); return "URL queued";
            })}>Add</Button></div>
        )}
        {mode === "text" && (
          <div className="space-y-2">
            <Input placeholder="Title" value={title} onChange={(e) => setTitle(e.target.value)} />
            <textarea className="h-32 w-full rounded-md border border-zinc-200 bg-white p-3 text-sm dark:border-zinc-700 dark:bg-zinc-900" placeholder="Paste Markdown or plain text…" value={text} onChange={(e) => setText(e.target.value)} />
            <Button variant="primary" loading={busy} disabled={!title || !text} onClick={() => run(async () => {
              await api.post(`/v1/collections/${cid}/documents/text`, { title, text, acl_groups: acl ? acl.split(",") : null }); setText(""); setTitle(""); return "Text queued";
            })}>Add text</Button>
          </div>
        )}
        <div className="mt-3 space-y-2">{msg && <div className="text-sm text-emerald-600">{msg}</div>}<ErrorNote error={err} /></div>
      </Card>

      <Card title={`Documents (${docs.data?.length ?? 0})`} pad={false}>
        {docs.loading && !docs.data ? <Spinner /> : (
          <Table head={["Document", "Type", "Version", "State", "Pages", "Updated", ""]}>
            {docs.data?.map((d) => (
              <tr key={d.id} className="hover:bg-zinc-50 dark:hover:bg-zinc-800/50">
                <td className="px-3 py-2"><Link to={`/documents/${d.id}`} className="flex items-center gap-2 font-medium hover:text-brand-600"><FileText className="h-4 w-4 text-zinc-400" />{d.title || d.source_uri}</Link>
                  {d.error && <div className="mt-0.5 text-xs text-red-600">{d.error}</div>}</td>
                <td className="px-3 py-2 text-xs text-zinc-500">{d.mime.split("/").pop()?.replace("vnd.openxmlformats-officedocument.", "")}</td>
                <td className="px-3 py-2 font-mono text-xs">v{d.version}</td>
                <td className="px-3 py-2"><StateBadge state={d.state} /></td>
                <td className="px-3 py-2 text-xs">{d.page_count ?? "—"}</td>
                <td className="px-3 py-2 text-xs text-zinc-500">{fmtAgo(d.updated_at)}</td>
                <td className="px-3 py-2 text-right">
                  <button title="Delete" className="text-zinc-400 hover:text-red-600" onClick={async () => { if (confirm("Delete document?")) { await api.del(`/v1/documents/${d.id}`); docs.reload(); } }}><Trash2 className="h-4 w-4" /></button>
                </td>
              </tr>
            ))}
          </Table>
        )}
        {docs.data && !docs.data.length && <Empty title="No documents yet">Upload a file above. Processing usually takes a few seconds; this table refreshes automatically.</Empty>}
      </Card>
    </div>
  );
}
