"""Versioned price table with regex model matching and long-context pricing tiers.

Cost is computed once per call from *non-overlapping* usage buckets (see ``Usage``). Unknown
models cost 0 and are logged, never guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from atlas.common.logging import get_logger
from atlas.gateway.schemas import Usage

log = get_logger(__name__)


@dataclass(slots=True)
class Tier:
    above_input_tokens: int
    input: float
    output: float


@dataclass(slots=True)
class Price:
    provider: str
    pattern: re.Pattern[str]
    input: float  # USD per 1M tokens
    output: float
    cache_read: float | None = None
    cache_write: float | None = None
    tiers: list[Tier] = field(default_factory=list)

    @property
    def specificity(self) -> int:
        # More literal characters = more specific; picks "claude-haiku-5-5" over "claude-.*".
        return len(re.sub(r"[.*+?\\^$()\[\]{}|]", "", self.pattern.pattern))


class PriceTable:
    def __init__(self, version: str, prices: list[Price]) -> None:
        self.version = version
        self.prices = sorted(prices, key=lambda p: -p.specificity)
        self._warned: set[str] = set()

    @classmethod
    def load(cls, path: Path) -> PriceTable:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        prices = []
        for m in raw["models"]:
            prices.append(Price(
                provider=m["provider"], pattern=re.compile(m["match"]), input=float(m["input"]),
                output=float(m["output"]), cache_read=m.get("cache_read"), cache_write=m.get("cache_write"),
                tiers=[Tier(**t) for t in m.get("tiers", [])]))
        return cls(str(raw.get("version", "unknown")), prices)

    def find(self, provider: str, model: str) -> Price | None:
        for p in self.prices:
            if p.provider == provider and p.pattern.fullmatch(model):
                return p
        return None

    def cost(self, provider: str, model: str, usage: Usage) -> float:
        p = self.find(provider, model)
        if p is None:
            if f"{provider}/{model}" not in self._warned:
                self._warned.add(f"{provider}/{model}")
                log.warning("price_unknown", provider=provider, model=model)
            return 0.0
        inp, out = p.input, p.output
        for t in sorted(p.tiers, key=lambda t: t.above_input_tokens):
            if usage.total_input > t.above_input_tokens:
                inp, out = t.input, t.output
        cr = p.cache_read if p.cache_read is not None else inp
        cw = p.cache_write if p.cache_write is not None else inp
        return (usage.prompt_tokens * inp + usage.completion_tokens * out
                + usage.cache_read_tokens * cr + usage.cache_write_tokens * cw) / 1_000_000

    def uncached_cost(self, provider: str, model: str, usage: Usage) -> float:
        """What the call would have cost with no provider prompt cache (for 'savings' reporting)."""
        flat = Usage(prompt_tokens=usage.total_input, completion_tokens=usage.completion_tokens)
        return self.cost(provider, model, flat)
