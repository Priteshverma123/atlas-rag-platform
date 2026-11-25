"""End-to-end eval runner against a live Atlas stack (black-box, through the public API).

1. creates a fresh collection and ingests the corpus; waits until every document is READY
2. retrieval ablation: dense | sparse | hybrid | hybrid+rerank  → recall@k, hit@k, MRR, nDCG, latency
3. generation: /v1/query (non-stream) → evidence-in-answer, citation precision, support ratio,
   abstention accuracy on unanswerable questions, p50/p95 latency, cost
4. writes evals/reports/<run>.json + .md (ablation table ready for the README)
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from atlas.evals import metrics as M

ABLATIONS = [("dense", "dense", False), ("sparse (BM25)", "sparse", False), ("hybrid (RRF)", "hybrid", False),
             ("hybrid + rerank", "hybrid", True)]


@dataclass
class EvalItem:
    id: str
    question: str
    category: str
    evidence: list[str] = field(default_factory=list)
    reference: str = ""

    @property
    def answerable(self) -> bool:
        return self.category != "unanswerable"


def load_dataset(path: Path) -> list[EvalItem]:
    return [EvalItem(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


JUDGE_PROMPT = """You grade answers from a document Q&A system against a reference answer written by an expert.
Return JSON: {"score": 1 | 0.5 | 0, "reason": "<one sentence>"}.
1 = all key facts of the reference are present and nothing contradicts it; 0.5 = partially correct or missing a key
fact; 0 = wrong, contradicts the reference, or refuses although the reference has an answer.
Extra correct detail is fine. Judge facts, not wording."""


class Runner:
    def __init__(self, api: str, key: str, k: int = 5, concurrency: int = 4, judge_url: str | None = None,
                 judge_key: str | None = None) -> None:
        self.http = httpx.AsyncClient(base_url=api.rstrip("/"), timeout=180,
                                      headers={"Authorization": f"Bearer {key}", "X-Atlas-User": "eval-runner"})
        self.k = k
        self.sem = asyncio.Semaphore(concurrency)
        self.judge = httpx.AsyncClient(base_url=judge_url.rstrip("/"), timeout=120,
                                       headers={"Authorization": f"Bearer {judge_key}"}) if judge_url else None
        self.judge_live = True

    async def judge_answer(self, it: EvalItem, answer: str) -> tuple[float | None, str]:
        """Pinned LLM judge (gateway route `judge`). Returns (None, reason) when only the mock LLM is available."""
        if self.judge is None or not self.judge_live:
            return None, "judge disabled"
        r = await self.judge.post("/v1/chat/completions", json={
            "model": "judge", "max_tokens": 200, "messages": [
                {"role": "system", "content": JUDGE_PROMPT},
                {"role": "user", "content": f"Question: {it.question}\nReference: {it.reference}\nAnswer: {answer}"}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "grade", "schema": {
                "type": "object", "additionalProperties": False, "required": ["score", "reason"],
                "properties": {"score": {"type": "number"}, "reason": {"type": "string"}}}}}})
        data = r.json()
        if data.get("provider") == "mock":
            self.judge_live = False
            return None, "judge is the mock LLM (set ATLAS_LLM_LIVE=true)"
        try:
            g = json.loads(data["choices"][0]["message"]["content"])
            return max(0.0, min(1.0, float(g["score"]))), str(g.get("reason", ""))[:300]
        except (KeyError, ValueError, TypeError):
            return None, "unparseable judge output"

    async def _req(self, method: str, path: str, **kw: Any) -> Any:
        r = await self.http.request(method, path, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} → {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else None

    async def setup_collection(self, name: str, corpus: Path) -> str:
        coll = await self._req("POST", "/v1/collections", json={"name": name, "description": "eval corpus"})
        cid = coll["id"]
        docs = []
        for f in sorted(corpus.glob("*")):
            if f.suffix.lower() in (".md", ".txt"):
                d = await self._req("POST", f"/v1/collections/{cid}/documents/text",
                                    json={"title": f.stem.replace("_", " ").title(), "text": f.read_text("utf-8"),
                                          "source_id": f.name})
            else:
                with f.open("rb") as fh:
                    d = await self._req("POST", f"/v1/collections/{cid}/documents", files={"file": (f.name, fh)})
            docs.append(d["document_id"])
        deadline = time.time() + 600
        pending = set(docs)
        while pending and time.time() < deadline:
            for did in list(pending):
                st = await self._req("GET", f"/v1/documents/{did}")
                if st["state"] == "ready":
                    pending.discard(did)
                elif st["state"] == "failed":
                    raise RuntimeError(f"ingest failed for {did}: {st['error']}")
            await asyncio.sleep(1.0)
        if pending:
            raise TimeoutError(f"{len(pending)} documents not ready")
        return str(cid)

    async def retrieval(self, items: list[EvalItem], cid: str) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        answerable = [i for i in items if i.answerable]
        for label, mode, rerank in ABLATIONS:
            rows: list[dict[str, float]] = []

            async def one(it: EvalItem, mode: str = mode, rerank: bool = rerank) -> None:
                async with self.sem:
                    t0 = time.perf_counter()
                    res = await self._req("POST", "/v1/search", json={"query": it.question, "collection_ids": [cid],
                                                                      "top_k": 20, "mode": mode, "rerank": rerank})
                    ms = (time.perf_counter() - t0) * 1000
                texts = [r["text"] + " " + (r.get("context") or "") for r in res["results"]]
                rows.append({"recall": M.recall_at_k(texts, it.evidence, self.k),
                             "hit": M.hit_at_k(texts, it.evidence, self.k), "mrr": M.mrr(texts, it.evidence),
                             "ndcg": M.ndcg_at_k(texts, it.evidence, 10), "ms": ms,
                             "reranked": float(res.get("reranked", False))})

            await asyncio.gather(*(one(i) for i in answerable))
            out[label] = {f"recall@{self.k}": M.mean([r["recall"] for r in rows]),
                          f"hit@{self.k}": M.mean([r["hit"] for r in rows]), "mrr": M.mean([r["mrr"] for r in rows]),
                          "ndcg@10": M.mean([r["ndcg"] for r in rows]),
                          "p50_ms": M.percentile([r["ms"] for r in rows], 50),
                          "p95_ms": M.percentile([r["ms"] for r in rows], 95),
                          "rerank_rate": M.mean([r["reranked"] for r in rows])}
        return out

    async def generation(self, items: list[EvalItem], cid: str) -> tuple[dict[str, float], list[dict[str, Any]]]:
        rows: list[dict[str, Any]] = []

        async def one(it: EvalItem) -> None:
            async with self.sem:
                t0 = time.perf_counter()
                res = await self._req("POST", "/v1/query", json={"question": it.question, "collection_ids": [cid],
                                                                 "stream": False, "use_cache": False,
                                                                 "use_memory": False})
                ms = (time.perf_counter() - t0) * 1000
            answer = res.get("answer", "")
            abstained = bool(res.get("abstained")) or "don't have enough information" in answer.lower()
            cited = res.get("citations", [])
            retrieved = {r["n"]: r for r in res.get("retrieved", [])}
            ver = res.get("verification") or {}
            judged, why = (await self.judge_answer(it, answer)) if it.answerable else (None, "")
            rows.append({
                "id": it.id, "category": it.category, "ms": ms, "abstained": abstained, "answer": answer[:500],
                "judge_score": judged, "judge_reason": why, "question": it.question,
                "correct": M.evidence_in_answer(answer, it.evidence) if it.answerable else float(abstained),
                "f1": M.token_f1(answer, it.reference) if it.reference else None,
                "supported_ratio": ver.get("supported_ratio", 1.0), "cited": len(cited),
                "invalid_markers": len(ver.get("invalid_markers", [])),
                "cost": res.get("cost_usd") or 0.0, "retrieved": len(retrieved)})

        await asyncio.gather(*(one(i) for i in items))
        ans = [r for r in rows if r["category"] != "unanswerable"]
        una = [r for r in rows if r["category"] == "unanswerable"]
        judged = [r["judge_score"] for r in ans if r["judge_score"] is not None]
        by_cat: dict[str, list[float]] = {}
        for r in rows:
            v = r["judge_score"] if r["judge_score"] is not None else r["correct"]
            by_cat.setdefault(r["category"], []).append(float(v))
        summary = {
            "judge_accuracy": M.mean(judged) if judged else None,
            "judged": len(judged),
            "accuracy_by_category": {k: round(M.mean(v), 3) for k, v in sorted(by_cat.items())},
            "answer_correctness": M.mean([r["correct"] for r in ans]),
            "support_ratio": M.mean([r["supported_ratio"] for r in ans if not r["abstained"]]),
            "citation_rate": M.mean([float(r["cited"] > 0) for r in ans if not r["abstained"]]),
            "invalid_marker_rate": M.mean([float(r["invalid_markers"] > 0) for r in ans]),
            "false_abstention_rate": M.mean([float(r["abstained"]) for r in ans]),
            "abstention_accuracy": M.mean([float(r["abstained"]) for r in una]) if una else 1.0,
            "p50_ms": M.percentile([r["ms"] for r in rows], 50), "p95_ms": M.percentile([r["ms"] for r in rows], 95),
            "cost_usd_total": sum(r["cost"] for r in rows),
        }
        return summary, rows

    async def aclose(self) -> None:
        await self.http.aclose()
        if self.judge:
            await self.judge.aclose()


def git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,  # noqa: S607
                              check=False).stdout.strip()
    except OSError:
        return ""


def render_markdown(report: dict[str, Any]) -> str:
    lines = [f"# Eval report: {report['dataset']} @ {report['git_sha'] or 'local'}", "",
             f"{report['items']} questions · corpus `{report['corpus']}` · {report['created_at']}", "",
             "## Retrieval ablation", "", "| Config | recall@5 | hit@5 | MRR | nDCG@10 | p50 ms | p95 ms |",
             "|---|---|---|---|---|---|---|"]
    for name, m in report["retrieval"].items():
        lines.append(f"| {name} | {m['recall@5']:.3f} | {m['hit@5']:.3f} | {m['mrr']:.3f} | {m['ndcg@10']:.3f} | "
                     f"{m['p50_ms']:.0f} | {m['p95_ms']:.0f} |")
    g = report["generation"]
    lines += ["", "## Generation", "", "| Metric | Value |", "|---|---|"]
    lines += [f"| {k} | {v:.4f} |" if isinstance(v, float) else f"| {k} | {v} |" for k, v in g.items()
              if k != "accuracy_by_category"]
    if g.get("accuracy_by_category"):
        lines += ["", "## Accuracy by category", "", "| Category | Accuracy |", "|---|---|"]
        lines += [f"| {k} | {v:.3f} |" for k, v in g["accuracy_by_category"].items()]
    misses = [r for r in report.get("rows", []) if (r.get("judge_score") if r.get("judge_score") is not None
                                                    else r.get("correct", 1)) < 1]
    if misses:
        lines += ["", "## Misses (for error analysis)", "", "| id | category | score | question |", "|---|---|---|---|"]
        lines += [f"| {r['id']} | {r['category']} | {r.get('judge_score', r.get('correct'))} | "
                  f"{r.get('question', '')[:90]} |" for r in misses[:40]]
    return "\n".join(lines) + "\n"
