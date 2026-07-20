"""
Chunker router — selects the best chunking strategy for a given document.

Auto-selection logic:
  1. If CHUNKING_STRATEGY is set (not "auto") → use that strategy
  2. If document has structured headings → HeadingChunker
  3. If parsed by OCR engine (deepdoc, dots_ocr) → SemanticChunker
  4. If document is long (> 50 pages) → HierarchicalChunker
  5. Default → FixedChunker

The router is stateless and does not cache instances.
"""

from __future__ import annotations

import structlog

from ingestion.chunkers.base import BaseChunker
from ingestion.config import get_settings

logger = structlog.get_logger(__name__)


# ------------------------------------------------------------------ #
# Lazy chunker factories                                               #
# ------------------------------------------------------------------ #

def _get_fixed_chunker() -> BaseChunker:
    from ingestion.chunkers.fixed import FixedChunker
    return FixedChunker()


def _get_heading_chunker() -> BaseChunker:
    from ingestion.chunkers.heading import HeadingChunker
    return HeadingChunker()


def _get_semantic_chunker() -> BaseChunker:
    from ingestion.chunkers.semantic import SemanticChunker
    return SemanticChunker()


def _get_sentence_chunker() -> BaseChunker:
    from ingestion.chunkers.sentence import SentenceChunker
    return SentenceChunker()


def _get_hierarchical_chunker() -> BaseChunker:
    from ingestion.chunkers.hierarchical import HierarchicalChunker
    return HierarchicalChunker()


_NAME_TO_FACTORY = {
    "fixed": _get_fixed_chunker,
    "heading": _get_heading_chunker,
    "semantic": _get_semantic_chunker,
    "sentence": _get_sentence_chunker,
    "hierarchical": _get_hierarchical_chunker,
}


def get_chunker_by_name(name: str) -> BaseChunker:
    """Get a chunker by name string. Raises ValueError if unknown."""
    factory = _NAME_TO_FACTORY.get(name.lower())
    if factory is None:
        raise ValueError(
            f"Unknown chunking strategy '{name}'. "
            f"Valid options: {list(_NAME_TO_FACTORY.keys())}"
        )
    return factory()


# ------------------------------------------------------------------ #
# Auto-selection heuristics                                            #
# ------------------------------------------------------------------ #

def _has_heading_structure(elements: list[dict]) -> bool:
    """
    Return True if the element list has at least 2 heading elements.
    This indicates a structured document where heading-aware grouping will work.
    """
    heading_count = sum(1 for e in elements if e.get("element_type") == "heading")
    return heading_count >= 2


def _is_ocr_document(doc) -> bool:
    """Return True if the document was processed by an OCR engine."""
    parser_used = getattr(doc, "parser_used", "") or ""
    ocr_used = getattr(doc, "ocr_used", "") or ""
    return any(
        ocr_name in (parser_used + ocr_used).lower()
        for ocr_name in ("deepdoc", "dots_ocr", "gemma_ocr", "ocr")
    )


def _is_long_document(doc) -> bool:
    """Return True if the document has more than 50 pages."""
    total_pages = getattr(doc, "total_pages", None)
    if total_pages is not None:
        return total_pages > 50
    return False


# ------------------------------------------------------------------ #
# Public entry point                                                   #
# ------------------------------------------------------------------ #

def select_chunker(doc, elements: list[dict]) -> BaseChunker:
    """
    Select the best chunker for the given document and its elements.

    Args:
        doc:      SQL Document record (for parser_used, ocr_used, total_pages).
        elements: Normalized chunk dicts (from normalizer.py / dedup.py).

    Returns:
        Instantiated BaseChunker ready to use.
    """
    settings = get_settings()
    strategy = settings.CHUNKING_STRATEGY.lower()

    # Explicit strategy override
    if strategy != "auto":
        chunker = get_chunker_by_name(strategy)
        logger.info(
            "chunker_selected",
            chunker=chunker.name,
            reason="explicit_config",
            strategy=strategy,
        )
        return chunker

    # Auto-selection
    if _has_heading_structure(elements):
        chunker = _get_heading_chunker()
        reason = "has_heading_structure"

    elif _is_ocr_document(doc):
        chunker = _get_semantic_chunker()
        reason = "ocr_document"

    elif _is_long_document(doc):
        chunker = _get_hierarchical_chunker()
        reason = "long_document"

    else:
        chunker = _get_fixed_chunker()
        reason = "default"

    logger.info(
        "chunker_selected",
        chunker=chunker.name,
        reason=reason,
        total_pages=getattr(doc, "total_pages", None),
        parser_used=getattr(doc, "parser_used", None),
    )
    return chunker
