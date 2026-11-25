"""BM25 as a sparse vector, for Qdrant's sparse index with ``Modifier.IDF``.

Document side: each term gets the BM25 term-frequency saturation weight
    tf * (k1 + 1) / (tf + k1 * (1 - b + b * doc_len / avg_len))
Query side: each unique term gets 1.0. Qdrant multiplies by IDF computed over the live collection
(``modifier=IDF``), so the dot product equals the classic BM25 score and IDF stays correct as
documents are added or deleted, with no global statistics to maintain here.

Term ids are a stable 31-bit hash of the stemmed token, so no vocabulary file is needed and the
encoder is deterministic across processes and deployments.
"""

from __future__ import annotations

import re
import zlib
from collections import Counter
from dataclasses import dataclass

import snowballstemmer

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-\.]*[A-Za-z0-9]|[A-Za-z0-9]")
_STOP = frozenset("""a an and are as at be but by for if in into is it no not of on or such that the their then there
these they this to was will with what which who whom how when where why do does did can could should would
i you he she we us our your its from have has had been were""".split())


@dataclass(frozen=True, slots=True)
class SparseVec:
    indices: list[int]
    values: list[float]


class BM25Encoder:
    def __init__(self, k1: float = 1.2, b: float = 0.75, avg_len: float = 256.0, language: str = "english") -> None:
        self.k1, self.b, self.avg_len = k1, b, avg_len
        self._stem = snowballstemmer.stemmer(language)

    def tokens(self, text: str) -> list[str]:
        out = []
        for tok in _TOKEN.findall(text.lower()):
            if tok in _STOP:
                continue
            # Keep identifier-like tokens (error codes, SKUs, versions) verbatim: exact-match
            # recall on these is the main reason hybrid search beats dense-only.
            if any(c.isdigit() for c in tok) or "_" in tok or "-" in tok or "." in tok:
                out.append(tok)
            else:
                out.append(self._stem.stemWord(tok))
        return out

    @staticmethod
    def term_id(term: str) -> int:
        return zlib.crc32(term.encode("utf-8")) & 0x7FFFFFFF

    def encode_document(self, text: str) -> SparseVec:
        toks = self.tokens(text)
        if not toks:
            return SparseVec([], [])
        tf = Counter(toks)
        norm = self.k1 * (1 - self.b + self.b * len(toks) / self.avg_len)
        weights: dict[int, float] = {}
        for term, f in tf.items():
            tid = self.term_id(term)
            weights[tid] = weights.get(tid, 0.0) + f * (self.k1 + 1) / (f + norm)
        idx = sorted(weights)
        return SparseVec(idx, [weights[i] for i in idx])

    def encode_query(self, text: str) -> SparseVec:
        ids = sorted({self.term_id(t) for t in self.tokens(text)})
        return SparseVec(ids, [1.0] * len(ids))
