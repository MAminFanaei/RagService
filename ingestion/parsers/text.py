"""
Plain text and Markdown parser.

- .txt / text/plain: Split on double newlines into paragraphs.
- .md / text/markdown: Use MarkdownHeaderTextSplitter (LangChain) to extract
  header-aware sections, then split oversized sections by paragraph.

Both paths emit ParsedElement objects with section_path populated from Markdown
headers (if present).
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from ingestion.parsers.base import BaseParser, ParsedElement, ParserError

logger = structlog.get_logger(__name__)

# Headers that MarkdownHeaderTextSplitter will track
_MARKDOWN_HEADERS = [
    ("#", "h1"),
    ("##", "h2"),
    ("###", "h3"),
    ("####", "h4"),
    ("#####", "h5"),
    ("######", "h6"),
]


def _split_paragraphs(text: str) -> list[str]:
    """Split text on blank lines, return non-empty paragraphs."""
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _parse_markdown(text: str, filename: str) -> list[ParsedElement]:
    """Parse Markdown with header-aware splitting via LangChain."""
    try:
        from langchain_text_splitters import MarkdownHeaderTextSplitter  # type: ignore
    except ImportError:
        try:
            from langchain.text_splitter import MarkdownHeaderTextSplitter  # type: ignore
        except ImportError:
            # Fallback: treat as plain text
            logger.warning("markdown_header_splitter_unavailable", filename=filename)
            return _parse_plain(text, filename)

    splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=_MARKDOWN_HEADERS,
        strip_headers=True,
    )

    try:
        docs = splitter.split_text(text)
    except Exception as exc:
        logger.warning("markdown_split_failed", error=str(exc), filename=filename)
        return _parse_plain(text, filename)

    elements: list[ParsedElement] = []

    for doc in docs:
        content = (doc.page_content or "").strip()
        if not content:
            continue

        meta = doc.metadata or {}
        # Build section_path from header metadata
        path: list[str] = []
        title: str | None = None

        for _, key in _MARKDOWN_HEADERS:
            val = meta.get(key)
            if val:
                path.append(val)
                title = val

        # Emit a heading element if we have a title
        if title and (not elements or elements[-1].section_title != title):
            elements.append(
                ParsedElement(
                    text=title,
                    parser_name="text",
                    element_type="heading",
                    heading_level=len(path),
                    section_path=list(path),
                    section_title=title,
                    raw_metadata={"source": "markdown_header"},
                )
            )

        # Split the content into paragraphs
        for para in _split_paragraphs(content):
            # Detect inline code blocks
            if para.startswith("```") or para.startswith("    "):
                etype = "code"
            else:
                etype = "text"

            elements.append(
                ParsedElement(
                    text=para,
                    parser_name="text",
                    element_type=etype,
                    section_path=list(path),
                    section_title=title,
                    raw_metadata={"source": "markdown"},
                )
            )

    return elements


def _parse_plain(text: str, filename: str) -> list[ParsedElement]:
    """Parse plain text by splitting on double newlines."""
    elements: list[ParsedElement] = []
    paras = _split_paragraphs(text)

    for para in paras:
        elements.append(
            ParsedElement(
                text=para,
                parser_name="text",
                element_type="text",
                section_path=[],
                section_title=None,
                raw_metadata={"source": "plain_text"},
            )
        )

    return elements


class TextParser(BaseParser):
    """Parser for .txt and .md files."""

    @property
    def name(self) -> str:
        return "text"

    async def parse(self, data: bytes, filename: str) -> list[ParsedElement]:
        # Decode
        for enc in ("utf-8", "utf-8-sig", "latin-1", "windows-1256"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                pass
        else:
            raise ParserError(self.name, "Cannot decode text file with any known encoding")

        if not text.strip():
            raise ParserError(self.name, "File is empty after decoding")

        fname_lower = filename.lower()
        is_markdown = fname_lower.endswith(".md") or fname_lower.endswith(".markdown")

        if is_markdown:
            elements = _parse_markdown(text, filename)
        else:
            elements = _parse_plain(text, filename)

        if not elements:
            raise ParserError(self.name, "No elements extracted from text file")

        logger.info(
            "text_parsed",
            filename=filename,
            is_markdown=is_markdown,
            element_count=len(elements),
        )
        return elements
