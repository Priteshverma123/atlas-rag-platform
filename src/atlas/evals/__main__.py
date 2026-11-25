"""CLI:  atlas-eval run  --dataset evals/datasets/smoke.jsonl --corpus evals/corpus --api URL --key KEY
       atlas-eval gate --report evals/reports/latest.json --baseline evals/baselines/smoke.json

The gate fails CI when quality regresses beyond tolerance (defaults: −0.03 retrieval, −0.02
generation) or latency regresses > 25%. Improvements are reported, never punished.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from atlas.evals.runner import Runner, git_sha, load_dataset, render_markdown

GATED = {  # metric path → (direction, tolerance)
    ("retrieval", "hybrid + rerank", "recall@5"): ("higher", 0.03),
    ("retrieval", "hybrid + rerank", "ndcg@10"): ("higher", 0.03),
    ("generation", "answer_correctness"): ("higher", 0.02),
    ("generation", "judge_accuracy"): ("higher", 0.03),
    ("generation", "support_ratio"): ("higher", 0.02),
    ("generation", "abstention_accuracy"): ("higher", 0.05),
    ("generation", "invalid_marker_rate"): ("lower", 0.02),
    ("generation", "p95_ms"): ("lower_rel", 0.25),
}


async def run(args: argparse.Namespace) -> int:
    items = load_dataset(Path(args.dataset))
    r = Runner(args.api, args.key, k=5, judge_url=args.judge_url, judge_key=args.judge_key)
    try:
        cid = await r.setup_collection(f"eval-{Path(args.dataset).stem}-{uuid.uuid4().hex[:6]}", Path(args.corpus))
        retrieval = await r.retrieval(items, cid)
        generation, rows = await r.generation(items, cid)
    finally:
        await r.aclose()
    report = {"dataset": Path(args.dataset).name, "corpus": args.corpus, "items": len(items), "git_sha": git_sha(),
              "created_at": datetime.now(UTC).isoformat(timespec="seconds"), "retrieval": retrieval,
              "generation": generation, "rows": rows}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(args.dataset).stem
    for name in ("latest", stem):
        (out / f"{name}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (out / f"{name}.md").write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))
    return 0


def _get(d: dict, path: tuple[str, ...]) -> float | None:  # type: ignore[type-arg]
    for p in path:
        if not isinstance(d, dict) or p not in d:
            return None
        d = d[p]
    return float(d)  # type: ignore[arg-type]


def gate(args: argparse.Namespace) -> int:
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    failures, lines = [], ["| metric | baseline | current | Δ | status |", "|---|---|---|---|---|"]
    for path, (direction, tol) in GATED.items():
        cur, ref = _get(report, path), _get(base, path)
        if cur is None or ref is None:
            continue
        delta = cur - ref
        if direction == "higher":
            ok = delta >= -tol
        elif direction == "lower":
            ok = delta <= tol
        else:  # relative latency
            ok = ref == 0 or cur <= ref * (1 + tol)
        lines.append(f"| {'.'.join(path[1:])} | {ref:.4f} | {cur:.4f} | {delta:+.4f} | {'✅' if ok else '❌'} |")
        if not ok:
            failures.append(".".join(path))
    print("\n".join(lines))
    if failures:
        print(f"\nQuality gate FAILED: {', '.join(failures)}", file=sys.stderr)
        return 1
    print("\nQuality gate passed.")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(prog="atlas-eval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dataset", required=True)
    r.add_argument("--corpus", required=True)
    r.add_argument("--api", default="http://localhost:8000")
    r.add_argument("--key", required=True)
    r.add_argument("--out", default="evals/reports")
    r.add_argument("--judge-url", default=None, help="gateway URL for the LLM judge, e.g. http://localhost:8090")
    r.add_argument("--judge-key", default=None, help="gateway internal key (ATLAS_GATEWAY_INTERNAL_KEY)")
    g = sub.add_parser("gate")
    g.add_argument("--report", required=True)
    g.add_argument("--baseline", required=True)
    args = ap.parse_args()
    sys.exit(asyncio.run(run(args)) if args.cmd == "run" else gate(args))


if __name__ == "__main__":
    main()
