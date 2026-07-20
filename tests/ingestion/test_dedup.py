"""
Tests for the deduplication and quality filter pipeline (ingestion/dedup.py).

Verifies each filter stage independently and the full pipeline:
- Empty filter
- Exact dedup (SHA-256)
- Near dedup (MinHash LSH — skipped if datasketch not installed)
- Word count minimum
- Fragmentation ratio
- Noise ratio
- Language confidence filter

Special cases:
- Tables are NEVER removed (is_table=True)
- Headings are NEVER removed (element_type="heading")
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from ingestion.dedup import (
    filter_chunks,
    _filter_empty,
    _filter_exact_dedup,
    _filter_word_count,
    _filter_fragmentation,
    _filter_noise,
    _filter_language_confidence,
    _filter_near_dedup,
    _normalize_text,
    _sha256,
    _word_count,
    _newline_ratio,
    _non_alphanum_ratio,
    _is_protected,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_chunk(
    text: str,
    element_type: str = "text",
    is_table: bool = False,
    language: str = "en",
    language_confidence: float | None = 0.95,
    chunk_id: str | None = None,
) -> dict:
    return {
        "chunk_id": chunk_id or str(uuid.uuid4()),
        "doc_id": "test-doc-id",
        "text": text,
        "element_type": element_type,
        "is_table": is_table,
        "language": language,
        "language_confidence": language_confidence,
        "chunk_index": 0,
        "total_chunks": 1,
    }


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

class TestUtilities:
    def test_normalize_text(self):
        assert _normalize_text("  Hello   World  ") == "hello world"
        assert _normalize_text("A\n\nB") == "a b"

    def test_sha256_deterministic(self):
        h1 = _sha256("hello")
        h2 = _sha256("hello")
        assert h1 == h2
        assert len(h1) == 64

    def test_sha256_different_inputs(self):
        assert _sha256("hello") != _sha256("world")

    def test_word_count(self):
        assert _word_count("hello world") == 2
        assert _word_count("") == 0
        assert _word_count("one") == 1
        assert _word_count("  spaces  ") == 1

    def test_newline_ratio_zero(self):
        assert _newline_ratio("no newlines here") == 0.0

    def test_newline_ratio_high(self):
        text = "a\nb\nc\nd\ne"  # 4 newlines / 9 chars ≈ 0.44
        assert _newline_ratio(text) > 0.30

    def test_newline_ratio_empty(self):
        assert _newline_ratio("") == 0.0

    def test_non_alphanum_ratio_clean(self):
        ratio = _non_alphanum_ratio("hello world")
        # Only space is non-alphanumeric and non-space? Wait: space IS whitespace.
        # Our function: non-alphanumeric chars (including spaces)
        # Actually the function counts non-alnum OR whitespace as "bad"
        # Let's just verify the ratio is low for clean text
        assert ratio < 0.30

    def test_non_alphanum_ratio_garbage(self):
        ratio = _non_alphanum_ratio("@#$%^&*!@#$%")
        assert ratio > 0.90

    def test_is_protected_table(self):
        chunk = _make_chunk("data", is_table=True)
        assert _is_protected(chunk) is True

    def test_is_protected_heading(self):
        chunk = _make_chunk("Title", element_type="heading")
        assert _is_protected(chunk) is True

    def test_is_protected_regular_text(self):
        chunk = _make_chunk("Regular text content.")
        assert _is_protected(chunk) is False


# ---------------------------------------------------------------------------
# Empty filter
# ---------------------------------------------------------------------------

class TestFilterEmpty:
    def test_removes_empty_text(self):
        chunks = [
            _make_chunk(""),
            _make_chunk("   "),
            _make_chunk("\n\t"),
            _make_chunk("Valid content."),
        ]
        kept, removed = _filter_empty(chunks)
        assert removed == 3
        assert len(kept) == 1
        assert kept[0]["text"] == "Valid content."

    def test_protected_empty_table_kept(self):
        chunks = [_make_chunk("", is_table=True)]
        kept, removed = _filter_empty(chunks)
        assert removed == 0
        assert len(kept) == 1

    def test_protected_empty_heading_kept(self):
        chunks = [_make_chunk("", element_type="heading")]
        kept, removed = _filter_empty(chunks)
        assert removed == 0

    def test_no_empty_chunks(self):
        chunks = [_make_chunk("text one"), _make_chunk("text two")]
        kept, removed = _filter_empty(chunks)
        assert removed == 0
        assert len(kept) == 2


# ---------------------------------------------------------------------------
# Exact dedup
# ---------------------------------------------------------------------------

class TestFilterExactDedup:
    def test_duplicate_removed(self):
        text = "This exact text appears twice in the document."
        chunks = [_make_chunk(text), _make_chunk(text)]
        kept, removed = _filter_exact_dedup(chunks)
        assert removed == 1
        assert len(kept) == 1

    def test_different_texts_both_kept(self):
        chunks = [
            _make_chunk("First unique text content."),
            _make_chunk("Second unique text content."),
        ]
        kept, removed = _filter_exact_dedup(chunks)
        assert removed == 0
        assert len(kept) == 2

    def test_case_insensitive_dedup(self):
        chunks = [
            _make_chunk("Hello World text."),
            _make_chunk("hello world text."),  # same after normalize
        ]
        kept, removed = _filter_exact_dedup(chunks)
        assert removed == 1

    def test_whitespace_normalized_dedup(self):
        chunks = [
            _make_chunk("Text with   spaces."),
            _make_chunk("text with spaces."),  # same after normalize
        ]
        kept, removed = _filter_exact_dedup(chunks)
        assert removed == 1

    def test_table_duplicate_kept(self):
        """Tables must never be removed even if exact duplicate."""
        text = "Col1 | Col2"
        chunks = [
            _make_chunk(text, is_table=True),
            _make_chunk(text, is_table=True),
        ]
        kept, removed = _filter_exact_dedup(chunks)
        assert removed == 0
        assert len(kept) == 2

    def test_triple_duplicate_two_removed(self):
        text = "Same text three times."
        chunks = [_make_chunk(text), _make_chunk(text), _make_chunk(text)]
        kept, removed = _filter_exact_dedup(chunks)
        assert removed == 2
        assert len(kept) == 1


# ---------------------------------------------------------------------------
# Word count filter
# ---------------------------------------------------------------------------

class TestFilterWordCount:
    def test_below_minimum_removed(self):
        chunks = [_make_chunk("too short")]  # 2 words
        kept, removed = _filter_word_count(chunks, min_words=8)
        assert removed == 1
        assert len(kept) == 0

    def test_at_minimum_kept(self):
        chunks = [_make_chunk("one two three four five six seven eight")]  # 8 words
        kept, removed = _filter_word_count(chunks, min_words=8)
        assert removed == 0
        assert len(kept) == 1

    def test_above_minimum_kept(self):
        text = "This sentence has more than eight words total for testing."
        chunks = [_make_chunk(text)]
        kept, removed = _filter_word_count(chunks, min_words=8)
        assert removed == 0

    def test_table_always_kept(self):
        """Short table is never removed by word count filter."""
        chunks = [_make_chunk("A | B", is_table=True)]
        kept, removed = _filter_word_count(chunks, min_words=100)
        assert removed == 0
        assert len(kept) == 1

    def test_heading_always_kept(self):
        chunks = [_make_chunk("Title", element_type="heading")]
        kept, removed = _filter_word_count(chunks, min_words=100)
        assert removed == 0


# ---------------------------------------------------------------------------
# Fragmentation filter
# ---------------------------------------------------------------------------

class TestFilterFragmentation:
    def test_high_newline_ratio_removed(self):
        # text = "a\nb\nc\nd\ne\nf\ng\nh" — 7 newlines / 15 chars ≈ 0.47
        text = "a\nb\nc\nd\ne\nf\ng"
        chunks = [_make_chunk(text)]
        kept, removed = _filter_fragmentation(chunks, max_ratio=0.30)
        assert removed == 1

    def test_low_newline_ratio_kept(self):
        text = "Normal paragraph with one line\nand a continuation."
        chunks = [_make_chunk(text)]
        kept, removed = _filter_fragmentation(chunks, max_ratio=0.30)
        assert removed == 0

    def test_table_with_high_newlines_kept(self):
        text = "Row1\nRow2\nRow3\nRow4\nRow5\nRow6\nRow7\nRow8"
        chunks = [_make_chunk(text, is_table=True)]
        kept, removed = _filter_fragmentation(chunks, max_ratio=0.30)
        assert removed == 0


# ---------------------------------------------------------------------------
# Noise filter
# ---------------------------------------------------------------------------

class TestFilterNoise:
    def test_high_noise_removed(self):
        # All special characters
        text = "@#$%^&*!@#$%^&*!@#$%^&*!@#$"
        chunks = [_make_chunk(text)]
        kept, removed = _filter_noise(chunks, max_ratio=0.90)
        assert removed == 1

    def test_clean_text_kept(self):
        text = "This is a clean paragraph of text."
        chunks = [_make_chunk(text)]
        kept, removed = _filter_noise(chunks, max_ratio=0.90)
        assert removed == 0

    def test_table_with_noise_kept(self):
        text = "@@@###$$$"
        chunks = [_make_chunk(text, is_table=True)]
        kept, removed = _filter_noise(chunks, max_ratio=0.90)
        assert removed == 0

    def test_rtl_text_not_noise(self):
        persian = "سلام دنیا این متن فارسی است"
        chunks = [_make_chunk(persian)]
        kept, removed = _filter_noise(chunks, max_ratio=0.90)
        assert removed == 0


# ---------------------------------------------------------------------------
# Language confidence filter
# ---------------------------------------------------------------------------

class TestFilterLanguageConfidence:
    def test_unknown_language_no_confidence_removed(self):
        chunks = [_make_chunk("garbled text", language="unknown", language_confidence=None)]
        kept, removed = _filter_language_confidence(chunks)
        assert removed == 1

    def test_unknown_language_low_confidence_removed(self):
        chunks = [_make_chunk("garbled text", language="unknown", language_confidence=0.3)]
        kept, removed = _filter_language_confidence(chunks, min_confidence=0.5)
        assert removed == 1

    def test_unknown_language_high_confidence_kept(self):
        # Unknown lang but relatively high confidence = keep
        chunks = [_make_chunk("text", language="unknown", language_confidence=0.8)]
        kept, removed = _filter_language_confidence(chunks, min_confidence=0.5)
        assert removed == 0

    def test_known_language_always_kept(self):
        chunks = [_make_chunk("text", language="en", language_confidence=None)]
        kept, removed = _filter_language_confidence(chunks)
        assert removed == 0

    def test_persian_unknown_confidence_kept_if_lang_set(self):
        # language="fa" is known, so even without confidence it stays
        chunks = [_make_chunk("فارسی", language="fa", language_confidence=None)]
        kept, removed = _filter_language_confidence(chunks)
        assert removed == 0

    def test_table_with_unknown_language_kept(self):
        chunks = [_make_chunk("data", language="unknown", language_confidence=None, is_table=True)]
        kept, removed = _filter_language_confidence(chunks)
        assert removed == 0


# ---------------------------------------------------------------------------
# Near dedup
# ---------------------------------------------------------------------------

class TestFilterNearDedup:
    def test_near_dedup_skips_when_datasketch_absent(self):
        """If datasketch is not installed, near-dedup is a no-op."""
        with patch("ingestion.dedup._MinHashIndex._init") as mock_init:
            # Simulate datasketch unavailable
            def fake_init(self):
                self._available = False
                self._lsh = None

            mock_init.side_effect = fake_init
            chunks = [
                _make_chunk("similar text one here"),
                _make_chunk("similar text two here"),
            ]
            kept, removed = _filter_near_dedup(chunks, threshold=0.85)
            # No-op when unavailable
            assert len(kept) == 2
            assert removed == 0

    def test_tables_not_near_deduped(self):
        """Tables bypass near-dedup regardless of MinHash state."""
        chunks = [
            _make_chunk("similar data row", is_table=True),
            _make_chunk("similar data row", is_table=True),
        ]
        kept, removed = _filter_near_dedup(chunks, threshold=0.85)
        # Both tables kept
        assert len(kept) == 2


# ---------------------------------------------------------------------------
# Full pipeline — filter_chunks()
# ---------------------------------------------------------------------------

class TestFilterChunks:
    def test_empty_input(self):
        result = filter_chunks([])
        assert result == []

    def test_valid_chunks_all_kept(self):
        chunks = [
            _make_chunk("First valid paragraph with enough words for the filter."),
            _make_chunk("Second valid paragraph with enough words for the filter."),
        ]
        with patch("ingestion.dedup.get_settings") as mock_settings:
            mock_settings.return_value.DEDUP_SIMILARITY_THRESHOLD = 0.85
            mock_settings.return_value.MIN_CHUNK_WORD_COUNT = 8
            result = filter_chunks(chunks)
        assert len(result) == 2

    def test_indices_updated_after_filtering(self):
        chunks = [
            _make_chunk(""),  # will be removed
            _make_chunk("Valid first chunk with sufficient words here."),
            _make_chunk("Valid second chunk with sufficient words here."),
        ]
        with patch("ingestion.dedup.get_settings") as mock_settings:
            mock_settings.return_value.DEDUP_SIMILARITY_THRESHOLD = 0.85
            mock_settings.return_value.MIN_CHUNK_WORD_COUNT = 3
            result = filter_chunks(chunks)
        assert result[0]["chunk_index"] == 0
        assert result[1]["chunk_index"] == 1
        assert all(c["total_chunks"] == 2 for c in result)

    def test_table_survives_all_filters(self):
        """A table with empty text, short content, and high noise must survive."""
        table_chunk = _make_chunk(
            text="A | B",
            element_type="table",
            is_table=True,
            language="unknown",
            language_confidence=None,
        )
        with patch("ingestion.dedup.get_settings") as mock_settings:
            mock_settings.return_value.DEDUP_SIMILARITY_THRESHOLD = 0.85
            mock_settings.return_value.MIN_CHUNK_WORD_COUNT = 100  # very high threshold
            result = filter_chunks([table_chunk])
        assert len(result) == 1
        assert result[0]["is_table"] is True

    def test_heading_survives_all_filters(self):
        heading = _make_chunk(
            text="Title",  # too short for word count
            element_type="heading",
            language="unknown",
            language_confidence=None,
        )
        with patch("ingestion.dedup.get_settings") as mock_settings:
            mock_settings.return_value.DEDUP_SIMILARITY_THRESHOLD = 0.85
            mock_settings.return_value.MIN_CHUNK_WORD_COUNT = 100
            result = filter_chunks([heading])
        assert len(result) == 1

    def test_garbled_ocr_removed(self):
        """Chunk with noise + unknown language + short word count should be removed."""
        bad_chunk = _make_chunk(
            text="@@@###",
            language="unknown",
            language_confidence=0.1,
        )
        with patch("ingestion.dedup.get_settings") as mock_settings:
            mock_settings.return_value.DEDUP_SIMILARITY_THRESHOLD = 0.85
            mock_settings.return_value.MIN_CHUNK_WORD_COUNT = 8
            result = filter_chunks([bad_chunk])
        assert len(result) == 0

    def test_exact_duplicate_removed_pipeline(self):
        text = "This is a long enough paragraph for all filters to pass easily."
        chunks = [_make_chunk(text), _make_chunk(text)]
        with patch("ingestion.dedup.get_settings") as mock_settings:
            mock_settings.return_value.DEDUP_SIMILARITY_THRESHOLD = 0.85
            mock_settings.return_value.MIN_CHUNK_WORD_COUNT = 8
            result = filter_chunks(chunks)
        assert len(result) == 1

    def test_mixed_batch_partial_removal(self):
        chunks = [
            _make_chunk("Valid content paragraph with enough words to pass."),
            _make_chunk(""),  # empty → removed
            _make_chunk("short"),  # word count → removed (below 8)
            _make_chunk("Another valid paragraph with sufficient word count here.", language="fa"),
        ]
        with patch("ingestion.dedup.get_settings") as mock_settings:
            mock_settings.return_value.DEDUP_SIMILARITY_THRESHOLD = 0.85
            mock_settings.return_value.MIN_CHUNK_WORD_COUNT = 8
            result = filter_chunks(chunks)
        assert len(result) == 2

    def test_settings_used_from_config(self):
        """filter_chunks reads MIN_CHUNK_WORD_COUNT from settings when not overridden."""
        chunks = [
            _make_chunk("one two three"),  # 3 words
            _make_chunk("long enough paragraph with eight words total here"),
        ]
        with patch("ingestion.dedup.get_settings") as mock_settings:
            mock_settings.return_value.DEDUP_SIMILARITY_THRESHOLD = 0.85
            mock_settings.return_value.MIN_CHUNK_WORD_COUNT = 5  # 3-word chunk passes
            result = filter_chunks(chunks)
        assert len(result) == 2  # Both pass when min=5

    def test_explicit_override_respected(self):
        """Explicit min_word_count overrides settings."""
        chunks = [_make_chunk("only three words")]  # 3 words
        with patch("ingestion.dedup.get_settings") as mock_settings:
            mock_settings.return_value.DEDUP_SIMILARITY_THRESHOLD = 0.85
            mock_settings.return_value.MIN_CHUNK_WORD_COUNT = 3
            result = filter_chunks(chunks, min_word_count=10)
        assert len(result) == 0  # Overridden to require 10 words
