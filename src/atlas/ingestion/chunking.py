"""Structure-aware chunking with parent-child ("small-to-big") support.

Rules:
  * a heading always starts a new chunk and updates the section path ("Report > Risks > FX");
  * tables, figures and code blocks are atomic chunks (oversized tables split by rows, header
    repeated);
  * paragraphs pack up to ``target_tokens``; oversized paragraphs split on sentences;
  * overlap is carried only *within* a section (overlap across sections adds noise);
  * consecutive chunks inside one top-level section are grouped into a parent (≤ parent_tokens)
    used at generation time when the context budget allows ("small-to-big").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from atlas.common.text import count_tokens, sha256_text
from atlas.ingestion.parsers.base import Block, ParsedDocument

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


@dataclass(slots=True)
class ChunkParams:
    target_tokens: int = 400
    max_tokens: int = 650
    overlap_tokens: int = 50
    min_tokens: int = 40
    parent_tokens: int = 1500


@dataclass(slots=True)
class ChunkDraft:
    ordinal: int
    kind: str
    text: str
    section_path: str
    page_start: int | None
    page_end: int | None
    bboxes: list[dict[str, Any]] = field(default_factory=list)
    token_count: int = 0
    text_hash: str = ""
    parent_ordinal: int | None = None

    def embed_text(self, title: str, context: str | None = None) -> str:
        """What gets embedded/BM25-indexed: title + section path (+ LLM context) + text."""
        head = " > ".join(p for p in (title, self.section_path) if p)
        parts = [head] if head else []
        if context:
            parts.append(context)
        parts.append(self.text)
        return "\n".join(parts)


@lru_cache
def load_templates(path: str) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def params_for(config_path: Path, mime: str, template: str | None) -> tuple[str, ChunkParams]:
    cfg = load_templates(str(config_path))
    name = template if template and template != "auto" else cfg["auto"].get(mime, "default")
    return name, ChunkParams(**cfg["templates"].get(name, cfg["templates"]["default"]))


@dataclass
class _Acc:
    blocks: list[Block] = field(default_factory=list)
    tokens: int = 0


def _split_sentences(text: str, max_tokens: int) -> list[str]:
    out, cur, cur_t = [], [], 0
    for s in _SENT.split(text):
        t = count_tokens(s)
        if cur and cur_t + t > max_tokens:
            out.append(" ".join(cur))
            cur, cur_t = [], 0
        cur.append(s)
        cur_t += t
    if cur:
        out.append(" ".join(cur))
    return out


def _split_table(md: str, max_tokens: int) -> list[str]:
    lines = md.splitlines()
    if len(lines) <= 3 or count_tokens(md) <= max_tokens:
        return [md]
    header, rows = lines[:2], lines[2:]
    parts, cur = [], list(header)
    for r in rows:
        if count_tokens("\n".join([*cur, r])) > max_tokens and len(cur) > 2:
            parts.append("\n".join(cur))
            cur = list(header)
        cur.append(r)
    parts.append("\n".join(cur))
    return parts


def chunk_document(doc: ParsedDocument, p: ChunkParams) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []
    stack: list[tuple[int, str]] = []  # (level, heading text)
    acc = _Acc()

    def section() -> str:
        return " > ".join(t for _, t in stack)

    def emit(kind: str, text: str, blocks: list[Block]) -> None:
        pages = [b.page for b in blocks if b.page is not None]
        chunks.append(ChunkDraft(
            ordinal=len(chunks), kind=kind, text=text.strip(), section_path=section(),
            page_start=min(pages) if pages else None, page_end=max(pages) if pages else None,
            bboxes=[{"page": b.page, "bbox": list(b.bbox)} for b in blocks if b.page is not None and b.bbox],
            token_count=count_tokens(text), text_hash=sha256_text(text.strip())))

    def flush(carry_overlap: bool = False) -> None:
        if not acc.blocks:
            return
        text = "\n\n".join(b.text for b in acc.blocks)
        emit("text", text, acc.blocks)
        tail: list[Block] = []
        if carry_overlap and p.overlap_tokens:
            sents = _SENT.split(acc.blocks[-1].text)
            keep: list[str] = []
            while sents and count_tokens(" ".join([sents[-1], *keep])) <= p.overlap_tokens:
                keep.insert(0, sents.pop())
            if keep:
                last = acc.blocks[-1]
                tail = [Block("paragraph", " ".join(keep), page=last.page, bbox=last.bbox)]
        acc.blocks, acc.tokens = tail, sum(count_tokens(b.text) for b in tail)

    for b in doc.blocks:
        if b.kind == "heading":
            flush()
            lvl = b.level or 1
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            stack.append((lvl, b.text[:120]))
            continue
        if b.kind in ("table", "figure", "code"):
            flush()
            pieces = _split_table(b.text, p.max_tokens) if b.kind == "table" else [b.text]
            for piece in pieces:
                emit(b.kind, piece, [b])
            continue
        t = count_tokens(b.text)
        if t > p.max_tokens:
            flush()
            for piece in _split_sentences(b.text, p.target_tokens):
                emit("text", piece, [b])
            continue
        if acc.tokens + t > p.target_tokens and acc.blocks:
            flush(carry_overlap=True)
        acc.blocks.append(b)
        acc.tokens += t
    flush()

    # merge tiny text chunks into their successor within the same section
    merged: list[ChunkDraft] = []
    for c in chunks:
        if (merged and merged[-1].kind == "text" and c.kind == "text" and merged[-1].section_path == c.section_path
                and merged[-1].token_count < p.min_tokens):
            prev = merged.pop()
            c = ChunkDraft(0, "text", prev.text + "\n\n" + c.text, c.section_path,
                           prev.page_start if prev.page_start is not None else c.page_start,
                           c.page_end if c.page_end is not None else prev.page_end, prev.bboxes + c.bboxes)
            c.token_count, c.text_hash = count_tokens(c.text), sha256_text(c.text)
        merged.append(c)
    for i, c in enumerate(merged):
        c.ordinal = i

    # parents: group consecutive chunks sharing the top-level section
    parents: list[ChunkDraft] = []
    group: list[ChunkDraft] = []

    def close_group() -> None:
        if len(group) > 1:
            text = "\n\n".join(g.text for g in group)
            pages = [g.page_start for g in group if g.page_start] + [g.page_end for g in group if g.page_end]
            parent = ChunkDraft(ordinal=len(merged) + len(parents), kind="parent", text=text,
                                section_path=group[0].section_path.split(" > ")[0],
                                page_start=min(pages) if pages else None, page_end=max(pages) if pages else None,
                                token_count=count_tokens(text), text_hash=sha256_text(text))
            for g in group:
                g.parent_ordinal = parent.ordinal
            parents.append(parent)
        group.clear()

    for c in merged:
        top = c.section_path.split(" > ")[0]
        gtok = sum(g.token_count for g in group)
        if group and (group[0].section_path.split(" > ")[0] != top or gtok + c.token_count > p.parent_tokens):
            close_group()
        group.append(c)
    close_group()
    return merged + parents
