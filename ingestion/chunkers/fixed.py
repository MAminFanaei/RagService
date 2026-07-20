"""
Fixed chunker — splits text using RecursiveCharacterTextSplitter.

Baseline strategy: good for plain text documents with no heading structure
and no OCR output.  Uses LangChain's splitter which respects paragraph,
sentence, and word boundaries in that priority order.

Tables and headings flow through unchanged (handled by BaseChunker utilities).
"""

from __future__ import annotations

import uuid

import structlog

from ingestion.chunkers.base import BaseChunker
from ingestion.config import get_settings

logger = structlog.get_logger(__name__)


class FixedChunker(BaseChunker):
    """
    Splits every non-protected chunk by token count using
    RecursiveCharacterTextSplitter with character-level separators.

    The splitter is instantiated lazily so LangChain is not imported at
    module load time (heavy import).
    """

    def __init__(
        self,
        max_tokens: int | None = None,
        overlap_tokens: int | None = None,
    ) -> None:
        s = get_settings()
        self._max_tokens   = max_tokens   or s.CHUNK_MAX_TOKENS
        self._overlap_tokens = overlap_tokens or s.CHUNK_OVERLAP_TOKENS
        # Approximate: 1 token ≈ 4 chars for Latin; we use a conservative 3
        self._max_chars    = self._max_tokens   * 3
        self._overlap_chars = self._overlap_tokens * 3
        self._splitter = None  # lazy

    def _get_splitter(self):
        if self._splitter is None:
            try:
                from langchain_text_splitters import RecursiveCharacterTextSplitter
            except ImportError:
                from langchain.text_splitter import RecursiveCharacterTextSplitter  # type: ignore
            
            safe_overlap = min(self._overlap_chars, self._max_chars )
            
            self._splitter = RecursiveCharacterTextSplitter(
                chunk_size=self._max_chars,
                chunk_overlap=safe_overlap,
                separators=["\n\n", "\n", ".", "،", "。", " ", ""],
                length_function=len,
                is_separator_regex=False,
            )
        return self._splitter

    @property
    def name(self) -> str:
        return "fixed"

    def chunk(self, elements: list[dict]) -> list[dict]:
        """
        Split each non-protected element into fixed-size chunks.
        Protected elements (tables, headings) pass through as-is.
        """
        non_tables, tables = self._extract_tables(elements)
        splitter = self._get_splitter()
        result: list[dict] = []

        for el in non_tables:
            if self._is_heading(el):
                # Headings pass through without splitting
                result.append({**el, "chunk_id": self._new_id()})
                continue

            text = el.get("text", "")
            if len(text) <= self._max_chars:
                result.append({**el, "chunk_id": self._new_id()})
                continue

            # Split long chunk — sub-chunks inherit all metadata from parent
            pieces = splitter.split_text(text)
            for piece in pieces:
                piece = piece.strip()
                if not piece:
                    continue
                new_chunk = {
                    **el,
                    "chunk_id":     self._new_id(),
                    "text":         piece,
                    "char_count":   len(piece),
                    "token_estimate": max(1, len(piece.split()) * 13 // 10),
                    # section_title_text rebuilt to reflect new text
                    "section_title_text": (
                        f"{el.get('section_title', '')} {piece}".strip()
                        if el.get("section_title")
                        else piece
                    ),
                }
                result.append(new_chunk)

        result = self._reinsert_tables(result, tables)
        result = self._renumber(result)

        logger.info(
            "chunker_complete",
            chunker=self.name,
            input_count=len(elements),
            output_count=len(result),
        )
        return result
