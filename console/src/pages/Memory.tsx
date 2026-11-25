import { Trash2 } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { fmtTime } from "../lib/format";
import { useFetch } from "../lib/hooks";
import { Badge, Button, Card, Empty, ErrorNote, PageHeader, Spinner, Table } from "../components/ui";

export default function Memory() {
  const [all, setAll] = useState(false);
  const facts = useFetch(() => api.get<any[]>(`/v1/memory?include_invalid=${all}`), [all], 10000);
  return (
    <div className="space-y-4">
      <PageHeader title="Memory" subtitle="What Atlas remembers about you: extracted in the background, merged nightly, never silently deleted. You can erase anything."
        actions={<>
          <label className="flex items-center gap-1.5 text-sm"><input type="checkbox" className="accent-brand-600" checked={all} onChange={(e) => setAll(e.target.checked)} />Show retired facts</label>
          <Button variant="danger" size="sm" onClick={async () => { if (confirm("Erase all memories?")) { await api.del("/v1/memory"); facts.reload(); } }}>Forget everything</Button>
        </>} />
      <Card pad={false}>
        <ErrorNote error={facts.error} />
        {facts.loading && !facts.data ? <Spinner /> : facts.data?.length ? (
          <Table head={["Fact", "Category", "Confidence", "Valid from", "Status", ""]}>
            {facts.data.map((f) => (
              <tr key={f.id}>
                <td className="px-3 py-2 text-sm">{f.fact}</td><td className="px-3 py-2"><Badge>{f.category}</Badge></td>
                <td className="px-3 py-2 font-mono text-xs">{f.confidence.toFixed(2)}</td><td className="px-3 py-2 font-mono text-xs">{fmtTime(f.valid_from)}</td>
                <td className="px-3 py-2">{f.invalid_at ? <Badge tone="amber">retired</Badge> : <Badge tone="green">active</Badge>}</td>
                <td className="px-3 py-2 text-right"><button className="text-zinc-400 hover:text-red-600" onClick={async () => { await api.del(`/v1/memory/${f.id}`); facts.reload(); }}><Trash2 className="h-4 w-4" /></button></td>
              </tr>
            ))}
          </Table>
        ) : <Empty title="No memories yet">Tell Atlas something about yourself in Chat, e.g. "I work in the EU finance team and prefer short answers". With a live LLM it's extracted in the background. (The mock LLM doesn't extract memories.)</Empty>}
      </Card>
    </div>
  );
}
