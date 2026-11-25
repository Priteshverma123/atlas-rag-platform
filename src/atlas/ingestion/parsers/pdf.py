"""Layout-aware PDF parsing with pdfplumber (MIT).

* headings from font size **or** bold weight (regulatory/legal PDFs mark headings with bold body-size
  text), with levels inferred from numbering ("Chapter IV" → 1, "B." → 2, "Z.1" → 3);
* running headers/footers (lines repeated across pages in the top/bottom margin, page numbers)
  are removed: they otherwise pollute every chunk and every embedding;
* paragraphs by vertical gaps / style changes; tables as Markdown; every block keeps page + bbox.

Docling (``ATLAS_PARSER=docling``) is better on complex layouts/scans but pulls in PyTorch;
this parser keeps the default image small and fast (ADR-0011).
"""

from __future__ import annotations

import io
import re
import statistics
from collections import Counter
from typing import Any

import pdfplumber

from atlas.ingestion.parsers.base import Block, ParsedDocument

_CHAPTER = re.compile(r"^(chapter|part|section|annex|appendix|schedule)\s+[\dIVXLC]+\b", re.I)
_LETTERED = re.compile(r"^([A-Z]{1,2})\.\s+\S")
_SUBNUM = re.compile(r"^([A-Z]{1,2}|\d{1,2})\.\d{1,2}\.?\s+\S")
_DOT_LEADER = re.compile(r"\.{5,}|…{2,}")
_PAGE_NO = re.compile(r"^(page\s*)?\d{1,4}(\s*(of|/)\s*\d{1,4})?$", re.I)
_DIGITS = re.compile(r"\d+")


