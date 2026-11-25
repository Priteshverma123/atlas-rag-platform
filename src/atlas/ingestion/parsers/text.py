"""Markdown / plain-text and HTML parsing."""

from __future__ import annotations

import re

import trafilatura
from bs4 import BeautifulSoup

from atlas.ingestion.parsers.base import Block, ParsedDocument

_H = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^(```|~~~)")


def parse_markdown(text: str, filename: str = "") -> ParsedDocument:
    blocks: list[Block] = []
    buf: list[str] = []
    kind: str = "paragraph"

    def flush() -> None:
        nonlocal kind
        if buf and "".join(buf).strip():
            blocks.append(Block(kind, "\n".join(buf).strip()))  # type: ignore[arg-type]
        buf.clear()
        kind = "paragraph"

    in_code = False
    for line in text.splitlines():
        if _FENCE.match(line.strip()):
            if in_code:
                buf.append(line)
                flush()
                in_code = False
            else:
                flush()
                in_code, kind = True, "code"
                buf.append(line)
            continue
        if in_code:
            buf.append(line)
            continue
        if m := _H.match(line):
            flush()
            blocks.append(Block("heading", m.group(2).strip(), level=len(m.group(1))))
            continue
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        line_kind = "table" if stripped.startswith("|") else "list" if re.match(r"^([-*+]|\d+\.)\s", stripped) \
            else "paragraph"
        if buf and line_kind != kind:
            flush()
        kind = line_kind
        buf.append(line)
    flush()
    title = next((b.text for b in blocks if b.kind == "heading"), filename or "untitled")
    return ParsedDocument(title=title[:500], blocks=blocks, parser="markdown")


def parse_html(html: str, url: str = "") -> ParsedDocument:
    md = trafilatura.extract(html, output_format="markdown", include_tables=True, include_links=False,
                             include_comments=False, url=url or None) or ""
    soup_title = BeautifulSoup(html[:200_000], "html.parser").title
    doc = parse_markdown(md, url)
    if soup_title and soup_title.string:
        doc.title = soup_title.string.strip()[:500]
    doc.parser = "trafilatura"
    doc.metadata["url"] = url
    return doc
