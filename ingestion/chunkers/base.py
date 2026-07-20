"""
Chunker base class.

Every chunker takes a list of normalized chunk dicts (from normalizer.py)
and returns a new list of chunk dicts where each dict represents one final
chunk to be embedded and indexed.

Chunkers must:
  - Never split table chunks (is_table=True)
  - Never split heading chunks (element_type="heading")
  - Preserve all metadata fields from the input dicts
  - Re-number chunk_index and total_chunks in output
  - Generate new chunk_id UUIDs for any newly created chunks

Tables and headings flow through every chunker unchanged.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod


class BaseChunker(ABC):
    """
    Abstract base for all chunking strategies.

    Input:  list of chunk dicts (output of normalizer.py or dedup.py)
    Output: list of chunk dicts (re-chunked, renumbered, new UUIDs assigned)
    """

    @abstractmethod
    def chunk(self, elements: list[dict]) -> list[dict]:
        """
        Re-chunk the input element list.

        Args:
            elements: Normalized chunk dicts from normalizer.py

        Returns:
            New list of chunk dicts ready for embedding.
            Every output dict must have all original fields plus:
              - A fresh chunk_id UUID
              - Correct chunk_index (0-based sequential)
              - Correct total_chunks (= len(result))
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable chunker name for logging and Document.chunker_used."""
        ...

    # ------------------------------------------------------------------ #
    # Shared utilities                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_table(chunk: dict) -> bool:
        return chunk.get("is_table", False)

    @staticmethod
    def _is_heading(chunk: dict) -> bool:
        return chunk.get("element_type") == "heading"

    @staticmethod
    def _is_protected(chunk: dict) -> bool:
        """Tables and headings are never split."""
        return chunk.get("is_table", False) or chunk.get("element_type") == "heading"

    @staticmethod
    def _new_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def _renumber(chunks: list[dict]) -> list[dict]:
        """Re-assign chunk_index and total_chunks after any structural change."""
        total = len(chunks)
        for i, c in enumerate(chunks):
            c["chunk_index"] = i
            c["total_chunks"] = total
        return chunks

    @staticmethod
    def _merge_metadata(base: dict, extra: dict) -> dict:
        """
        Merge extra metadata into base dict without overwriting critical fields.
        Used when splitting a chunk: the sub-chunk inherits all metadata from parent.
        """
        merged = {**base}
        _PRESERVE = {"chunk_id", "text", "char_count", "token_estimate", "chunk_index", "total_chunks"}
        for k, v in extra.items():
            if k not in _PRESERVE:
                merged[k] = v
        return merged

    def _extract_tables(self, chunks: list[dict]) -> tuple[list[dict], list[tuple[int, dict]]]:
        """
        Separate table chunks from non-table chunks.

        Returns:
            (non_table_chunks, [(original_index, table_chunk), ...])
        """
        non_tables: list[dict] = []
        tables: list[tuple[int, dict]] = []
        for i, c in enumerate(chunks):
            if self._is_table(c):
                tables.append((i, c))
            else:
                non_tables.append(c)
        return non_tables, tables

    def _reinsert_tables(
        self,
        chunked: list[dict],
        tables: list[tuple[int, dict]],
    ) -> list[dict]:
        """
        Re-insert table chunks at approximately their original positions.

        Tables are inserted by proportional position (original_index / total_original * len(chunked)).
        """
        if not tables:
            return chunked

        result = list(chunked)

        # Estimate original total size
        original_total = len(result) + len(tables)

        for orig_idx, table in tables:
            # Proportional position in the new list
            target_pos = int((orig_idx / max(original_total, 1)) * max(len(result), 1))
            target_pos = max(0, min(target_pos, len(result)))
            result.insert(target_pos, table)

        return result
