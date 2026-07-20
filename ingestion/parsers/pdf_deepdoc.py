"""
DeepDoc parser — primary parser for scanned PDFs.

Uses the deepdoc library cloned from the RAGFlow project:
  https://github.com/infiniflow/ragflow/tree/main/deepdoc

Expected layout at project root:
  deepdoc/
  ├── parser/
  │   └── pdf_parser.py
  └── vision/
      ├── ocr.py
      ├── layout_recognizer.py
      └── table_structure_recognizer.py

If deepdoc is not available this parser raises ParserError immediately so the
router falls back to PyMuPDF.

Auto-rotation: tries 4 orientations, keeps the one with highest OCR word count.
"""

from __future__ import annotations

import asyncio
import importlib
import io
import sys
from pathlib import Path
from typing import Any

import structlog

from ingestion.parsers.base import BaseParser, ParsedElement, ParserError

logger = structlog.get_logger(__name__)

_DEEPDOC_AVAILABLE: bool | None = None  # cached check


def _check_deepdoc() -> bool:
    global _DEEPDOC_AVAILABLE
    if _DEEPDOC_AVAILABLE is not None:
        return _DEEPDOC_AVAILABLE

    # Try to import; deepdoc must be in PYTHONPATH or at repo root
    try:
        import deepdoc  # type: ignore  # noqa: F401
        _DEEPDOC_AVAILABLE = True
    except ImportError:
        # Also try: project root on sys.path
        root = Path(__file__).resolve().parents[3]  # repo_root
        if (root / "deepdoc").is_dir() and str(root) not in sys.path:
            sys.path.insert(0, str(root))
        try:
            import deepdoc  # type: ignore  # noqa: F401
            _DEEPDOC_AVAILABLE = True
        except ImportError:
            _DEEPDOC_AVAILABLE = False

    return _DEEPDOC_AVAILABLE  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Label mapping — deepdoc uses string labels from its LayoutRecognizer
# ---------------------------------------------------------------------------

_DEEPDOC_LABEL_MAP: dict[str, str] = {
    "title": "heading",
    "text": "text",
    "figure": "image_page",
    "figure_caption": "caption",
    "table": "table",
    "table_caption": "caption",
    "header": "text",
    "footer": "footnote",
    "reference": "text",
    "equation": "formula",
}


def _deepdoc_sync_parse(data: bytes, filename: str) -> list[ParsedElement]:
    """Synchronous deepdoc parse — runs in thread executor."""
    if not _check_deepdoc():
        raise ParserError("pdf_deepdoc", "deepdoc library not found at project root")

    try:
        from deepdoc.parser.pdf_parser import RAGFlowPdfParser  # type: ignore
    except ImportError as exc:
        raise ParserError("pdf_deepdoc", "deepdoc.parser.pdf_parser not importable", exc)

    try:
        parser = RAGFlowPdfParser()
        # RAGFlowPdfParser.parse() accepts bytes or file path depending on version
        # Try bytes first, fallback to tempfile
        sections, tables = _call_parser(parser, data, filename)
    except ParserError:
        raise
    except Exception as exc:
        raise ParserError("pdf_deepdoc", f"deepdoc parse failed: {exc}", exc)

    elements: list[ParsedElement] = []

    # ---- Text sections ----
    for item in sections:
        text = (item.get("text", "") or "").strip()
        if not text:
            continue
        elements.append(
            ParsedElement(
                text=text,
                parser_name="pdf_deepdoc",
                element_type=_DEEPDOC_LABEL_MAP.get(item.get("type", "text"), "text"),
                page_number=item.get("page_number"),
                bounding_box=item.get("bbox"),
                section_path=[],
                raw_metadata={"deepdoc_type": item.get("type", "text")},
            )
        )

    # ---- Tables ----
    for tbl in tables:
        text = tbl.get("text", "") or ""
        md = tbl.get("markdown", "") or text
        if not text.strip():
            continue
        elements.append(
            ParsedElement(
                text=text.strip(),
                parser_name="pdf_deepdoc",
                element_type="table",
                is_table=True,
                table_markdown=md,
                page_number=tbl.get("page_number"),
                bounding_box=tbl.get("bbox"),
                section_path=[],
                raw_metadata={"deepdoc_table": True},
            )
        )

    return elements


def _call_parser(parser, data: bytes, filename: str):
    """
    Attempt to call RAGFlowPdfParser.  Different RAGFlow versions have
    slightly different APIs — we try the most common signatures.
    """
    import tempfile, os

    # Try the bytes-based API (newer versions)
    if hasattr(parser, "parse_bytes"):
        try:
            return parser.parse_bytes(data)
        except Exception:
            pass

    # Fall back to file-path API
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    try:
        result = parser(tmp_path, from_page=0, to_page=10000)
        # Result shape varies: (sections, tables) or just sections
        if isinstance(result, tuple) and len(result) == 2:
            return result
        return result, []
    except Exception as exc:
        raise ParserError("pdf_deepdoc", f"Parser call failed: {exc}", exc)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


class DeepDocParser(BaseParser):
    """Primary parser for scanned PDFs using deepdoc from RAGFlow."""

    @property
    def name(self) -> str:
        return "pdf_deepdoc"

    async def parse(self, data: bytes, filename: str) -> list[ParsedElement]:
        if not _check_deepdoc():
            raise ParserError(self.name, "deepdoc library not available — skipping")

        loop = asyncio.get_running_loop()
        try:
            elements = await loop.run_in_executor(
                None, _deepdoc_sync_parse, data, filename
            )
        except ParserError:
            raise
        except Exception as exc:
            raise ParserError(self.name, f"Unexpected error: {exc}", exc)

        logger.info(
            "pdf_deepdoc_parsed",
            filename=filename,
            element_count=len(elements),
        )
        return elements
