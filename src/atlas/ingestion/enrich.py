"""Contextual retrieval enrichment (Anthropic, 2024): a cheap model writes 1–2 sentences that
situate each chunk in its document; the context is prepended before embedding and BM25 indexing.

Cost controls (measured in bench/enrichment.md):
  * the document goes in the *system* prompt, identical for every chunk of the document, so the
    gateway's provider prompt-cache breakpoint makes each chunk call pay mostly cache-read price;
  * chunks whose section path already identifies them are skipped (heuristic);
  * bounded concurrency; enrichment runs on the `light` queue after the document is already
    searchable (the doc is READY before enrichment: latency to first answer is unaffected).
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from atlas.common.text import truncate_tokens
from atlas.llm.client import LLMClient

SYSTEM = """You situate excerpts within a document to improve search retrieval.
<document title="{title}">
{document}
</document>
For the excerpt the user gives you, reply with a short context (1-2 sentences, max 60 words) that
names the entity, period and topic the excerpt is about, so it can be understood on its own.
Reply with only the context."""

SUMMARY_SYSTEM = "Summarize documents for a search index. Be factual and specific; no preamble."


class DocSummary(BaseModel):
    summary: str = Field(description="5-8 sentence summary covering the document's main points")
    topics: list[str] = Field(description="up to 8 key topics or entities")


def needs_context(section_path: str, text: str) -> bool:
    # Very short or table-only chunks benefit most; deep section paths already carry context.
    return section_path.count(">") < 2 or len(text) < 400


async def contextualize(llm: LLMClient, tenant_id: str, title: str, document_text: str,
                        chunks: list[tuple[str, str, str]], concurrency: int = 8,
                        doc_token_budget: int = 12_000) -> dict[str, str]:
    """chunks: (chunk_id, section_path, text) → {chunk_id: context}. Mock provider output is dropped."""
    system = SYSTEM.format(title=title, document=truncate_tokens(document_text, doc_token_budget))
    sem = asyncio.Semaphore(concurrency)
    out: dict[str, str] = {}

    async def one(cid: str, section: str, text: str) -> None:
        if not needs_context(section, text):
            return
        async with sem:
            res = await llm.chat("fast", [{"role": "system", "content": system},
                                          {"role": "user", "content": f"<excerpt>\n{text}\n</excerpt>"}],
                                 tenant_id=tenant_id, max_tokens=120)
        if res.provider != "mock" and res.text.strip():
            out[cid] = res.text.strip()

    await asyncio.gather(*(one(*c) for c in chunks))
    return out


async def summarize(llm: LLMClient, tenant_id: str, title: str, document_text: str) -> DocSummary | None:
    res, meta = await llm.structured("fast", [
        {"role": "system", "content": SUMMARY_SYSTEM},
        {"role": "user", "content": f"<document title=\"{title}\">\n{truncate_tokens(document_text, 12_000)}\n"
                                    "</document>"}], DocSummary, tenant_id=tenant_id, max_tokens=600)
    return None if meta.provider == "mock" else res
