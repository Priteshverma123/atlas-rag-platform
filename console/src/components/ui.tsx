import clsx from "clsx";
import { Check, Copy, Loader2 } from "lucide-react";
import { type ReactNode, useState } from "react";
import { tryParseJson } from "../lib/format";

export const cx = clsx;

export function Card({ title, actions, children, className, pad = true }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; pad?: boolean;
}) {
  return (
    <section className={cx("rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-2 border-b border-zinc-100 px-4 py-3 dark:border-zinc-800">
          <h3 className="text-sm font-semibold text-zinc-800 dark:text-zinc-100">{title}</h3>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      )}
      {/* flex column body so cards placed in a fixed-height layout (chat, trace view) scroll inside */}
      <div className={cx("flex min-h-0 flex-1 flex-col", pad && "p-4")}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="text-xs font-medium text-zinc-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums text-zinc-900 dark:text-zinc-50">{value}</div>
      {hint && <div className="mt-1 text-xs text-zinc-500">{hint}</div>}
    </div>
  );
}

const BADGE: Record<string, string> = {
  gray: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  green: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  red: "bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300",
  amber: "bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  blue: "bg-sky-50 text-sky-700 dark:bg-sky-950 dark:text-sky-300",
  violet: "bg-violet-50 text-violet-700 dark:bg-violet-950 dark:text-violet-300",
  brand: "bg-brand-50 text-brand-700 dark:bg-brand-700/20 dark:text-brand-400",
};

export function Badge({ children, tone = "gray", mono }: { children: ReactNode; tone?: keyof typeof BADGE; mono?: boolean }) {
  return <span className={cx("inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium", BADGE[tone], mono && "font-mono")}>{children}</span>;
}

export function LevelBadge({ level }: { level?: string | null }) {
  if (!level || level === "DEFAULT") return null;
  return <Badge tone={level === "ERROR" ? "red" : level === "WARNING" ? "amber" : "gray"} mono>{level === "ERROR" ? "Error" : level === "WARNING" ? "Warning" : level}</Badge>;
}

export function StateBadge({ state }: { state: string }) {
  const tone = state === "ready" ? "green" : state === "failed" ? "red" : state === "received" ? "gray" : "blue";
  return <Badge tone={tone}>{state}</Badge>;
}

export function Button({ children, variant = "secondary", size = "md", loading, className, ...rest }: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger"; size?: "sm" | "md"; loading?: boolean;
}) {
  return (
    <button
      {...rest}
      disabled={rest.disabled || loading}
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "h-7 px-2.5 text-xs" : "h-9 px-3.5 text-sm",
        variant === "primary" && "bg-brand-600 text-white hover:bg-brand-700",
        variant === "secondary" && "border border-zinc-200 bg-white text-zinc-800 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:hover:bg-zinc-800",
        variant === "ghost" && "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800",
        variant === "danger" && "border border-red-200 bg-white text-red-700 hover:bg-red-50 dark:border-red-900 dark:bg-zinc-900 dark:text-red-400",
        className,
      )}
    >
      {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
      {children}
    </button>
  );
}

export const inputCls =
  "h-9 w-full rounded-md border border-zinc-200 bg-white px-3 text-sm text-zinc-900 outline-none placeholder:text-zinc-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100";

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cx(inputCls, props.className)} />;
}

