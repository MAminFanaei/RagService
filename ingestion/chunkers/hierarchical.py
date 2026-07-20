"""
Hierarchical chunker — produces parent + child chunk pairs.

Best for long documents (> 50 pages) where retrieval benefits from:
  - Small child chunks (256 tokens) for high-precision matching
  - Large parent chunks (1024 tokens) for LLM context expansion

Output adds a `parent_chunk_id` field to each child chunk.
Parent chunks have `parent_chunk_id = None` and `is_parent = True`.

Strategy:
  1. Run FixedChunker with PARENT_MAX_TOKENS to get parent chunks
  2. For each parent, run FixedChunker with CHILD_MAX_TOKENS to get children
  3. Output: parents first (with is_parent=True), then children (with parent_chunk_id)

The retriever fetches child chunks for precision, then expands to parent
for LLM context — both are stored in ES with the same schema.
"""

from __future__ import annotations

import structlog

from ingestion.chunkers.base import BaseChunker
from ingestion.config import get_settings

logger = structlog.get_logger(__name__)

PARENT_MAX_TOKENS = 1024
CHILD_MAX_TOKENS  = 256
CHILD_OVERLAP     = 32


class HierarchicalChunker(BaseChunker):
    """
    Produces parent + child chunk pairs for long documents.
    Children carry `parent_chunk_id` linking them to their parent.
    """

    def __init__(self) -> None:
        s = get_settings()
        self._overlap = s.CHUNK_OVERLAP_TOKENS

    @property
    def name(self) -> str:
        return "hierarchical"

    def chunk(self, elements: list[dict]) -> list[dict]:
        from ingestion.chunkers.fixed import FixedChunker

        non_tables, tables = self._extract_tables(elements)

        # Step 1: create parent chunks
        parent_chunker = FixedChunker(
            max_tokens=PARENT_MAX_TOKENS,
            overlap_tokens=self._overlap,
        )
        parents = parent_chunker.chunk(non_tables)

        # Mark parents and create children
        all_chunks: list[dict] = []

        for parent in parents:
            if self._is_protected(parent):
                # Tables / headings: emit as-is, no children
                all_chunks.append({
                    **parent,
                    "is_parent": False,
                    "parent_chunk_id": None,
                })
                continue

            parent_id = self._new_id()
            parent_chunk = {
                **parent,
                "chunk_id":       parent_id,
                "is_parent":      True,
                "parent_chunk_id": None,
            }
            all_chunks.append(parent_chunk)

            # Step 2: split parent text into children
            child_chunker = FixedChunker(
                max_tokens=CHILD_MAX_TOKENS,
                overlap_tokens=CHILD_OVERLAP,
            )
            # Wrap the parent text as a single-element list for the chunker
            parent_as_element = [{**parent, "chunk_id": parent_id}]
            children = child_chunker.chunk(parent_as_element)

            for child in children:
                child_chunk = {
                    **child,
                    "chunk_id":       self._new_id(),
                    "is_parent":      False,
                    "parent_chunk_id": parent_id,
                }
                all_chunks.append(child_chunk)

        # Re-insert tables (they get no children)
        all_chunks = self._reinsert_tables(all_chunks, tables)
        all_chunks = self._renumber(all_chunks)

        logger.info(
            "chunker_complete",
            chunker=self.name,
            input_count=len(elements),
            output_count=len(all_chunks),
            parent_count=sum(1 for c in all_chunks if c.get("is_parent")),
            child_count=sum(1 for c in all_chunks if not c.get("is_parent")),
        )
        return all_chunks
