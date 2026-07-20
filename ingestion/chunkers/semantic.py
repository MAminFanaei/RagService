"""
Semantic chunker — uses Chonkie's SemanticChunker backed by BGE-M3 embeddings
to split text at semantic similarity boundaries.

Best for OCR output where heading structure is absent or unreliable.
Splits happen where the semantic similarity between adjacent sentences drops
below a threshold, producing topic-coherent chunks.

Falls back to FixedChunker if chonkie or the embedding model is unavailable.
"""

from __future__ import annotations

import structlog

from ingestion.chunkers.base import BaseChunker
from ingestion.config import get_settings

logger = structlog.get_logger(__name__)


def _build_semantic_chunker(max_tokens: int):
    """
    Build a Chonkie SemanticChunker with BGE-M3 embeddings.
    Returns None if chonkie or sentence-transformers is not available.
    """
    try:
        from chonkie import SemanticChunker  # type: ignore
        chunker = SemanticChunker(
            embedding_model="BAAI/bge-m3",
            chunk_size=max_tokens,
            threshold=0.5,          # similarity drop threshold
            min_sentences=1,
        )
        return chunker
    except Exception as exc:
        logger.warning(
            "semantic_chunker_unavailable",
            error=str(exc),
            fallback="FixedChunker",
        )
        return None


class SemanticChunker(BaseChunker):
    """
    Splits text at semantic similarity boundaries using Chonkie + BGE-M3.
    Falls back to FixedChunker if Chonkie is unavailable.
    """

    def __init__(self, max_tokens: int | None = None) -> None:
        s = get_settings()
        self._max_tokens = max_tokens or s.CHUNK_MAX_TOKENS
        self._chonkie = None      # lazy
        self._chonkie_tried = False

    def _get_chonkie(self):
        if not self._chonkie_tried:
            self._chonkie = _build_semantic_chunker(self._max_tokens)
            self._chonkie_tried = True
        return self._chonkie

    @property
    def name(self) -> str:
        return "semantic"

    def _split_with_chonkie(self, text: str) -> list[str]:
        """Split text using Chonkie. Returns list of text pieces."""
        chunker = self._get_chonkie()
        if chunker is None:
            return [text]
        try:
            chunks = chunker.chunk(text)
            # Chonkie returns objects with .text attribute
            return [c.text for c in chunks if c.text.strip()]
        except Exception as exc:
            logger.warning("chonkie_split_failed", error=str(exc))
            return [text]

    def _nearest_heading_metadata(self, elements: list[dict], idx: int) -> dict:
        """
        Find the nearest heading element before idx and return its metadata
        fields to inherit (section_title, section_path, heading_level).
        """
        for i in range(idx - 1, -1, -1):
            if elements[i].get("element_type") == "heading":
                return {
                    "section_title": elements[i].get("section_title"),
                    "section_path":  elements[i].get("section_path", []),
                    "heading_level": elements[i].get("heading_level"),
                    "section_title_text": elements[i].get("section_title", ""),
                }
        return {}

    def chunk(self, elements: list[dict]) -> list[dict]:
        non_tables, tables = self._extract_tables(elements)

        # Check if Chonkie is available; if not, fall back immediately
        if self._get_chonkie() is None:
            logger.info("semantic_chunker_fallback", reason="chonkie_unavailable")
            from ingestion.chunkers.fixed import FixedChunker
            fallback = FixedChunker(max_tokens=self._max_tokens)
            result = fallback.chunk(non_tables)
            result = self._reinsert_tables(result, tables)
            return self._renumber(result)

        result: list[dict] = []

        for idx, el in enumerate(non_tables):
            if self._is_protected(el):
                result.append({**el, "chunk_id": self._new_id()})
                continue

            text = el.get("text", "")
            pieces = self._split_with_chonkie(text)
            heading_meta = self._nearest_heading_metadata(non_tables, idx)

            for piece in pieces:
                piece = piece.strip()
                if not piece:
                    continue
                section_title = el.get("section_title") or heading_meta.get("section_title", "")
                new_chunk = {
                    **el,
                    **heading_meta,
                    "chunk_id":    self._new_id(),
                    "text":        piece,
                    "char_count":  len(piece),
                    "token_estimate": max(1, len(piece.split()) * 13 // 10),
                    "section_title_text": (
                        f"{section_title} {piece}".strip() if section_title else piece
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
