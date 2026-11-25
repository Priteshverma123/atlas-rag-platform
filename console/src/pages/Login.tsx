import { ArrowRight, KeyRound, Sparkles } from "lucide-react";
import { useState } from "react";
import { api, type Session } from "../lib/api";
import { Button, ErrorNote, Input } from "../components/ui";

export default function Login({ onLogin }: { onLogin: (s: Session) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [pulseKey, setPulseKey] = useState("");

  const demo = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.raw<any>("/api", undefined, "/v1/demo/session", { method: "POST" });
      onLogin({ apiKey: r.api_key, pulseKey: r.pulse.key, pulseUrl: r.pulse.url, tenantId: r.tenant_id, collectionId: r.collection_id });
    } catch (e: any) {
      setError(e.status === 404 ? "Demo mode is disabled on this deployment. Sign in with an API key." : e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-full items-center justify-center bg-gradient-to-b from-zinc-50 to-zinc-100 p-6 dark:from-zinc-950 dark:to-zinc-900">
      <div className="w-full max-w-md rounded-xl border border-zinc-200 bg-white p-8 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-brand-500 text-white"><Sparkles className="h-5 w-5" /></div>
          <div>
            <h1 className="text-lg font-semibold">Atlas Console</h1>
            <p className="text-sm text-zinc-500">Knowledge platform · tracing · costs</p>
          </div>
        </div>
        <Button variant="primary" className="w-full" onClick={demo} loading={busy}>
          Start the demo <ArrowRight className="h-4 w-4" />
        </Button>
        <p className="mt-2 text-center text-xs text-zinc-500">Creates a demo workspace with sample company documents.</p>
        <div className="my-6 flex items-center gap-3 text-xs text-zinc-400"><div className="h-px flex-1 bg-zinc-200 dark:bg-zinc-800" />or use keys<div className="h-px flex-1 bg-zinc-200 dark:bg-zinc-800" /></div>
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); onLogin({ apiKey, pulseKey, pulseUrl: "/pulse" }); }}>
          <Input placeholder="Atlas API key (atl_…)" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
          <Input placeholder="Pulse project key (for tracing)" value={pulseKey} onChange={(e) => setPulseKey(e.target.value)} />
          <Button type="submit" className="w-full" disabled={!apiKey}><KeyRound className="h-4 w-4" />Sign in</Button>
        </form>
        <div className="mt-4"><ErrorNote error={error} /></div>
      </div>
    </div>
  );
}
