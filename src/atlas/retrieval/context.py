"""Context assembly under a token budget.

dedupe identical texts → (optional) small-to-big parent expansion → merge adjacent chunks of the
same document → number sources → stop at the budget. "More context is not better": precision
and ordering beat volume, so the budget is tuned with evals, not maximised.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Any

from atlas.common.text import count_tokens
from atlas.retrieval.fusion import Fused


@dataclass(slots=True)
class Source:
    n: int  # citation number shown to the model: [n]
    chunk_ids: list[str]
    document_id: str
    title: str
    text: str
    section_path: str
    page_start: int | None
    page_end: int | None
    score: float
    source_uri: str = ""
    scores: dict[str, float] = field(default_factory=dict)

    def render(self) -> str:
        # attribute values are escaped: section paths contain ">" ("Policy > Travel"), which otherwise
        # terminates the tag early for any parser (and confuses models about where content starts)
        esc = lambda v: html.escape(str(v), quote=True)  # noqa: E731
        pages = f' pages="{self.page_start}-{self.page_end}"' if self.page_start else ""
        sec = f' section="{esc(self.section_path)}"' if self.section_path else ""
        return f'<source id="{self.n}" title="{esc(self.title)}"{sec}{pages}>\n{self.text}\n</source>'

    def citation(self) -> dict[str, Any]:
        return {"n": self.n, "document_id": self.document_id, "title": self.title, "chunk_ids": self.chunk_ids,
                "section_path": self.section_path, "page_start": self.page_start, "page_end": self.page_end,
                "source_uri": self.source_uri, "score": round(self.score, 4)}


def build_context(items: list[Fused], budget_tokens: int, parents: dict[str, str] | None = None,
                  max_sources: int = 8) -> list[Source]:
    parents = parents or {}
    seen_text: set[str] = set()
    used_parents: set[str] = set()
    sources: list[Source] = []
    spent = 0
    for f in items:
        p = f.payload
        h = p.get("text_hash") or p.get("text", "")[:200]
        if h in seen_text:
            continue
        seen_text.add(h)
        text, chunk_ids = p.get("text", ""), [f.id]
        pid = p.get("parent_id")
        if pid and pid in parents and pid not in used_parents:
            parent_text = parents[pid]
            if spent + count_tokens(parent_text) <= budget_tokens:  # small-to-big only when it fits
                text = parent_text
                used_parents.add(pid)
        elif pid and pid in used_parents:
            continue  # already covered by its expanded parent
        # merge with an adjacent chunk of the same doc already selected
        merged = False
        for s in sources:
            if s.document_id == p.get("document_id") and s.section_path == p.get("section_path") and \
                    count_tokens(s.text) + count_tokens(text) < budget_tokens // 3:
                s.text += "\n\n" + text
                s.chunk_ids.append(f.id)
                merged = True
                break
        cost = count_tokens(text)
        if spent + cost > budget_tokens:
            break
        spent += cost
        if merged:
            continue
        sources.append(Source(n=len(sources) + 1, chunk_ids=chunk_ids, document_id=p.get("document_id", ""),
                              title=p.get("title", ""), text=text, section_path=p.get("section_path", ""),
                              page_start=p.get("page_start"), page_end=p.get("page_end"), score=f.score,
                              source_uri=p.get("source_uri", ""), scores=dict(f.raw)))
        if len(sources) >= max_sources:
            break
    return sources
