import { KeyRound, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { useFetch } from "../lib/hooks";
import { Badge, Button, Card, CopyButton, ErrorNote, Input, KeyValue, PageHeader, Spinner, Table } from "../components/ui";

export default function Settings() {
  const info = useFetch(() => api.get<any>("/v1/system/info"), []);
  const keys = useFetch(() => api.get<any[]>("/v1/admin/keys"), []);
  const [name, setName] = useState("");
  const [created, setCreated] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const create = async () => {
    setErr(null);
    try {
      const r = await api.post<any>("/v1/admin/keys", { name, scopes: ["read", "write", "llm"] });
      setCreated(r.api_key); setName(""); keys.reload();
    } catch (e: any) { setErr(e.message); }
  };

  return (
    <div className="space-y-4">
      <PageHeader title="Settings" subtitle="System configuration, LLM gateway routes and API keys." />
      {info.loading && !info.data ? <Spinner /> : info.data && <>
        <Card title="LLM mode" actions={info.data.llm_live ? <Badge tone="green">live providers</Badge> : <Badge tone="amber">mock LLM (zero spend)</Badge>}>
          <p className="text-sm text-zinc-600 dark:text-zinc-300">
            {info.data.llm_live
              ? "Real providers are enabled (ATLAS_LLM_LIVE=true). Costs are tracked per call and capped by per-key and per-tenant budgets."
              : "The gateway is routing every call to the deterministic mock LLM, so no API key is used and nothing is spent. Set ATLAS_LLM_LIVE=true in .env and restart the gateway to use real providers."}
          </p>
        </Card>
        <Card title="Gateway routes" pad={false}>
          <Table head={["Route", "Targets (in order)"]}>
            {info.data.gateway_routes.map((r: any) => (
              <tr key={r.id}><td className="px-3 py-2 font-mono text-sm">{r.id}</td>
                <td className="px-3 py-2"><div className="flex flex-wrap gap-1">{r.targets.map((t: string) => <Badge key={t} mono>{t}</Badge>)}</div></td></tr>
            ))}
          </Table>
        </Card>
        <Card title="System"><KeyValue data={{
          env: info.data.env, embed_model: `${info.data.embed_model} (${info.data.embed_dim}d)`, rerank: info.data.rerank_enabled,
          parser: info.data.parser, contextual_enrichment: info.data.contextual_enrichment, trace_content_capture: info.data.capture_content,
          pipeline_version: info.data.pipeline_version, ...Object.fromEntries(Object.entries(info.data.retrieval_defaults).map(([k, v]) => [`retrieval.${k}`, v])),
        }} /></Card>
      </>}
      <Card title="API keys" actions={<div className="flex gap-2"><Input className="h-8 w-48 text-xs" placeholder="Key name" value={name} onChange={(e) => setName(e.target.value)} />
        <Button size="sm" onClick={create} disabled={!name}><Plus className="h-3.5 w-3.5" />Create</Button></div>} pad={false}>
        {created && <div className="m-3 flex items-center gap-2 rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm dark:border-emerald-900 dark:bg-emerald-950">
          <KeyRound className="h-4 w-4" />Copy now, it won't be shown again: <code className="font-mono">{created}</code><CopyButton text={created} /></div>}
        <div className="px-3"><ErrorNote error={err ?? keys.error} /></div>
        <Table head={["Name", "Prefix", "Scopes", "RPM", "TPM", "Budget/day", ""]}>
          {keys.data?.map((k) => (
            <tr key={k.key_id}>
              <td className="px-3 py-2">{k.name}</td><td className="px-3 py-2 font-mono text-xs">{k.prefix}…</td>
              <td className="px-3 py-2"><div className="flex gap-1">{k.scopes.map((s: string) => <Badge key={s}>{s}</Badge>)}</div></td>
              <td className="px-3 py-2 text-xs">{k.rpm}</td><td className="px-3 py-2 text-xs">{k.tpm}</td><td className="px-3 py-2 text-xs">{k.budget_usd_day ?? "—"}</td>
              <td className="px-3 py-2 text-right">{k.revoked ? <Badge tone="red">revoked</Badge> :
                <button className="text-zinc-400 hover:text-red-600" onClick={async () => { if (confirm("Revoke key?")) { await api.del(`/v1/admin/keys/${k.key_id}`); keys.reload(); } }}><Trash2 className="h-4 w-4" /></button>}</td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}
