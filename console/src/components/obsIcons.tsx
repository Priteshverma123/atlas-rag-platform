import { ArrowLeftRight, Bot, Boxes, CircleDot, FlaskConical, Globe, Search, Shield, Sparkles, Wrench } from "lucide-react";

const MAP: Record<string, { icon: any; color: string; label: string }> = {
  span: { icon: ArrowLeftRight, color: "text-sky-500", label: "SPAN" },
  generation: { icon: Sparkles, color: "text-fuchsia-500", label: "GENERATION" },
  retriever: { icon: Search, color: "text-emerald-600", label: "RETRIEVER" },
  embedding: { icon: Boxes, color: "text-teal-500", label: "EMBEDDING" },
  tool: { icon: Wrench, color: "text-orange-500", label: "TOOL" },
  guardrail: { icon: Shield, color: "text-red-500", label: "GUARDRAIL" },
  agent: { icon: Bot, color: "text-violet-500", label: "AGENT" },
  evaluator: { icon: FlaskConical, color: "text-amber-500", label: "EVALUATOR" },
  event: { icon: CircleDot, color: "text-zinc-500", label: "EVENT" },
  http: { icon: Globe, color: "text-zinc-400", label: "HTTP" },
};

export function ObsIcon({ type, className = "h-4 w-4" }: { type: string; className?: string }) {
  const m = MAP[type] ?? MAP.span;
  const I = m.icon;
  return <I className={`${className} ${m.color} shrink-0`} />;
}

export const obsLabel = (type: string) => (MAP[type] ?? MAP.span).label;
export const OBS_TYPES = Object.keys(MAP).filter((t) => t !== "http");