export function Select({ options, ...props }: React.SelectHTMLAttributes<HTMLSelectElement> & { options: { value: string; label: string }[] }) {
  return (
    <select {...props} className={cx(inputCls, "pr-8", props.className)}>
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { value: T; label: ReactNode }[] }) {
  return (
    <div className="inline-flex rounded-md border border-zinc-200 bg-zinc-50 p-0.5 dark:border-zinc-800 dark:bg-zinc-900">
      {items.map((it) => (
        <button key={it.value} onClick={() => onChange(it.value)}
          className={cx("rounded px-3 py-1 text-xs font-medium",
            value === it.value ? "bg-white text-zinc-900 shadow-sm dark:bg-zinc-800 dark:text-zinc-50" : "text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200")}>
          {it.label}
        </button>
      ))}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return <div className="flex items-center gap-2 p-6 text-sm text-zinc-500"><Loader2 className="h-4 w-4 animate-spin" />{label ?? "Loading…"}</div>;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 p-10 text-center">
      <div className="text-sm font-semibold text-zinc-700 dark:text-zinc-200">{title}</div>
      {children && <div className="max-w-md text-xs text-zinc-500">{children}</div>}
    </div>
  );
}

export function ErrorNote({ error }: { error?: string | null }) {
  if (!error) return null;
  return <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">{error}</div>;
}

export function CopyButton({ text }: { text: string }) {
  const [ok, setOk] = useState(false);
  return (
    <button title="Copy" onClick={() => { navigator.clipboard?.writeText(text); setOk(true); setTimeout(() => setOk(false), 1200); }}
      className="rounded p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800">
      {ok ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  );
}

/** Formatted / raw view of an input or output payload (string, JSON, chat messages). */
export function Payload({ value, raw }: { value?: string | null; raw?: boolean }) {
  if (value == null || value === "") return <div className="text-sm text-zinc-400">—</div>;
  const parsed = tryParseJson(value);
  if (!raw && Array.isArray(parsed) && parsed.every((m: any) => m && typeof m === "object" && "role" in m)) {
    return (
      <div className="space-y-2">
        {(parsed as any[]).map((m, i) => (
          <div key={i} className="rounded-md border border-zinc-200 dark:border-zinc-800">
            <div className="border-b border-zinc-100 px-3 py-1 text-[11px] font-semibold uppercase tracking-wide text-zinc-500 dark:border-zinc-800">{m.role}</div>
            <pre className="whitespace-pre-wrap break-words px-3 py-2 font-sans text-sm text-zinc-800 dark:text-zinc-200">{typeof m.content === "string" ? m.content : JSON.stringify(m.content, null, 2)}</pre>
          </div>
        ))}
      </div>
    );
  }
  const text = typeof parsed === "string" ? parsed : JSON.stringify(parsed, null, 2);
  return (
    <pre className={cx("max-h-[460px] overflow-auto whitespace-pre-wrap break-words rounded-md bg-zinc-50 px-3 py-2 text-sm text-zinc-800 dark:bg-zinc-950 dark:text-zinc-200",
      typeof parsed === "string" && !raw ? "font-sans" : "font-mono text-xs")}>{text}</pre>
  );
}

export function KeyValue({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data ?? {});
  if (!entries.length) return <div className="text-sm text-zinc-400">—</div>;
  return (
    <div className="divide-y divide-zinc-100 rounded-md bg-zinc-50 dark:divide-zinc-800 dark:bg-zinc-950">
      {entries.map(([k, v]) => (
        <div key={k} className="grid grid-cols-[minmax(140px,260px)_1fr] gap-4 px-3 py-1.5 font-mono text-xs">
          <div className="truncate text-zinc-500" title={k}>{k}</div>
          <div className="break-all text-zinc-800 dark:text-zinc-200">{typeof v === "object" ? JSON.stringify(v) : String(v)}</div>
        </div>
      ))}
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">{title}</h1>
        {subtitle && <p className="mt-0.5 text-sm text-zinc-500">{subtitle}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-2">{actions}</div>
    </div>
  );
}

export function Table({ head, children, className }: { head: ReactNode[]; children: ReactNode; className?: string }) {
  return (
    <div className={cx("overflow-x-auto", className)}>
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-zinc-200 text-xs text-zinc-500 dark:border-zinc-800">
            {head.map((h, i) => <th key={i} className="whitespace-nowrap px-3 py-2 font-medium">{h}</th>)}
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">{children}</tbody>
      </table>
    </div>
  );
}
