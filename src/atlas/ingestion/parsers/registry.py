"""MIME sniffing + parser dispatch."""

from __future__ import annotations

import zipfile
from io import BytesIO

from atlas.common.errors import InvalidInput
from atlas.common.logging import get_logger
from atlas.ingestion.parsers.base import ParsedDocument
from atlas.ingestion.parsers.office import parse_docx, parse_pptx
from atlas.ingestion.parsers.pdf import parse_pdf
from atlas.ingestion.parsers.text import parse_html, parse_markdown

log = get_logger(__name__)

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
HTML = "text/html"
MARKDOWN = "text/markdown"
TEXT = "text/plain"
SUPPORTED = {PDF, DOCX, PPTX, HTML, MARKDOWN, TEXT}


def sniff_mime(data: bytes, filename: str = "", declared: str | None = None) -> str:
    """Trust magic bytes over the client-declared type (uploads are an attack surface)."""
    name = filename.lower()
    if data[:5] == b"%PDF-":
        return PDF
    if data[:2] == b"PK":
        try:
            names = zipfile.ZipFile(BytesIO(data)).namelist()
        except zipfile.BadZipFile:
            names = []
        if any(n.startswith("word/") for n in names):
            return DOCX
        if any(n.startswith("ppt/") for n in names):
            return PPTX
        raise InvalidInput("unsupported zip-based format")
    head = data[:2048].lstrip().lower()
    if head.startswith((b"<!doctype html", b"<html")) or name.endswith((".html", ".htm")):
        return HTML
    if name.endswith((".md", ".markdown")) or declared == MARKDOWN:
        return MARKDOWN
    try:
        data[:4096].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidInput("binary file of unsupported type") from exc
    return TEXT


def parse(data: bytes, mime: str, filename: str, *, prefer: str = "native") -> ParsedDocument:
    if mime == PDF and prefer == "docling":
        try:
            from atlas.ingestion.parsers.docling_parser import parse_with_docling

            return parse_with_docling(data, filename)
        except ImportError:
            log.warning("docling_not_installed_falling_back")
        except Exception:
            log.exception("docling_failed_falling_back", filename=filename)
    if mime == PDF:
        return parse_pdf(data, filename)
    if mime == DOCX:
        return parse_docx(data, filename)
    if mime == PPTX:
        return parse_pptx(data, filename)
    text = data.decode("utf-8", errors="replace")
    if mime == HTML:
        return parse_html(text, filename)
    return parse_markdown(text, filename)
