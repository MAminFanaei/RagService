"""
Docling parser — primary parser for text-heavy PDFs.

Uses the `docling` library (DocLayNet layout model + TableFormer).
Extracts: reading order, structured tables, footnotes, figure captions.

The parser is wrapped in run_in_executor because docling is synchronous
and CPU-bound.  It is tried first for text PDFs, before PyMuPDF fallback.
"""

from __future__ import annotations

import asyncio
import io
import tempfile
import os
from pathlib import Path
from typing import Any

import structlog

from ingestion.parsers.base import BaseParser, ParsedElement, ParserError

logger = structlog.get_logger(__name__)


# Map docling element label strings to our element_type contract values
_LABEL_MAP: dict[str, str] = {
    "title": "heading",
    "section_header": "heading",
    "text": "text",
    "list_item": "list_item",
    "table": "table",
    "figure_caption": "caption",
    "footnote": "footnote",
    "formula": "formula",
    "code": "code",
    "page_footer": "footnote",
    "page_header": "text",
    "picture": "image_page",
}


def _docling_sync_parse(data: bytes, filename: str) -> list[ParsedElement]:
    """
    Synchronous docling parse.  Called via run_in_executor.
    Raises ParserError on failure so the router can fall back.
    """
    try:
        from docling.document_converter import DocumentConverter, PdfFormatOption  # type: ignore
        from docling.datamodel.pipeline_options import PdfPipelineOptions  # type: ignore
        from docling.datamodel.base_models import InputFormat  # type: ignore
    except ImportError as exc:
        raise ParserError("pdf_docling", "docling not installed", exc)

    # Write bytes to a temp file — docling requires a file path
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    try:
        pipeline_opts = PdfPipelineOptions()
        pipeline_opts.do_ocr = False          # OCR handled by dedicated layer
        pipeline_opts.do_table_structure = True

        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_opts)
            }
        )
        result = converter.convert(tmp_path)
        doc = result.document
    except Exception as exc:
        raise ParserError("pdf_docling", f"Docling conversion failed: {exc}", exc)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    elements: list[ParsedElement] = []
    section_headings: dict[int, str] = {}

    def current_path() -> list[str]:
        return [section_headings[lvl] for lvl in sorted(section_headings)]

    def current_title() -> str | None:
        if not section_headings:
            return None
        return section_headings[max(section_headings)]

    try:
        # docling DoclingDocument has .body.children with typed nodes
        for item, _level in doc.iterate_items():
            label = getattr(item, "label", None)
            if label is None:
                continue

            label_str = str(label.value) if hasattr(label, "value") else str(label)
            etype = _LABEL_MAP.get(label_str, "text")

            # ---- Text / page references ----
            text = ""
            try:
                text = item.text if hasattr(item, "text") else str(item)
            except Exception:
                text = ""

            text = (text or "").strip()
            if not text and etype not in ("table", "image_page"):
                continue

            # ---- Page number ----
            page_num: int | None = None
            try:
                prov = item.prov
                if prov and len(prov) > 0:
                    page_num = prov[0].page_no
            except Exception:
                pass

            # ---- Bounding box ----
            bb: dict | None = None
            try:
                if item.prov and len(item.prov) > 0:
                    bbox = item.prov[0].bbox
                    bb = {
                        "x0": bbox.l,
                        "y0": bbox.t,
                        "x1": bbox.r,
                        "y1": bbox.b,
                    }
            except Exception:
                pass

            # ---- Heading level ----
            heading_lvl: int | None = None
            if etype == "heading":
                # docling exposes level on SectionHeaderItem
                try:
                    heading_lvl = int(item.level) if hasattr(item, "level") else 1
                except Exception:
                    heading_lvl = 1
                # Update tracker
                for lvl in range(heading_lvl + 1, 9):
                    section_headings.pop(lvl, None)
                section_headings[heading_lvl] = text

            # ---- Table ----
            is_tbl = etype == "table"
            tbl_md: str | None = None
            if is_tbl:
                try:
                    tbl_md = item.export_to_markdown()
                except Exception:
                    tbl_md = text

            # ---- Image page ----
            raw_meta: dict[str, Any] = {"label": label_str}
            if etype == "image_page":
                raw_meta["image_bytes"] = b""  # docling doesn't expose raw bytes here
                text = ""

            elements.append(
                ParsedElement(
                    text=text,
                    parser_name="pdf_docling",
                    element_type=etype,
                    is_table=is_tbl,
                    table_markdown=tbl_md,
                    page_number=page_num,
                    bounding_box=bb,
                    heading_level=heading_lvl,
                    section_path=current_path(),
                    section_title=current_title() if etype != "heading" else text,
                    raw_metadata=raw_meta,
                )
            )
    except Exception as exc:
        raise ParserError("pdf_docling", f"Element extraction failed: {exc}", exc)

    return elements


class DoclingParser(BaseParser):
    """Primary parser for text-heavy PDFs using the docling library."""

    @property
    def name(self) -> str:
        return "pdf_docling"

    async def parse(self, data: bytes, filename: str) -> list[ParsedElement]:
        loop = asyncio.get_running_loop()
        try:
            elements = await loop.run_in_executor(
                None, _docling_sync_parse, data, filename
            )
        except ParserError:
            raise
        except Exception as exc:
            raise ParserError(self.name, f"Unexpected error: {exc}", exc)

        logger.info(
            "pdf_docling_parsed",
            filename=filename,
            element_count=len(elements),
        )
        return elements
