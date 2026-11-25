"""Ingestion throughput + retrieval-latency-at-scale benchmark (black box, through the public API).

    uv run python bench/ingest_bench.py --key atl_... --docs 1000 [--batch 100] [--queries 50]

1. generates N realistic synthetic policy documents (unique content → real parsing/embedding work)
2. uploads them with the bulk endpoint, concurrent batches
3. polls /stats until every document is READY → docs/min, chunks/s, p50/p95 time-to-ready
4. runs search + query load against the now-larger index → p50/p95 latency at that scale
5. writes bench/results/ingest_<N>.json and prints a Markdown summary

Scale out ingestion with:  docker compose up -d --scale worker-ingest=4
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import time
from pathlib import Path

import httpx

TOPICS = ["data retention", "access control", "incident response", "vendor management", "backup and recovery",
          "encryption", "change management", "remote work", "physical security", "logging and monitoring",
          "patch management", "business continuity", "fraud monitoring", "customer authentication", "audit"]
TEAMS = ["Finance", "Engineering", "Operations", "Risk", "Legal", "Security", "HR", "Treasury", "Payments"]


def make_doc(i: int, rng: random.Random) -> tuple[str, str]:
    topic, team = rng.choice(TOPICS), rng.choice(TEAMS)
    code = f"POL-{i:06d}"
    days, hours, pct = rng.randint(2, 365), rng.randint(1, 72), rng.randint(5, 99)
    sections = []
    for s in range(rng.randint(3, 6)):
        sentences = [
            f"Policy {code} governs {topic} for the {team} team and applies to all regions.",
            f"Records under {code} must be retained for {days} days unless a legal hold applies.",
            f"Incidents related to {topic} must be escalated within {hours} hours to the {team} lead.",
            f"At least {pct} percent of critical systems in scope must pass the quarterly {topic} review.",
            f"Exceptions to {code} require written approval from the {team} director and expire after 90 days.",
        ]
        rng.shuffle(sentences)
        body = " ".join(sentences * rng.randint(2, 4))
        sections.append(f"## Section {s + 1}: {topic.title()} requirement {s + 1}\n\n{body}\n")
    return f"{code}.md", f"# {code}: {team} {topic} policy\n\n" + "\n".join(sections)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--key", required=True)
    ap.add_argument("--docs", type=int, default=200)
    ap.add_argument("--batch", type=int, default=100)
    ap.add_argument("--parallel-uploads", type=int, default=4)
    ap.add_argument("--queries", type=int, default=50)
    ap.add_argument("--timeout-min", type=int, default=120)
    a = ap.parse_args()
    rng = random.Random(42)
    http = httpx.AsyncClient(base_url=a.api, timeout=600, headers={"Authorization": f"Bearer {a.key}"})
    coll = (await http.post("/v1/collections", json={"name": f"bench-{a.docs}-{int(time.time())}"})).json()
    cid = coll["id"]
    docs = [make_doc(i, rng) for i in range(a.docs)]

    t0 = time.perf_counter()
    sem = asyncio.Semaphore(a.parallel_uploads)

    async def upload(chunk: list[tuple[str, str]]) -> None:
        async with sem:
            files = [("files", (name, body.encode(), "text/markdown")) for name, body in chunk]
            r = await http.post(f"/v1/collections/{cid}/documents/batch", files=files)
            r.raise_for_status()

    await asyncio.gather(*(upload(docs[i:i + a.batch]) for i in range(0, len(docs), a.batch)))
    upload_s = time.perf_counter() - t0
    print(f"uploaded {a.docs} docs in {upload_s:.1f}s ({60 * a.docs / upload_s:.0f} docs/min accepted)")

    deadline = time.time() + a.timeout_min * 60
    stats: dict = {}
    while time.time() < deadline:
        stats = (await http.get(f"/v1/collections/{cid}/stats")).json()
        ready = stats["by_state"].get("ready", 0)
        failed = stats["by_state"].get("failed", 0)
        print(f"  {time.perf_counter() - t0:6.0f}s ready={ready} failed={failed} chunks={stats['chunks']}", flush=True)
        if ready + failed >= a.docs:
            break
        await asyncio.sleep(5)
    total_s = time.perf_counter() - t0

    # retrieval latency at this index size
    lat_search, lat_query = [], []
    for _ in range(a.queries):
        name, body = rng.choice(docs)
        code = name.removesuffix(".md")
        q = f"How long must records under {code} be retained?"
        t1 = time.perf_counter()
        await http.post("/v1/search", json={"query": q, "collection_ids": [cid], "top_k": 5})
        lat_search.append((time.perf_counter() - t1) * 1000)
    for _ in range(max(5, a.queries // 5)):
        name, _ = rng.choice(docs)
        t1 = time.perf_counter()
        await http.post("/v1/query", json={"question": f"What is the escalation deadline in {name[:-3]}?",
                                           "collection_ids": [cid], "stream": False, "use_cache": False})
        lat_query.append((time.perf_counter() - t1) * 1000)

    def pct(xs: list[float], p: float) -> float:
        return sorted(xs)[min(len(xs) - 1, int(p / 100 * len(xs)))] if xs else 0.0

    result = {"docs": a.docs, "chunks": stats.get("chunks"), "by_state": stats.get("by_state"),
              "wall_clock_s": round(total_s, 1), "docs_per_min": round(60 * a.docs / total_s, 1),
              "chunks_per_s": round((stats.get("chunks") or 0) / total_s, 1),
              "time_to_ready_s": stats.get("time_to_ready_s"),
              "search_ms": {"p50": round(statistics.median(lat_search), 1), "p95": round(pct(lat_search, 95), 1)},
              "query_ms": {"p50": round(statistics.median(lat_query), 1), "p95": round(pct(lat_query, 95), 1)}}
    out = Path(__file__).parent / "results"
    out.mkdir(exist_ok=True)
    (out / f"ingest_{a.docs}.json").write_text(json.dumps(result, indent=2))
    print("\n| docs | chunks | wall | docs/min | chunks/s | ready p50/p95 | search p50/p95 | query p50/p95 |")
    print("|---|---|---|---|---|---|---|---|")
    ttr = result["time_to_ready_s"] or {}
    print(f"| {a.docs} | {result['chunks']} | {result['wall_clock_s']}s | {result['docs_per_min']} | "
          f"{result['chunks_per_s']} | {ttr.get('p50') or 0:.1f}s / {ttr.get('p95') or 0:.1f}s | "
          f"{result['search_ms']['p50']} / {result['search_ms']['p95']} ms | "
          f"{result['query_ms']['p50']} / {result['query_ms']['p95']} ms |")
    await http.aclose()


if __name__ == "__main__":
    asyncio.run(main())
