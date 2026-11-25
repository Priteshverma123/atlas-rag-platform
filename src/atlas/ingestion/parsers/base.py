"""Parser output model: a flat list of typed blocks with provenance (page + bbox).

Provenance survives all the way to the answer, so a citation can open the PDF at the exact
region. Bounding boxes are in PDF points, origin top-left: (x0, top, x1, bottom).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

BlockKind = Literal["heading", "paragraph", "table", "figure", "list", "code"]


@dataclass(slots=True)
class Block:
    kind: BlockKind
    text: str
    level: int = 0  # heading level (1 = top)
    page: int | None = None  # 1-based
    bbox: tuple[float, float, float, float] | None = None


@dataclass(slots=True)
class ParsedDocument:
    title: str
    blocks: list[Block] = field(default_factory=list)
    page_count: int | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    parser: str = "native"

    def plain_text(self) -> str:
        return "\n\n".join(b.text for b in self.blocks)
