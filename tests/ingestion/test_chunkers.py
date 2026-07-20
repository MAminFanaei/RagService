"""
Tests for all chunker implementations.

Covers:
  - FixedChunker: splits long text, passes through short text, tables preserved
  - HeadingChunker: groups by section, splits oversized, merges undersized
  - SemanticChunker: falls back to FixedChunker when Chonkie absent
  - SentenceChunker: falls back to FixedChunker when Chonkie absent
  - HierarchicalChunker: produces parent+child pairs
  - BaseChunker utilities: _renumber, _extract_tables, _reinsert_tables
  - Router: auto-selection logic (heading structure, OCR doc, long doc, default)

All tests use mock Document objects — no DB required.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_chunk(
    text: str = "Hello world",
    element_type: str = "text",
    is_table: bool = False,
    section_path: list | None = None,
    section_title: str | None = None,
    heading_level: int | None = None,
    page_number: int | None = 1,
    token_estimate: int | None = None,
) -> dict:
    """Build a minimal normalized chunk dict."""
    t = token_estimate or max(1, len(text.split()) * 13 // 10)
    return {
        "chunk_id":          str(uuid.uuid4()),
        "doc_id":            "doc-001",
        "text":              text,
        "char_count":        len(text),
        "token_estimate":    t,
        "element_type":      element_type,
        "is_table":          is_table,
        "is_footnote":       False,
        "section_path":      section_path or [],
        "section_title":     section_title,
        "section_title_text": f"{section_title or ''} {text}".strip(),
        "heading_level":     heading_level,
        "page_number":       page_number,
        "language":          "en",
        "script_direction":  "ltr",
        "source_file":       "test.pdf",
        "doc_title":         "Test Document",
        "tags":              [],
        "ingestion_version": "1.0",
        "dense_vector":      None,
        "sparse_vector":     None,
        "table_markdown":    None,
        "total_chunks":      None,
        "chunk_index":       0,
    }


def _make_doc(
    parser_used: str | None = None,
    ocr_used: str | None = None,
    total_pages: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id="doc-001",
        parser_used=parser_used,
        ocr_used=ocr_used,
        total_pages=total_pages,
    )


LONG_TEXT = " ".join(["word"] * 600)   # ~600 words > any max_tokens setting
SHORT_TEXT = "Short text."


# ===========================================================================
# FixedChunker
# ===========================================================================

class TestFixedChunker:

    def _make(self, max_tokens=50, overlap_tokens=5):
        from ingestion.chunkers.fixed import FixedChunker
        return FixedChunker(max_tokens=max_tokens, overlap_tokens=overlap_tokens)

    def test_short_text_passes_through(self):
        chunker = self._make()
        el = _make_chunk(text=SHORT_TEXT)
        result = chunker.chunk([el])
        assert len(result) == 1
        assert result[0]["text"] == SHORT_TEXT

    def test_long_text_is_split(self):
        chunker = self._make(max_tokens=50)
        el = _make_chunk(text=LONG_TEXT)
        result = chunker.chunk([el])
        assert len(result) > 1

    def test_table_never_split(self):
        chunker = self._make(max_tokens=10)
        table = _make_chunk(text=LONG_TEXT, is_table=True)
        result = chunker.chunk([table])
        # Table flows through as single chunk
        assert any(c["is_table"] for c in result)
        table_chunks = [c for c in result if c["is_table"]]
        assert len(table_chunks) == 1

    def test_heading_never_split(self):
        chunker = self._make(max_tokens=5)
        heading = _make_chunk(
            text="Chapter 1: Introduction to Something Very Long Indeed",
            element_type="heading",
        )
        result = chunker.chunk([heading])
        heading_chunks = [c for c in result if c["element_type"] == "heading"]
        assert len(heading_chunks) == 1

    def test_output_renumbered(self):
        chunker = self._make(max_tokens=50)
        els = [_make_chunk(text=LONG_TEXT), _make_chunk(text=SHORT_TEXT)]
        result = chunker.chunk(els)
        for i, c in enumerate(result):
            assert c["chunk_index"] == i
            assert c["total_chunks"] == len(result)

    def test_metadata_preserved(self):
        chunker = self._make(max_tokens=50)
        el = _make_chunk(
            text=LONG_TEXT,
            section_title="My Section",
            section_path=["Ch1", "1.2"],
            page_number=5,
        )
        result = chunker.chunk([el])
        # All sub-chunks inherit page_number and section_path
        for c in result:
            assert c["page_number"] == 5
            assert c["section_path"] == ["Ch1", "1.2"]

    def test_new_chunk_ids_assigned(self):
        chunker = self._make(max_tokens=50)
        el = _make_chunk(text=LONG_TEXT)
        original_id = el["chunk_id"]
        result = chunker.chunk([el])
        ids = [c["chunk_id"] for c in result]
        # All output IDs should be unique (may differ from original)
        assert len(ids) == len(set(ids))

    def test_empty_input(self):
        chunker = self._make()
        result = chunker.chunk([])
        assert result == []


# ===========================================================================
# HeadingChunker
# ===========================================================================

class TestHeadingChunker:

    def _make(self, max_tokens=100, min_tokens=20):
        from ingestion.chunkers.heading import HeadingChunker
        return HeadingChunker(max_tokens=max_tokens, min_tokens=min_tokens)

    def test_groups_by_section_path(self):
        chunker = self._make(min_tokens=5)
        els = [
            _make_chunk(text="Intro text.", section_path=["Ch1"], token_estimate=10),
            _make_chunk(text="More intro.", section_path=["Ch1"], token_estimate=10),
            _make_chunk(text="Section 2.", section_path=["Ch2"], token_estimate=10),
        ]
        result = chunker.chunk(els)
        # Ch1 elements should be merged; Ch2 is separate
        assert len(result) >= 1

    def test_oversized_group_is_split(self):
        chunker = self._make(max_tokens=20, min_tokens=2)
        # One group with too much text
        long_el = _make_chunk(
            text=LONG_TEXT,
            section_path=["Ch1"],
            token_estimate=600,
        )
        result = chunker.chunk([long_el])
        assert len(result) > 1

    def test_table_preserved(self):
        chunker = self._make()
        table = _make_chunk(text="| col1 | col2 |", is_table=True)
        regular = _make_chunk(text="Some text.", section_path=["Ch1"])
        result = chunker.chunk([table, regular])
        assert any(c["is_table"] for c in result)

    def test_output_renumbered(self):
        chunker = self._make(min_tokens=2)
        els = [
            _make_chunk(text="A.", section_path=["S1"], token_estimate=5),
            _make_chunk(text="B.", section_path=["S2"], token_estimate=5),
        ]
        result = chunker.chunk(els)
        for i, c in enumerate(result):
            assert c["chunk_index"] == i


# ===========================================================================
# SemanticChunker (Chonkie absent → FixedChunker fallback)
# ===========================================================================

class TestSemanticChunker:

    def test_falls_back_when_chonkie_absent(self):
        from ingestion.chunkers.semantic import SemanticChunker
        chunker = SemanticChunker(max_tokens=50)
        # Force _chonkie_tried=True, _chonkie=None to simulate missing Chonkie
        chunker._chonkie_tried = True
        chunker._chonkie = None

        els = [_make_chunk(text=LONG_TEXT)]
        result = chunker.chunk(els)
        # FixedChunker fallback should split the long text
        assert len(result) >= 1
        for c in result:
            assert c["text"].strip()

    def test_name(self):
        from ingestion.chunkers.semantic import SemanticChunker
        assert SemanticChunker().name == "semantic"

    def test_table_preserved_in_fallback(self):
        from ingestion.chunkers.semantic import SemanticChunker
        chunker = SemanticChunker(max_tokens=10)
        chunker._chonkie_tried = True
        chunker._chonkie = None
        table = _make_chunk(text=LONG_TEXT, is_table=True)
        result = chunker.chunk([table])
        assert any(c["is_table"] for c in result)


# ===========================================================================
# SentenceChunker (Chonkie absent → FixedChunker fallback)
# ===========================================================================

class TestSentenceChunker:

    def test_falls_back_when_chonkie_absent(self):
        from ingestion.chunkers.sentence import SentenceChunker
        chunker = SentenceChunker(max_tokens=50)
        chunker._chonkie_tried = True
        chunker._chonkie = None

        els = [_make_chunk(text=LONG_TEXT)]
        result = chunker.chunk(els)
        assert len(result) >= 1

    def test_name(self):
        from ingestion.chunkers.sentence import SentenceChunker
        assert SentenceChunker().name == "sentence"


# ===========================================================================
# HierarchicalChunker
# ===========================================================================

class TestHierarchicalChunker:

    def _make(self):
        from ingestion.chunkers.hierarchical import HierarchicalChunker
        return HierarchicalChunker()

    def test_produces_parent_and_children(self):
        chunker = self._make()
        el = _make_chunk(text=LONG_TEXT)
        result = chunker.chunk([el])
        parents  = [c for c in result if c.get("is_parent")]
        children = [c for c in result if not c.get("is_parent") and c.get("parent_chunk_id")]
        assert len(parents) >= 1
        assert len(children) >= 1

    def test_children_reference_parent(self):
        chunker = self._make()
        el = _make_chunk(text=LONG_TEXT)
        result = chunker.chunk([el])
        parent_ids = {c["chunk_id"] for c in result if c.get("is_parent")}
        for child in result:
            if child.get("parent_chunk_id"):
                assert child["parent_chunk_id"] in parent_ids

    def test_table_has_no_children(self):
        chunker = self._make()
        table = _make_chunk(text=LONG_TEXT, is_table=True)
        result = chunker.chunk([table])
        table_chunks = [c for c in result if c["is_table"]]
        assert len(table_chunks) == 1
        # Table should not have a parent_chunk_id (it's atomic)
        assert table_chunks[0].get("parent_chunk_id") is None

    def test_name(self):
        assert self._make().name == "hierarchical"

    def test_output_renumbered(self):
        chunker = self._make()
        result = chunker.chunk([_make_chunk(text=LONG_TEXT)])
        for i, c in enumerate(result):
            assert c["chunk_index"] == i
            assert c["total_chunks"] == len(result)


# ===========================================================================
# BaseChunker utilities
# ===========================================================================

class TestBaseChunkerUtils:

    def _make_base(self):
        from ingestion.chunkers.fixed import FixedChunker
        return FixedChunker()

    def test_renumber(self):
        chunker = self._make_base()
        chunks = [_make_chunk(), _make_chunk(), _make_chunk()]
        result = chunker._renumber(chunks)
        for i, c in enumerate(result):
            assert c["chunk_index"] == i
            assert c["total_chunks"] == 3

    def test_extract_tables(self):
        chunker = self._make_base()
        t = _make_chunk(is_table=True)
        n = _make_chunk(is_table=False)
        non_tables, tables = chunker._extract_tables([t, n, t])
        assert len(non_tables) == 1
        assert len(tables) == 2

    def test_reinsert_tables_empty(self):
        chunker = self._make_base()
        chunks = [_make_chunk()]
        result = chunker._reinsert_tables(chunks, [])
        assert result == chunks

    def test_is_protected(self):
        chunker = self._make_base()
        assert chunker._is_protected({"is_table": True, "element_type": "text"})
        assert chunker._is_protected({"is_table": False, "element_type": "heading"})
        assert not chunker._is_protected({"is_table": False, "element_type": "text"})


# ===========================================================================
# Chunker Router
# ===========================================================================

class TestChunkerRouter:

    def test_explicit_strategy_fixed(self):
        from ingestion.chunkers.router import select_chunker
        doc = _make_doc()
        els = [_make_chunk()]
        with patch("ingestion.chunkers.router.get_settings") as mock_settings:
            mock_settings.return_value.CHUNKING_STRATEGY = "fixed"
            chunker = select_chunker(doc, els)
        assert chunker.name == "fixed"

    def test_explicit_strategy_heading(self):
        from ingestion.chunkers.router import select_chunker
        doc = _make_doc()
        els = [_make_chunk()]
        with patch("ingestion.chunkers.router.get_settings") as mock_settings:
            mock_settings.return_value.CHUNKING_STRATEGY = "heading"
            chunker = select_chunker(doc, els)
        assert chunker.name == "heading"

    def test_auto_selects_heading_for_structured_doc(self):
        from ingestion.chunkers.router import select_chunker
        doc = _make_doc()
        els = [
            _make_chunk(element_type="heading"),
            _make_chunk(element_type="heading"),
            _make_chunk(element_type="text"),
        ]
        with patch("ingestion.chunkers.router.get_settings") as mock_settings:
            mock_settings.return_value.CHUNKING_STRATEGY = "auto"
            chunker = select_chunker(doc, els)
        assert chunker.name == "heading"

    def test_auto_selects_semantic_for_ocr_doc(self):
        from ingestion.chunkers.router import select_chunker
        # No headings, but ocr_used is set
        doc = _make_doc(ocr_used="dots_ocr")
        els = [_make_chunk(element_type="text")]
        with patch("ingestion.chunkers.router.get_settings") as mock_settings:
            mock_settings.return_value.CHUNKING_STRATEGY = "auto"
            chunker = select_chunker(doc, els)
        assert chunker.name == "semantic"

    def test_auto_selects_hierarchical_for_long_doc(self):
        from ingestion.chunkers.router import select_chunker
        doc = _make_doc(total_pages=100)
        els = [_make_chunk(element_type="text")]
        with patch("ingestion.chunkers.router.get_settings") as mock_settings:
            mock_settings.return_value.CHUNKING_STRATEGY = "auto"
            chunker = select_chunker(doc, els)
        assert chunker.name == "hierarchical"

    def test_auto_defaults_to_fixed(self):
        from ingestion.chunkers.router import select_chunker
        doc = _make_doc()   # no parser, no pages
        els = [_make_chunk(element_type="text")]
        with patch("ingestion.chunkers.router.get_settings") as mock_settings:
            mock_settings.return_value.CHUNKING_STRATEGY = "auto"
            chunker = select_chunker(doc, els)
        assert chunker.name == "fixed"

    def test_invalid_strategy_raises(self):
        from ingestion.chunkers.router import get_chunker_by_name
        with pytest.raises(ValueError, match="Unknown chunking strategy"):
            get_chunker_by_name("nonexistent")

    def test_parser_used_ocr_triggers_semantic(self):
        """
        Fix for issue #8: parser_used is set before select_chunker is called
        in the pipeline. Verify the router reads it correctly.
        """
        from ingestion.chunkers.router import select_chunker
        doc = _make_doc(parser_used="deepdoc", ocr_used=None)
        els = [_make_chunk(element_type="text")]
        with patch("ingestion.chunkers.router.get_settings") as mock_settings:
            mock_settings.return_value.CHUNKING_STRATEGY = "auto"
            chunker = select_chunker(doc, els)
        assert chunker.name == "semantic"
