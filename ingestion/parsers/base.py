"""
Base types for all parsers.

ParsedElement is the internal intermediate type that every parser produces.
The normalizer then converts list[ParsedElement] → list[dict] (the chunk contract).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedElement:
    """
    Unified output type from every parser.

    All fields are optional except `text` and `parser_name` — parsers fill
    what they can and leave the rest as None/empty.  The normalizer assigns
    defaults for anything missing.
    """

    # Core content
    text: str
    parser_name: str

    # Element classification
    element_type: str = "text"          # text|table|heading|footnote|caption|code|list_item|formula|image_page
    is_table: bool = False
    table_markdown: str | None = None   # Markdown-rendered table (when is_table=True)

    # Location
    page_number: int | None = None
    bounding_box: dict | None = None    # {x0, y0, x1, y1}  — page-relative pixels

    # Heading / section hierarchy
    section_title: str | None = None
    section_path: list[str] = field(default_factory=list)   # ["Ch1", "1.2", "1.2.3"]
    heading_level: int | None = None    # 1-6 (None if not a heading)

    # Language hint from parser (may be None — normalizer runs lingua anyway)
    language_hint: str | None = None

    # Debug / provenance
    raw_metadata: dict[str, Any] = field(default_factory=dict)


class BaseParser(ABC):
    """
    Abstract base for all file parsers.

    Concrete implementations must be async-safe (they run inside Celery workers
    via asyncio.run / loop.run_in_executor as appropriate).
    """

    @abstractmethod
    async def parse(self, data: bytes, filename: str) -> list[ParsedElement]:
        """
        Parse raw file bytes into a list of ParsedElement objects.

        Args:
            data:     Raw file bytes.
            filename: Original filename (used for logging and extension hints only).

        Returns:
            Ordered list of ParsedElement objects preserving document reading order.

        Raises:
            ParserError: On unrecoverable parse failure (router will try next parser).
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier used in logging and Document.parser_used."""
        ...


class ParserError(Exception):
    """Raised by a parser to signal the router should try the next fallback."""

    def __init__(self, parser_name: str, reason: str, cause: Exception | None = None):
        self.parser_name = parser_name
        self.reason = reason
        self.cause = cause
        super().__init__(f"[{parser_name}] {reason}" + (f": {cause}" if cause else ""))
