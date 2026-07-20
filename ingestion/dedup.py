"""
Deduplication and quality filtering for normalized chunks.

filter_chunks() applies a pipeline of filters in order:
  1. Empty text filter
  2. Exact dedup (SHA-256 of normalized text)
  3. Near-dedup (MinHash LSH, threshold 0.85)
  4. Word count minimum (< MIN_CHUNK_WORD_COUNT)
  5. Fragmentation (newline ratio > 0.30)
  6. Noise ratio (non-alphanumeric / total > 0.90)
  7. Language confidence filter

Exempt from filters (never removed):
  - is_table == True
  - element_type == "heading"

Each filter logs how many chunks it removed and why.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_text(text: str) -> str:
    """Normalize text for dedup comparison: lowercase, collapse whitespace."""
    return re.sub(r"\s+", " ", text.lower()).strip()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _word_count(text: str) -> int:
    return len(text.split())


def _newline_ratio(text: str) -> float:
    if not text:
        return 0.0
    return text.count("\n") / len(text)


def _non_alphanum_ratio(text: str) -> float:
    if not text:
        return 1.0
    non_alphanum = sum(
        1
        for ch in text
        if not (ch.isalnum() or ch.isspace())
    )
    return non_alphanum / len(text)


def _is_protected(chunk: dict) -> bool:
    """Return True for chunks that must never be filtered out."""
    return chunk.get("is_table", False) or chunk.get("element_type") == "heading"


# ---------------------------------------------------------------------------
# MinHash near-dedup (optional — graceful fallback if datasketch not installed)
# ---------------------------------------------------------------------------

class _MinHashIndex:
    """Wrapper around datasketch MinHashLSH for near-dedup."""

    def __init__(self, threshold: float = 0.85, num_perm: int = 128):
        self.threshold = threshold
        self.num_perm = num_perm
        self._lsh = None
        self._available = False
        self._init()

    def _init(self):
        try:
            from datasketch import MinHash, MinHashLSH  # type: ignore

            self._lsh = MinHashLSH(threshold=self.threshold, num_perm=self.num_perm)
            self._MinHash = MinHash
            self._available = True
        except ImportError:
            logger.warning(
                "datasketch_not_installed",
                message="Near-dedup disabled. Install datasketch to enable.",
            )

    def is_near_dup(self, chunk_id: str, text: str) -> bool:
        """Return True if `text` is a near-duplicate of something already indexed."""
        if not self._available or self._lsh is None:
            return False
        try:
            m = self._MinHash(num_perm=self.num_perm)
            for word in _normalize_text(text).split():
                m.update(word.encode("utf-8"))
            results = self._lsh.query(m)
            if results:
                return True
            # Not a dup — add to index
            self._lsh.insert(chunk_id, m)
            return False
        except Exception as exc:
            logger.warning("minhash_error", error=str(exc))
            return False


# ---------------------------------------------------------------------------
# Individual filter functions
# ---------------------------------------------------------------------------

def _filter_empty(chunks: list[dict]) -> tuple[list[dict], int]:
    kept = [c for c in chunks if c.get("text", "").strip() or _is_protected(c)]
    return kept, len(chunks) - len(kept)


def _filter_exact_dedup(chunks: list[dict]) -> tuple[list[dict], int]:
    seen: set[str] = set()
    kept: list[dict] = []
    removed = 0
    for chunk in chunks:
        if _is_protected(chunk):
            kept.append(chunk)
            continue
        h = _sha256(_normalize_text(chunk.get("text", "")))
        if h in seen:
            removed += 1
        else:
            seen.add(h)
            kept.append(chunk)
    return kept, removed


def _filter_near_dedup(
    chunks: list[dict], threshold: float
) -> tuple[list[dict], int]:
    index = _MinHashIndex(threshold=threshold)
    if not index._available:
        return chunks, 0

    kept: list[dict] = []
    removed = 0
    for chunk in chunks:
        if _is_protected(chunk):
            kept.append(chunk)
            continue
        text = chunk.get("text", "")
        chunk_id = chunk.get("chunk_id", _sha256(text))
        if index.is_near_dup(chunk_id, text):
            removed += 1
        else:
            kept.append(chunk)
    return kept, removed


def _filter_word_count(
    chunks: list[dict], min_words: int
) -> tuple[list[dict], int]:
    kept: list[dict] = []
    removed = 0
    for chunk in chunks:
        if _is_protected(chunk):
            kept.append(chunk)
            continue
        if _word_count(chunk.get("text", "")) < min_words:
            removed += 1
        else:
            kept.append(chunk)
    return kept, removed


def _filter_fragmentation(
    chunks: list[dict], max_ratio: float = 0.30
) -> tuple[list[dict], int]:
    kept: list[dict] = []
    removed = 0
    for chunk in chunks:
        if _is_protected(chunk):
            kept.append(chunk)
            continue
        if _newline_ratio(chunk.get("text", "")) > max_ratio:
            removed += 1
        else:
            kept.append(chunk)
    return kept, removed


def _filter_noise(
    chunks: list[dict], max_ratio: float = 0.90
) -> tuple[list[dict], int]:
    kept: list[dict] = []
    removed = 0
    for chunk in chunks:
        if _is_protected(chunk):
            kept.append(chunk)
            continue
        if _non_alphanum_ratio(chunk.get("text", "")) > max_ratio:
            removed += 1
        else:
            kept.append(chunk)
    return kept, removed


def _filter_language_confidence(
    chunks: list[dict], min_confidence: float = 0.5
) -> tuple[list[dict], int]:
    """
    Remove chunks where language is 'unknown' AND confidence is very low (or None).
    This catches completely garbled OCR output that lingua couldn't classify.
    """
    kept: list[dict] = []
    removed = 0
    for chunk in chunks:
        if _is_protected(chunk):
            kept.append(chunk)
            continue
        lang = chunk.get("language", "unknown")
        conf = chunk.get("language_confidence")
        # Only remove if explicitly unknown AND confidence is absent or very low
        if lang == "unknown" and (conf is None or conf < min_confidence):
            removed += 1
        else:
            kept.append(chunk)
    return kept, removed


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def filter_chunks(
    chunks: list[dict],
    *,
    dedup_threshold: float | None = None,
    min_word_count: int | None = None,
) -> list[dict]:
    """
    Apply the full deduplication and quality filter pipeline.

    Args:
        chunks:           Normalized chunk dicts from normalizer.py.
        dedup_threshold:  Near-dedup MinHash similarity threshold (0-1).
                          Defaults to settings.DEDUP_SIMILARITY_THRESHOLD.
        min_word_count:   Minimum word count per chunk.
                          Defaults to settings.MIN_CHUNK_WORD_COUNT.

    Returns:
        Filtered list of chunk dicts (order preserved, chunk_index NOT updated —
        call normalizer again or update manually if needed).
    """
    from ingestion.config import get_settings

    settings = get_settings()
    if dedup_threshold is None:
        dedup_threshold = settings.DEDUP_SIMILARITY_THRESHOLD
    if min_word_count is None:
        min_word_count = settings.MIN_CHUNK_WORD_COUNT

    original_count = len(chunks)
    doc_id = chunks[0].get("doc_id", "unknown") if chunks else "unknown"

    stages = [
        ("empty", lambda c: _filter_empty(c)),
        ("exact_dedup", lambda c: _filter_exact_dedup(c)),
        ("near_dedup", lambda c: _filter_near_dedup(c, dedup_threshold)),
        ("word_count", lambda c: _filter_word_count(c, min_word_count)),
        ("fragmentation", lambda c: _filter_fragmentation(c, 0.30)),
        ("noise", lambda c: _filter_noise(c, 0.90)),
        ("language_confidence", lambda c: _filter_language_confidence(c)),
    ]

    current = chunks
    total_removed = 0

    for stage_name, stage_fn in stages:
        current, removed = stage_fn(current)
        if removed > 0:
            logger.info(
                "dedup_stage",
                doc_id=doc_id,
                stage=stage_name,
                removed=removed,
                remaining=len(current),
            )
        total_removed += removed

    # Update chunk indices to reflect new order
    for i, chunk in enumerate(current):
        chunk["chunk_index"] = i
        chunk["total_chunks"] = len(current)

    logger.info(
        "dedup_complete",
        doc_id=doc_id,
        original=original_count,
        kept=len(current),
        removed=total_removed,
    )

    return current
