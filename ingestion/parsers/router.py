"""
Parser router — maps MIME type to an ordered list of parsers and tries each
in turn, falling back on failure.

PDF routing also accounts for scanned vs text PDFs using a lightweight
heuristic on the PyMuPDF page objects before handing off to the heavy parsers.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import structlog

from ingestion.parsers.base import ParsedElement, ParserError

if TYPE_CHECKING:
    from ingestion.parsers.base import BaseParser

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Scanned-page / mixed-PDF detection
# ---------------------------------------------------------------------------

def _is_page_scanned(page) -> bool:
    """
    Return True when a PyMuPDF page has no extractable text but contains images.
    Used to decide whether OCR is needed before dispatching to a heavy parser.
    """
    try:
        text = page.get_text("text").strip()
        images = page.get_images(full=False)
        return len(text) == 0 and len(images) > 0
    except Exception:
        return False


def _classify_pdf(data: bytes) -> str:
    """
    Classify a PDF as 'text', 'scanned', or 'mixed' by sampling the first
    10 pages.  Requires PyMuPDF — called only when mime_type == 'application/pdf'.

    Returns one of: 'text' | 'scanned' | 'mixed'
    """
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(stream=data, filetype="pdf")
        n = min(len(doc), 10)
        if n == 0:
            return "text"

        scanned_count = sum(1 for i in range(n) if _is_page_scanned(doc[i]))
        ratio = scanned_count / n

        if ratio == 0.0:
            return "text"
        elif ratio == 1.0:
            return "scanned"
        else:
            return "mixed"
    except Exception as exc:
        logger.warning("pdf_classify_failed", error=str(exc))
        return "mixed"  # conservative fallback — use full parser chain


# ---------------------------------------------------------------------------
# Parser factory helpers
# ---------------------------------------------------------------------------

def _pdf_parsers(pdf_kind: str) -> list["BaseParser"]:
    """Return ordered parser list for a PDF based on scanned classification."""
    from ingestion.parsers.pdf_pymupdf import PyMuPDFParser
    from ingestion.parsers.pdf_docling import DoclingParser
    from ingestion.parsers.pdf_deepdoc import DeepDocParser

    pymupdf = PyMuPDFParser()
    docling = DoclingParser()
    deepdoc = DeepDocParser()

    if pdf_kind == "text":
        return [docling, pymupdf]
    elif pdf_kind == "scanned":
        return [deepdoc, pymupdf]
    else:  # mixed
        return [docling, deepdoc, pymupdf]


def _parsers_for_mime(mime_type: str, data: bytes) -> list["BaseParser"]:
    """Return ordered list of parsers for the given MIME type."""
    from ingestion.parsers.docx import DocxParser
    from ingestion.parsers.pdf_pymupdf import PyMuPDFParser
    from ingestion.parsers.pptx import PptxParser
    from ingestion.parsers.xlsx import XlsxParser
    from ingestion.parsers.html import HtmlParser
    from ingestion.parsers.text import TextParser

    m = mime_type.lower()

    if m == "application/pdf":
        kind = _classify_pdf(data)
        logger.info("pdf_classified", kind=kind)
        return _pdf_parsers(kind)

    if m == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return [DocxParser()]

    if m == "application/vnd.openxmlformats-officedocument.presentationml.presentation":
        return [PptxParser()]

    if m == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        return [XlsxParser()]

    if m in ("text/html", "application/xhtml+xml"):
        return [HtmlParser()]

    if m in ("text/plain", "text/markdown", "text/x-markdown"):
        return [TextParser()]

    # Images with OCR — return empty list; the pipeline handles these directly
    if m in ("image/png", "image/jpeg", "image/jpg", "image/tiff", "image/tif"):
        return []

    # Unknown — will raise in route_and_parse
    return []


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def route_and_parse(
    mime_type: str,
    data: bytes,
    filename: str,
) -> list[ParsedElement]:
    """
    Route `data` to the appropriate parser(s) based on `mime_type` and return
    a list of ParsedElement objects.

    Tries each parser in priority order; on ParserError or unexpected exception,
    logs the failure and moves on.  Raises AllParsersFailedError if no parser
    succeeds.

    For pure-image MIME types (PNG/JPEG/TIFF) returns a single ParsedElement
    with element_type='image_page' so the OCR router can handle it.
    """
    image_mimes = {"image/png", "image/jpeg", "image/jpg", "image/tiff", "image/tif"}
    if mime_type.lower() in image_mimes:
        return [
            ParsedElement(
                text="",
                parser_name="intake",
                element_type="image_page",
                page_number=1,
                raw_metadata={"image_bytes": data, "filename": filename},
            )
        ]

    parsers = _parsers_for_mime(mime_type, data)

    if not parsers:
        raise AllParsersFailedError(
            mime_type=mime_type,
            errors=[f"No parser registered for MIME type: {mime_type}"],
        )

    errors: list[str] = []
    for parser in parsers:
        try:
            log = logger.bind(parser=parser.name, filename=filename)
            log.info("parser_attempting")
            elements = await parser.parse(data, filename)
            log.info("parser_succeeded", element_count=len(elements))
            return elements
        except ParserError as exc:
            errors.append(str(exc))
            logger.warning(
                "parser_failed",
                parser=parser.name,
                reason=exc.reason,
                will_retry=True,
            )
        except Exception as exc:
            errors.append(f"[{parser.name}] Unexpected: {exc}")
            logger.exception("parser_unexpected_error", parser=parser.name)

    raise AllParsersFailedError(mime_type=mime_type, errors=errors)


class AllParsersFailedError(Exception):
    """Raised when every parser in the fallback chain has failed."""

    def __init__(self, mime_type: str, errors: list[str]):
        self.mime_type = mime_type
        self.errors = errors
        detail = "; ".join(errors)
        super().__init__(
            f"All parsers failed for MIME type '{mime_type}'. Errors: {detail}"
        )
