"""DOCX and PPTX parsing that preserves reading order, heading levels and tables."""

from __future__ import annotations

import io
import re

import docx
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph
from pptx import Presentation

from atlas.ingestion.parsers.base import Block, ParsedDocument
from atlas.ingestion.parsers.pdf import _table_md

_HEADING = re.compile(r"heading\s*(\d)", re.I)


def parse_docx(data: bytes, filename: str = "") -> ParsedDocument:
    d = docx.Document(io.BytesIO(data))
    blocks: list[Block] = []
    for child in d.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, d)
            text = p.text.strip()
            if not text:
                continue
            style = (p.style.name if p.style is not None else "") or ""
            if style.lower() == "title":
                blocks.append(Block("heading", text, level=1))
            elif m := _HEADING.search(style):
                blocks.append(Block("heading", text, level=min(int(m.group(1)) + 1, 6)))
            elif "list" in style.lower():
                blocks.append(Block("list", text))
            else:
                blocks.append(Block("paragraph", text))
        elif tag == "tbl":
            t = DocxTable(child, d)
            md = _table_md([[c.text for c in row.cells] for row in t.rows])
            if md:
                blocks.append(Block("table", md))
    title = (d.core_properties.title or next((b.text for b in blocks if b.kind == "heading"), filename))[:500]
    return ParsedDocument(title=title, blocks=blocks, parser="python-docx")


def parse_pptx(data: bytes, filename: str = "") -> ParsedDocument:
    prs = Presentation(io.BytesIO(data))
    blocks: list[Block] = []
    for n, slide in enumerate(prs.slides, start=1):
        title_shape = slide.shapes.title
        title = title_shape.text.strip() if title_shape is not None and title_shape.has_text_frame else ""
        blocks.append(Block("heading", f"Slide {n}: {title}" if title else f"Slide {n}", level=2, page=n))
        for shape in slide.shapes:
            if shape == title_shape:
                continue
            if getattr(shape, "has_table", False) and shape.has_table:
                md = _table_md([[c.text for c in row.cells] for row in shape.table.rows])
                if md:
                    blocks.append(Block("table", md, page=n))
            elif shape.has_text_frame:
                text = "\n".join(p.text for p in shape.text_frame.paragraphs if p.text.strip())
                if text.strip():
                    blocks.append(Block("paragraph", text.strip(), page=n))
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                blocks.append(Block("paragraph", f"Speaker notes: {notes}", page=n))
    return ParsedDocument(title=filename, blocks=blocks, page_count=len(prs.slides), parser="python-pptx")
