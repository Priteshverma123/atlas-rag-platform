"""Optional Docling backend (install the `docling` extra / build the worker with WITH_DOCLING=1).

Docling gives layout analysis, table structure recognition and OCR for scanned PDFs. Its
provenance boxes use a bottom-left origin; we convert to top-left to match pdfplumber.
"""

from __future__ import annotations

import io
from typing import Any

from atlas.ingestion.parsers.base import Block, ParsedDocument


def parse_with_docling(data: bytes, filename: str) -> ParsedDocument:
    from docling.datamodel.base_models import DocumentStream
    from docling.document_converter import DocumentConverter

    result = DocumentConverter().convert(DocumentStream(name=filename or "document.pdf", stream=io.BytesIO(data)))
    doc: Any = result.document
    blocks: list[Block] = []
    for item, level in doc.iterate_items():
        label = str(getattr(item, "label", "")).lower()
        page, bbox = None, None
        if getattr(item, "prov", None):
            prov = item.prov[0]
            page = prov.page_no
            try:
                height = doc.pages[page].size.height
                b = prov.bbox.to_top_left_origin(page_height=height)
                bbox = (b.l, b.t, b.r, b.b)
            except Exception:
                bbox = None
        if "table" in label:
            text, kind = item.export_to_markdown(doc=doc), "table"
        elif "picture" in label:
            text, kind = (item.caption_text(doc=doc) or "[figure]"), "figure"
        elif label in ("title", "section_header"):
            text, kind = item.text, "heading"
        elif "list" in label:
            text, kind = item.text, "list"
        elif "code" in label:
            text, kind = item.text, "code"
        else:
            text, kind = getattr(item, "text", ""), "paragraph"
        if text and text.strip():
            blocks.append(Block(kind, text.strip(), level=(1 if label == "title" else max(2, int(level) + 1))
                                if kind == "heading" else 0, page=page, bbox=bbox))  # type: ignore[arg-type]
    title = next((b.text for b in blocks if b.kind == "heading"), filename)
    return ParsedDocument(title=title[:500], blocks=blocks, page_count=len(getattr(doc, "pages", {}) or {}) or None,
                          parser="docling")
