"""
HTML parser.

Primary extraction: trafilatura (main content, removes boilerplate/nav/ads).
Heading structure: BeautifulSoup4 (headings h1-h6 for section_path).

Two-pass strategy:
1. trafilatura extracts clean main text
2. BeautifulSoup maps character offsets to headings to assign section_path
   (approximate: we do best-effort heading attribution by document order)
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from ingestion.parsers.base import BaseParser, ParsedElement, ParserError

logger = structlog.get_logger(__name__)


def _parse_headings(soup) -> list[tuple[str, int, str]]:
    """
    Walk the DOM and return list of (tag_name, heading_level, text) for h1-h6.
    """
    headings = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        text = tag.get_text(separator=" ").strip()
        if not text:
            continue
        level = int(tag.name[1])
        headings.append((tag.name, level, text))
    return headings


class HtmlParser(BaseParser):
    """Parser for HTML pages using trafilatura + BeautifulSoup4."""

    @property
    def name(self) -> str:
        return "html"

    async def parse(self, data: bytes, filename: str) -> list[ParsedElement]:
        try:
            import trafilatura  # type: ignore
        except ImportError as exc:
            raise ParserError(self.name, "trafilatura not installed", exc)

        try:
            from bs4 import BeautifulSoup  # type: ignore
        except ImportError as exc:
            raise ParserError(self.name, "beautifulsoup4 not installed", exc)

        # Decode bytes
        for enc in ("utf-8", "utf-8-sig", "latin-1"):
            try:
                html_str = data.decode(enc)
                break
            except UnicodeDecodeError:
                pass
        else:
            raise ParserError(self.name, "Cannot decode HTML bytes")

        # ---- Parse heading structure ----
        soup = BeautifulSoup(html_str, "html.parser")
        headings = _parse_headings(soup)

        # ---- Extract main content via trafilatura ----
        extracted = trafilatura.extract(
            html_str,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
            output_format="txt",
        )

        if not extracted:
            # Fallback: just use soup text
            extracted = soup.get_text(separator="\n")

        if not extracted or not extracted.strip():
            raise ParserError(self.name, "trafilatura returned empty content")

        elements: list[ParsedElement] = []
        section_headings: dict[int, str] = {}

        def current_path() -> list[str]:
            return [section_headings[lvl] for lvl in sorted(section_headings)]

        def current_title() -> str | None:
            if not section_headings:
                return None
            return section_headings[max(section_headings)]

        # ---- Emit heading elements ----
        for _, level, htext in headings:
            for lvl in range(level + 1, 7):
                section_headings.pop(lvl, None)
            section_headings[level] = htext
            elements.append(
                ParsedElement(
                    text=htext,
                    parser_name=self.name,
                    element_type="heading",
                    heading_level=level,
                    section_path=current_path(),
                    section_title=htext,
                    raw_metadata={"source": "heading"},
                )
            )

        # ---- Split extracted text into paragraphs ----
        # Reset section tracker: attribution is approximate
        section_headings.clear()
        heading_iter = iter(headings)
        current_heading_idx = 0
        active_heading_idx = -1

        # Re-walk: assign paragraphs to headings by position in soup text
        soup_text = soup.get_text(separator="\n")
        heading_positions: list[tuple[int, int, str]] = []
        for _, level, htext in headings:
            pos = soup_text.find(htext)
            if pos >= 0:
                heading_positions.append((pos, level, htext))

        content_pos = 0
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", extracted) if p.strip()]

        # Find heading context for each paragraph (best-effort)
        for para in paragraphs:
            # Try to find where this paragraph falls relative to headings
            # We use a simple approach: find the last heading that appears
            # before this paragraph in soup_text
            para_pos = soup_text.find(para[:40]) if len(para) >= 40 else soup_text.find(para)

            active_lvl: dict[int, str] = {}
            if para_pos >= 0:
                for hpos, hlvl, htext in heading_positions:
                    if hpos < para_pos:
                        # Clear deeper levels
                        for lvl in range(hlvl + 1, 7):
                            active_lvl.pop(lvl, None)
                        active_lvl[hlvl] = htext

            path = [active_lvl[lvl] for lvl in sorted(active_lvl)]
            title = active_lvl[max(active_lvl)] if active_lvl else None

            elements.append(
                ParsedElement(
                    text=para,
                    parser_name=self.name,
                    element_type="text",
                    section_path=path,
                    section_title=title,
                    raw_metadata={"source": "trafilatura"},
                )
            )

        logger.info(
            "html_parsed",
            filename=filename,
            heading_count=len(headings),
            paragraph_count=len(paragraphs),
            element_count=len(elements),
        )
        return elements
