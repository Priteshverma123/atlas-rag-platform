"""Retrieval + generation metrics.

Relevance is defined by *gold evidence strings* (short verbatim facts the answer depends on), not
chunk ids, so labels survive re-chunking, re-embedding and template changes: a chunk is relevant
if it contains any gold evidence string (case/whitespace-insensitive).
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence

_WS = re.compile(r"\s+")
_WORD = re.compile(r"[a-z0-9]+")


def norm(s: str) -> str:
    return _WS.sub(" ", s.lower()).strip()


def relevant(chunk_text: str, evidence: Sequence[str]) -> bool:
    t = norm(chunk_text)
    return any(norm(e) in t for e in evidence)


def recall_at_k(chunks: Sequence[str], evidence: Sequence[str], k: int) -> float:
    """Fraction of evidence strings found anywhere in the top-k chunks."""
    if not evidence:
        return 1.0
    top = norm(" \n ".join(chunks[:k]))
    return sum(norm(e) in top for e in evidence) / len(evidence)


def hit_at_k(chunks: Sequence[str], evidence: Sequence[str], k: int) -> float:
    return float(any(relevant(c, evidence) for c in chunks[:k])) if evidence else 1.0


def mrr(chunks: Sequence[str], evidence: Sequence[str]) -> float:
    for i, c in enumerate(chunks, start=1):
        if relevant(c, evidence):
            return 1.0 / i
    return 0.0


def ndcg_at_k(chunks: Sequence[str], evidence: Sequence[str], k: int) -> float:
    rels = [1.0 if relevant(c, evidence) else 0.0 for c in chunks[:k]]
    dcg = sum(r / math.log2(i + 2) for i, r in enumerate(rels))
    ideal = sorted(rels, reverse=True)
    n_rel = max(1, min(k, sum(rels) or 1))
    idcg = sum(1.0 / math.log2(i + 2) for i in range(int(n_rel))) if any(ideal) else 1.0
    return dcg / idcg if idcg else 0.0


def token_f1(pred: str, ref: str) -> float:
    p, r = _WORD.findall(pred.lower()), _WORD.findall(ref.lower())
    if not p or not r:
        return float(p == r)
    common: dict[str, int] = {}
    for w in p:
        if w in r:
            common[w] = min(p.count(w), r.count(w))
    same = sum(common.values())
    if same == 0:
        return 0.0
    prec, rec = same / len(p), same / len(r)
    return 2 * prec * rec / (prec + rec)


def evidence_in_answer(answer: str, evidence: Sequence[str]) -> float:
    """Answer correctness proxy: share of gold evidence facts restated in the answer."""
    if not evidence:
        return 1.0
    a = norm(answer)
    return sum(norm(e) in a for e in evidence) / len(evidence)


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def percentile(xs: Sequence[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    idx = min(len(s) - 1, max(0, math.ceil(p / 100 * len(s)) - 1))
    return s[idx]
