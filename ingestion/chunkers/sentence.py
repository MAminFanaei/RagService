"""
Sentence chunker — uses Chonkie's SentenceChunker with a multilingual
sentence tokenizer (works for EN/FA/AR).

Best for short documents like slide decks (PPTX) or spreadsheets where
content is already sentence-length or shorter.

Overlap is in sentences, not characters, which avoids cutting mid-thought.
Falls back to FixedChunker if Chonkie is unavailable.
"""

from __future__ import annotations

import structlog

from ingestion.chunkers.base import BaseChunker
from ingestion.config import get_settings

logger = structlog.get_logger(__name__)


def _build_sentence_chunker(max_tokens: int, overlap_sentences: int):
    """
    Build a Chonkie SentenceChunker.
    Returns None if chonkie is not installed.
    """
    try:
        from chonkie import SentenceChunker  # type: ignore
        chunker = SentenceChunker(
            chunk_size=max_tokens,
            chunk_overlap=overlap_sentences,  # overlap in sentences
            min_sentences_per_chunk=1,
        )
        return chunker
    except Exception as exc:
        logger.warning(
            "sentence_chunker_unavailable",
            error=str(exc),
            fallback="FixedChunker",
        )
        return None


class SentenceChunker(BaseChunker):
    """
    Splits text at sentence boundaries using Chonkie + multilingual tokenizer.
    Falls back to FixedChunker if Chonkie is unavailable.
    """

    def __init__(
        self,
        max_tokens: int | None = None,
        overlap_sentences: int = 1,
    ) -> None:
        s = get_settings()
        self._max_tokens       = max_tokens or s.CHUNK_MAX_TOKENS
        self._overlap_sentences = overlap_sentences
        self._chonkie = None
        self._chonkie_tried = False

    def _get_chonkie(self):
        if not self._chonkie_tried:
            self._chonkie = _build_sentence_chunker(
                self._max_tokens, self._overlap_sentences
            )
            self._chonkie_tried = True
        return self._chonkie

    @property
    def name(self) -> str:
        return "sentence"

    def _split_with_chonkie(self, text: str) -> list[str]:
        chunker = self._get_chonkie()
        if chunker is None:
            return [text]
        try:
            chunks = chunker.chunk(text)
            return [c.text for c in chunks if c.text.strip()]
        except Exception as exc:
            logger.warning("sentence_split_failed", error=str(exc))
            return [text]

    def chunk(self, elements: list[dict]) -> list[dict]:
        non_tables, tables = self._extract_tables(elements)

        if self._get_chonkie() is None:
            logger.info("sentence_chunker_fallback", reason="chonkie_unavailable")
            from ingestion.chunkers.fixed import FixedChunker
            fallback = FixedChunker(max_tokens=self._max_tokens)
            result = fallback.chunk(non_tables)
            result = self._reinsert_tables(result, tables)
            return self._renumber(result)

        result: list[dict] = []

        for el in non_tables:
            if self._is_protected(el):
                result.append({**el, "chunk_id": self._new_id()})
                continue

            text = el.get("text", "")
            pieces = self._split_with_chonkie(text)
            section_title = el.get("section_title", "")

            for piece in pieces:
                piece = piece.strip()
                if not piece:
                    continue
                new_chunk = {
                    **el,
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
