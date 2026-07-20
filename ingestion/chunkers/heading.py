"""
Heading chunker — groups elements by section_path, then splits/merges
groups to fit within token bounds.

Best for structured documents (DOCX, well-formed PDFs) where the parser
has already extracted a heading hierarchy.

Algorithm:
  1. Group consecutive elements by their section_path (shared prefix)
  2. If a group exceeds CHUNK_MAX_TOKENS → split with FixedChunker within
     the group, each sub-chunk inherits the group's heading metadata
  3. If a group is smaller than CHUNK_MIN_TOKENS → merge with the next
     sibling group (if they share a common parent section)
  4. Tables and headings are never split (handled by BaseChunker)
"""

from __future__ import annotations

import structlog

from ingestion.chunkers.base import BaseChunker
from ingestion.config import get_settings

logger = structlog.get_logger(__name__)


def _section_key(el: dict) -> tuple:
    """Return a tuple key representing the element's section path."""
    path = el.get("section_path") or []
    return tuple(path)


def _share_parent(key_a: tuple, key_b: tuple) -> bool:
    """Return True if two section keys share the same parent (all but last component)."""
    if not key_a or not key_b:
        return False
    return key_a[:-1] == key_b[:-1]


class HeadingChunker(BaseChunker):
    """
    Groups elements by section and respects token bounds.
    """

    def __init__(
        self,
        max_tokens: int | None = None,
        min_tokens: int | None = None,
        overlap_tokens: int | None = None,
    ) -> None:
        s = get_settings()
        self._max_tokens     = max_tokens     or s.CHUNK_MAX_TOKENS
        self._min_tokens     = min_tokens     or s.CHUNK_MIN_TOKENS
        self._overlap_tokens = overlap_tokens or s.CHUNK_OVERLAP_TOKENS
        self._max_chars      = self._max_tokens * 3
        self._min_chars      = self._min_tokens * 3

    @property
    def name(self) -> str:
        return "heading"

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _token_estimate(self, el: dict) -> int:
        est = el.get("token_estimate")
        if est:
            return est
        return max(1, len(el.get("text", "").split()) * 13 // 10)

    def _group_total_tokens(self, group: list[dict]) -> int:
        return sum(self._token_estimate(e) for e in group)

    def _split_group(self, group: list[dict]) -> list[dict]:
        """Split a group that exceeds max_tokens using FixedChunker."""
        from ingestion.chunkers.fixed import FixedChunker
        fc = FixedChunker(
            max_tokens=self._max_tokens,
            overlap_tokens=self._overlap_tokens,
        )
        return fc.chunk(group)

    def _merge_elements(self, group: list[dict]) -> dict:
        """
        Merge a group of small elements into one chunk.
        Uses the first element as the base and concatenates text with newlines.
        """
        if len(group) == 1:
            return {**group[0], "chunk_id": self._new_id()}

        base = group[0]
        merged_text = "\n\n".join(
            e.get("text", "") for e in group if e.get("text", "").strip()
        )
        section_title = base.get("section_title", "")
        return {
            **base,
            "chunk_id": self._new_id(),
            "text": merged_text,
            "char_count": len(merged_text),
            "token_estimate": max(1, len(merged_text.split()) * 13 // 10),
            "section_title_text": (
                f"{section_title} {merged_text}".strip()
                if section_title else merged_text
            ),
        }

    # ------------------------------------------------------------------ #
    # Main chunking logic                                                  #
    # ------------------------------------------------------------------ #

    def chunk(self, elements: list[dict]) -> list[dict]:
        # Extract tables first — they bypass all grouping logic
        non_tables, tables = self._extract_tables(elements)

        # Step 1: Group by section_path
        groups: list[list[dict]] = []
        current_key: tuple = ()
        current_group: list[dict] = []

        for el in non_tables:
            key = _section_key(el)
            if key == current_key:
                current_group.append(el)
            else:
                if current_group:
                    groups.append(current_group)
                current_key = key
                current_group = [el]

        if current_group:
            groups.append(current_group)

        # Step 2: Split oversized groups / merge undersized groups
        result: list[dict] = []
        pending_merge: list[dict] = []   # accumulates small groups to merge
        pending_key: tuple = ()

        def flush_pending():
            if pending_merge:
                merged = self._merge_elements(pending_merge)
                result.append(merged)
            pending_merge.clear()

        for group in groups:
            total = self._group_total_tokens(group)
            key = _section_key(group[0])

            if total > self._max_tokens:
                # Flush any pending merge first
                flush_pending()
                pending_key = ()
                # Split this group
                split_chunks = self._split_group(group)
                result.extend(split_chunks)

            elif total < self._min_tokens:
                # Try to merge with pending if they share a parent
                if pending_merge and _share_parent(pending_key, key):
                    pending_merge.extend(group)
                else:
                    flush_pending()
                    pending_merge.extend(group)
                    pending_key = key

            else:
                # Normal-sized group: flush pending, emit as merged chunk
                flush_pending()
                pending_key = ()
                merged = self._merge_elements(group)
                result.append(merged)

        flush_pending()

        result = self._reinsert_tables(result, tables)
        result = self._renumber(result)

        logger.info(
            "chunker_complete",
            chunker=self.name,
            input_count=len(elements),
            output_count=len(result),
        )
        return result
