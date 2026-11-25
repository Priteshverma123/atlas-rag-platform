import { Link } from "react-router-dom";
import { useApp } from "../App";
import { pulse } from "../lib/api";
import { fmtAgo, fmtNum, fmtUsd, rangeParams } from "../lib/format";
import { useFetch } from "../lib/hooks";
import { Card, Empty, ErrorNote, PageHeader, Spinner, Table } from "../components/ui";

export default function Users() {
  const { rangeHours } = useApp();
  const u = useFetch(() => pulse.get<any>(`/api/users?${rangeParams(Math.max(rangeHours, 24 * 7))}`), [rangeHours], 15000);
  return (
    <div>
      <PageHeader title="Users" subtitle="Who is using Atlas, how much, and what it costs per user." />
      <Card pad={false}>
        <ErrorNote error={u.error} />
        {u.loading && !u.data ? <Spinner /> : (
          <Table head={["User", "Traces", "Sessions", "Tokens", "Cost", "Errors", "First seen", "Last seen"]}>
            {u.data?.data.map((r: any) => (
              <tr key={r.user_id}>
                <td className="px-3 py-2 font-mono text-xs"><Link to={`/traces?user=${encodeURIComponent(r.user_id)}`}>{r.user_id}</Link></td>
                <td className="px-3 py-2">{r.traces}</td><td className="px-3 py-2">{r.sessions}</td>
                <td className="px-3 py-2 font-mono text-xs">{fmtNum(r.tokens)}</td><td className="px-3 py-2 font-mono text-xs">{fmtUsd(r.cost)}</td>
                <td className="px-3 py-2">{r.errors}</td><td className="px-3 py-2 text-xs text-zinc-500">{fmtAgo(r.first_seen)}</td><td className="px-3 py-2 text-xs text-zinc-500">{fmtAgo(r.last_seen)}</td>
              </tr>
            ))}
          </Table>
        )}
        {u.data && !u.data.data.length && <Empty title="No users yet" />}
      </Card>
    </div>
  );
}