def _table_md(rows: list[list[Any]]) -> str:
    rows = [[(c or "").replace("\n", " ").strip() for c in r] for r in rows if r and any(r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    head, *body = rows
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines)


def _inside(word: dict[str, Any], boxes: list[tuple[float, float, float, float]]) -> bool:
    cx, cy = (word["x0"] + word["x1"]) / 2, (word["top"] + word["bottom"]) / 2
    return any(x0 <= cx <= x1 and t <= cy <= b for x0, t, x1, b in boxes)


def _is_bold(fontname: str) -> bool:
    f = fontname.lower()
    return "bold" in f or "black" in f or "heavy" in f or f.endswith(",b")


def _lines(page: Any, tboxes: list[tuple[float, float, float, float]]) -> list[dict[str, Any]]:
    words = [w for w in page.extract_words(extra_attrs=["size", "fontname"], use_text_flow=True)
             if not _inside(w, tboxes)]
    lines: list[dict[str, Any]] = []
    for w in words:
        if lines and abs(w["top"] - lines[-1]["top"]) < 3:
            ln = lines[-1]
            ln["text"] += " " + w["text"]
            ln["x0"], ln["x1"] = min(ln["x0"], w["x0"]), max(ln["x1"], w["x1"])
            ln["bottom"] = max(ln["bottom"], w["bottom"])
            ln["sizes"].append(w["size"])
            ln["bold"].append(_is_bold(w.get("fontname", "")))
        else:
            lines.append({"text": w["text"], "x0": w["x0"], "x1": w["x1"], "top": w["top"], "bottom": w["bottom"],
                          "sizes": [w["size"]], "bold": [_is_bold(w.get("fontname", ""))]})
    for ln in lines:
        ln["is_bold"] = sum(ln["bold"]) >= 0.8 * len(ln["bold"])
        ln["size"] = statistics.mean(ln["sizes"])
    return lines


def _signature(text: str) -> str:
    """Normalise a header/footer line so 'Page 3' and 'Page 4' compare equal."""
    return _DIGITS.sub("#", " ".join(text.lower().split()))


def _heading_level(text: str, size: float, body: float) -> int:
    if _CHAPTER.match(text):
        return 1
    if _SUBNUM.match(text):
        return 3
    if _LETTERED.match(text):
        return 2
    return 1 if size >= body * 1.6 else 2 if size >= body * 1.3 else 3


def parse_pdf(data: bytes, filename: str = "") -> ParsedDocument:
    blocks: list[Block] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        meta = {k: str(v) for k, v in (pdf.metadata or {}).items() if isinstance(v, str | int | float)}
        sizes = [c["size"] for p in pdf.pages[:10] for c in p.chars[:5000]]
        body_size = statistics.median(sizes) if sizes else 10.0

        # pass 1: lines per page + running header/footer detection
        pages: list[tuple[Any, list[Any], list[dict[str, Any]]]] = []
        margin_sigs: Counter[str] = Counter()
        for page in pdf.pages:
            tables = page.find_tables()
            lines = _lines(page, [t.bbox for t in tables])
            h = float(page.height)
            for ln in lines:
                if ln["top"] < h * 0.1 or ln["bottom"] > h * 0.9:
                    margin_sigs[_signature(ln["text"])] += 1
            pages.append((page, tables, lines))
        n_pages = len(pages)
        repeated = {sig for sig, c in margin_sigs.items() if n_pages >= 3 and c >= max(3, 0.3 * n_pages)}

        # pass 2: blocks
        for pno, (page, tables, lines) in enumerate(pages, start=1):
            h = float(page.height)
            items: list[tuple[float, Block]] = []
            for t in tables:
                md = _table_md(t.extract())
                if md:
                    items.append((t.bbox[1], Block("table", md, page=pno, bbox=tuple(t.bbox))))  # type: ignore[arg-type]
            kept = []
            for ln in lines:
                in_margin = ln["top"] < h * 0.1 or ln["bottom"] > h * 0.9
                if in_margin and (_signature(ln["text"]) in repeated or _PAGE_NO.match(ln["text"].strip())):
                    continue
                kept.append(ln)

            para: list[dict[str, Any]] = []

            def flush() -> None:
                if not para:
                    return
                text = " ".join(ln["text"] for ln in para).strip()
                if text.count("�") > 0.05 * len(text):
                    para.clear()  # broken font mapping (undecodable glyphs): unreadable noise for search
                    return
                size = statistics.mean(ln["size"] for ln in para)
                bold = all(ln["is_bold"] for ln in para)
                bbox = (min(ln["x0"] for ln in para), para[0]["top"], max(ln["x1"] for ln in para), para[-1]["bottom"])
                toc = bool(_DOT_LEADER.search(text))
                is_heading = not toc and len(text) < 180 and len(para) <= 3 and (
                    size >= body_size * 1.2 or (bold and not text.endswith((".", ",", ";"))))
                kind = "heading" if is_heading else "paragraph"
                items.append((para[0]["top"], Block(kind, text, level=_heading_level(text, size, body_size)
                                                    if is_heading else 0, page=pno, bbox=bbox)))
                para.clear()

            for ln in kept:
                if para:
                    prev = para[-1]
                    gap = ln["top"] - prev["bottom"]
                    height = prev["bottom"] - prev["top"]
                    style_change = abs(ln["size"] - prev["size"]) > 1.0 or ln["is_bold"] != prev["is_bold"]
                    if gap > max(4.0, 0.8 * height) or style_change:
                        flush()
                para.append(ln)
            flush()
            blocks.extend(b for _, b in sorted(items, key=lambda x: x[0]))

        # PDF metadata titles are often blank or junk ("Microsoft Word - final.docx"): prefer the first
        # substantial heading that isn't a running header.
        meta_title = (meta.get("Title") or "").strip()
        first_page = [b.text for b in blocks if b.kind == "heading" and b.page == 1 and "�" not in b.text]
        first_heading = max(first_page, key=len, default="") or next(
            (b.text for b in blocks if b.kind == "heading" and len(b.text) > 15), "")
        title = meta_title if len(meta_title) > 5 and "microsoft word" not in meta_title.lower() else             (first_heading or filename)
        return ParsedDocument(title=title[:500], blocks=blocks, page_count=n_pages, metadata=meta,
                              parser="pdfplumber")
