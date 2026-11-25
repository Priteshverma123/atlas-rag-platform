"""Citation extraction and verification.

Tier 1 (always, ~0 ms, free): every cited sentence is checked for lexical support against the
sources it cites; invalid markers and uncited factual sentences are flagged.
Tier 2 (sampled / evals): an LLM judge checks entailment for sentences tier 1 could not confirm.

Output feeds the UI ("weak evidence" badges), the ``unsupported_claim_rate`` metric, and the
feedback → eval-dataset loop.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import BaseModel

from atlas.llm.client import LLMClient
from atlas.retrieval.context import Source

_MARK = re.compile(r"\[(\d{1,3})\]")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9*\-#\"(])|\n+")
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "to", "in", "and", "or", "is", "are", "was", "were", "for", "on", "with", "by",
         "it", "this", "that", "as", "at", "from", "be", "has", "have", "its", "their", "which"}


def _terms(s: str) -> set[str]:
    return {w for w in _WORD.findall(s.lower()) if w not in _STOP and len(w) > 2}


@dataclass(slots=True)
class SentenceCheck:
    sentence: str
    markers: list[int]
    support: float
    status: str  # supported | weak | invalid_marker | uncited


@dataclass(slots=True)
class Verification:
    sentences: int = 0
    cited: int = 0
    supported: int = 0
    weak: list[SentenceCheck] = field(default_factory=list)
    uncited: list[SentenceCheck] = field(default_factory=list)
    invalid_markers: list[int] = field(default_factory=list)

    @property
    def supported_ratio(self) -> float:
        return self.supported / self.cited if self.cited else 1.0

    def to_dict(self) -> dict[str, Any]:
        return {"sentences": self.sentences, "cited": self.cited, "supported": self.supported,
                "supported_ratio": round(self.supported_ratio, 3), "invalid_markers": self.invalid_markers,
                "weak": [asdict(w) for w in self.weak], "uncited": [asdict(u) for u in self.uncited]}


def cited_numbers(answer: str) -> list[int]:
    seen: list[int] = []
    for m in _MARK.finditer(answer):
        n = int(m.group(1))
        if n not in seen:
            seen.append(n)
    return seen


def verify(answer: str, sources: list[Source], threshold: float = 0.5) -> Verification:
    by_n = {s.n: s for s in sources}
    src_terms = {s.n: _terms(s.text) for s in sources}
    v = Verification()
    for raw in _SENT.split(answer):
        sent = raw.strip()
        if len(sent) < 15 or sent.startswith(("|", "```")):
            continue
        v.sentences += 1
        markers = [int(x) for x in _MARK.findall(sent)]
        clean = _MARK.sub("", sent)
        terms = _terms(clean)
        if not markers:
            if len(terms) >= 6 and any(ch.isdigit() for ch in clean) or len(terms) >= 10:
                v.uncited.append(SentenceCheck(clean, [], 0.0, "uncited"))
            continue
        v.cited += 1
        bad = [n for n in markers if n not in by_n]
        if bad:
            v.invalid_markers.extend(b for b in bad if b not in v.invalid_markers)
        valid = [n for n in markers if n in by_n]
        support = max((len(terms & src_terms[n]) / max(1, len(terms)) for n in valid), default=0.0)
        if support >= threshold:
            v.supported += 1
        else:
            v.weak.append(SentenceCheck(clean, markers, round(support, 3), "invalid_marker" if not valid else "weak"))
    return v


class Entailment(BaseModel):
    supported: bool
    reason: str


async def llm_check(llm: LLMClient, tenant_id: str, sentence: str, sources: list[Source]) -> Entailment:
    """Tier-2 entailment check with the pinned judge route."""
    ctx = "\n\n".join(s.render() for s in sources)
    res, _ = await llm.structured("judge", [
        {"role": "system", "content": "Decide if the claim is fully supported by the sources. Be strict: "
                                      "partial or implied support is not support."},
        {"role": "user", "content": f"<sources>\n{ctx}\n</sources>\n<claim>{sentence}</claim>"}],
        Entailment, tenant_id=tenant_id, max_tokens=200)
    return res
