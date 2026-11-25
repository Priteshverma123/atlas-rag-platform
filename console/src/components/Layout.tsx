import {
  Activity, BookOpen, Brain, ChevronDown, Gauge, Home, LogOut, MessageSquare, Moon, Search, Settings,
  Sparkles, Star, Sun, Telescope, Users, Waypoints,
} from "lucide-react";
import { type ReactNode, useState } from "react";
import { NavLink } from "react-router-dom";
import { useApp } from "../App";
import { api } from "../lib/api";
import { RANGES } from "../lib/format";
import { useFetch } from "../lib/hooks";
import { Badge, cx } from "./ui";

const NAV: { section: string; items: { to: string; label: string; icon: ReactNode }[] }[] = [
  { section: "", items: [{ to: "/", label: "Home", icon: <Home className="h-4 w-4" /> }] },
  {
    section: "Knowledge",
    items: [
      { to: "/knowledge", label: "Collections", icon: <BookOpen className="h-4 w-4" /> },
      { to: "/chat", label: "Chat", icon: <MessageSquare className="h-4 w-4" /> },
      { to: "/search", label: "Search playground", icon: <Search className="h-4 w-4" /> },
      { to: "/research", label: "Deep research", icon: <Telescope className="h-4 w-4" /> },
      { to: "/memory", label: "Memory", icon: <Brain className="h-4 w-4" /> },
    ],
  },
  {
    section: "Observability",
    items: [
      { to: "/traces", label: "Tracing", icon: <Waypoints className="h-4 w-4" /> },
      { to: "/sessions", label: "Sessions", icon: <Activity className="h-4 w-4" /> },
      { to: "/users", label: "Users", icon: <Users className="h-4 w-4" /> },
      { to: "/scores", label: "Scores", icon: <Star className="h-4 w-4" /> },
    ],
  },
  {
    section: "Operations",
    items: [
      { to: "/usage", label: "Usage & cost", icon: <Gauge className="h-4 w-4" /> },
      { to: "/settings", label: "Settings", icon: <Settings className="h-4 w-4" /> },
    ],
  },
];

export function Layout({ children }: { children: ReactNode }) {
  const { rangeHours, setRangeHours, logout } = useApp();
  const info = useFetch(() => api.get<any>("/v1/system/info"), []);
  const [dark, setDark] = useState(document.documentElement.classList.contains("dark"));
  const toggleTheme = () => {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    try { localStorage.setItem("atlas.theme", next ? "dark" : "light"); } catch { /* ignore */ }
  };
  const range = RANGES.find((r) => r.hours === rangeHours) ?? RANGES[1];

  return (
    <div className="flex h-full">
      <aside className="flex w-60 shrink-0 flex-col border-r border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
        <div className="flex h-14 items-center gap-2 border-b border-zinc-200 px-4 dark:border-zinc-800">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-brand-500 text-white"><Sparkles className="h-4 w-4" /></div>
          <div className="text-sm font-semibold">Atlas Console</div>
        </div>
        <nav className="flex-1 overflow-y-auto px-2 py-3">
          {NAV.map((g) => (
            <div key={g.section} className="mb-3">
              {g.section && <div className="px-2 pb-1 pt-2 text-[11px] font-medium uppercase tracking-wide text-zinc-400">{g.section}</div>}
              {g.items.map((it) => (
                <NavLink key={it.to} to={it.to} end={it.to === "/"}
                  className={({ isActive }) => cx("flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm",
                    isActive ? "bg-zinc-100 font-medium text-zinc-900 dark:bg-zinc-800 dark:text-zinc-50"
                      : "text-zinc-600 hover:bg-zinc-50 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800/60")}>
                  {it.icon}{it.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="space-y-2 border-t border-zinc-200 p-3 text-xs dark:border-zinc-800">
          <div className="flex items-center justify-between">
            <span className="text-zinc-500">LLM mode</span>
            {info.data ? (info.data.llm_live ? <Badge tone="green">live</Badge> : <Badge tone="amber">mock</Badge>) : <Badge>…</Badge>}
          </div>
          <div className="flex items-center justify-between">
            <button onClick={toggleTheme} className="flex items-center gap-1.5 text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200">
              {dark ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}{dark ? "Light" : "Dark"}
            </button>
            <button onClick={logout} className="flex items-center gap-1.5 text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200">
              <LogOut className="h-3.5 w-3.5" />Sign out
            </button>
          </div>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-zinc-200 bg-white px-6 dark:border-zinc-800 dark:bg-zinc-900">
          <div className="text-sm text-zinc-500">tenant <span className="font-mono text-zinc-800 dark:text-zinc-200">demo</span></div>
          <div className="relative">
            <label className="flex items-center gap-2 rounded-md border border-zinc-200 px-2 py-1 text-xs dark:border-zinc-700">
              <span className="rounded bg-zinc-100 px-1.5 py-0.5 font-mono dark:bg-zinc-800">{range.short}</span>
              <select value={rangeHours} onChange={(e) => setRangeHours(Number(e.target.value))}
                className="appearance-none bg-transparent pr-5 outline-none">
                {RANGES.map((r) => <option key={r.hours} value={r.hours}>{r.label}</option>)}
              </select>
              <ChevronDown className="pointer-events-none absolute right-2 h-3.5 w-3.5 text-zinc-400" />
            </label>
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto p-6">{children}</main>
      </div>
    </div>
  );
}
