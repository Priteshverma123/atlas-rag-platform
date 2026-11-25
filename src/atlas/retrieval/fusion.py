"""Rank fusion.

RRF (Cormack et al., 2009): score(d) = Σ_i w_i / (k + rank_i(d)). Rank-based, so it needs no
score calibration between BM25 and cosine similarity, which live on incomparable scales.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Fused:
    id: str
    score: float
    payload: dict[str, Any]
    ranks: dict[str, int] = field(default_factory=dict)  # stage name → 1-based rank
    raw: dict[str, float] = field(default_factory=dict)  # stage name → raw score


def rrf(rankings: dict[str, Sequence[tuple[str, float, dict[str, Any]]]], *, k: int = 60,
        weights: dict[str, float] | None = None) -> list[Fused]:
    """rankings: stage → [(id, raw_score, payload)] best-first."""
    weights = weights or {}
    out: dict[str, Fused] = {}
    for stage, items in rankings.items():
        w = weights.get(stage, 1.0)
        for rank, (pid, raw, payload) in enumerate(items, start=1):
            f = out.get(pid)
            if f is None:
                f = out[pid] = Fused(pid, 0.0, payload)
            f.score += w / (k + rank)
            f.ranks[stage] = rank
            f.raw[stage] = raw
    return sorted(out.values(), key=lambda f: -f.score)
