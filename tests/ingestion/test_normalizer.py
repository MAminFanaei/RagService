"""
Tests for the normalizer (ingestion/normalizer.py).

Verifies:
- All chunk contract fields are present
- Language detection assigns correct codes for EN/FA/AR text
- RTL direction assigned for Persian/Arabic/Hebrew
- LTR direction for English
- section_title_text construction
- chunk_index and total_chunks are set correctly
- Empty elements are skipped
- token_estimate and char_count are positive for non-empty text
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from ingestion.parsers.base import ParsedElement
from ingestion.normalizer import (
    normalize,
    _detect_language,
    _classify_by_bbox,
    _estimate_tokens,
    _sort_rtl_elements,
    _RTL_LANGUAGES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_doc(
    doc_id: str | None = None,
    filename: str = "test.pdf",
    tags: list | None = None,
    custom: dict | None = None,
) -> MagicMock:
    doc = MagicMock()
    doc.id = doc_id or str(uuid.uuid4())
    doc.original_filename = filename
    doc.tags = tags or []
    doc.custom = custom or {}
    return doc


def _make_element(
    text: str = "Sample text",
    element_type: str = "text",
    page_number: int | None = 1,
    section_title: str | None = None,
    section_path: list | None = None,
    is_table: bool = False,
    table_markdown: str | None = None,
    bounding_box: dict | None = None,
    heading_level: int | None = None,
    language_hint: str | None = None,
    parser_name: str = "test_parser",
) -> ParsedElement:
    return ParsedElement(
        text=text,
        parser_name=parser_name,
        element_type=element_type,
        page_number=page_number,
        section_title=section_title,
        section_path=section_path or [],
        is_table=is_table,
        table_markdown=table_markdown,
        bounding_box=bounding_box,
        heading_level=heading_level,
        language_hint=language_hint,
    )


# ---------------------------------------------------------------------------
# normalize() — chunk contract fields
# ---------------------------------------------------------------------------

class TestNormalizeContractFields:
    def test_all_required_fields_present(self):
        doc = _make_doc()
        elements = [_make_element(text="Hello world this is a test sentence.")]
        chunks = normalize(elements, doc)
        assert len(chunks) == 1
        chunk = chunks[0]

        required_fields = [
            "chunk_id", "doc_id", "text", "section_title_text",
            "source_file", "doc_title", "page_number", "section_path",
            "section_title", "element_type", "is_table", "table_markdown",
            "language", "script_direction", "chunk_index", "total_chunks",
            "char_count", "token_estimate", "tags", "ingestion_version",
            "dense_vector", "sparse_vector",
        ]
        for field in required_fields:
            assert field in chunk, f"Missing field: {field}"

    def test_chunk_id_is_uuid(self):
        doc = _make_doc()
        elements = [_make_element(text="Some text for testing UUID generation.")]
        chunks = normalize(elements, doc)
        chunk_id = chunks[0]["chunk_id"]
        # Should parse as UUID without error
        parsed = uuid.UUID(chunk_id)
        assert str(parsed) == chunk_id

    def test_doc_id_matches(self):
        doc_id = str(uuid.uuid4())
        doc = _make_doc(doc_id=doc_id)
        elements = [_make_element(text="Text for doc_id test.")]
        chunks = normalize(elements, doc)
        assert chunks[0]["doc_id"] == doc_id

    def test_source_file_set(self):
        doc = _make_doc(filename="contract.pdf")
        elements = [_make_element(text="Contract text content here.")]
        chunks = normalize(elements, doc)
        assert chunks[0]["source_file"] == "contract.pdf"

    def test_dense_and_sparse_vector_none_initially(self):
        doc = _make_doc()
        elements = [_make_element(text="Vectors not yet assigned by normalizer.")]
        chunks = normalize(elements, doc)
        assert chunks[0]["dense_vector"] is None
        assert chunks[0]["sparse_vector"] is None

    def test_ingestion_version_present(self):
        doc = _make_doc()
        elements = [_make_element(text="Version test chunk content.")]
        with patch("ingestion.normalizer.get_settings") as mock_settings:
            mock_settings.return_value.INGESTION_VERSION = "1.0"
            mock_settings.return_value.LANGUAGE_DETECTION_CONFIDENCE_THRESHOLD = 0.8
            chunks = normalize(elements, doc)
        assert chunks[0]["ingestion_version"] == "1.0"

    def test_tags_from_document(self):
        doc = _make_doc(tags=["legal", "Q4-2024"])
        elements = [_make_element(text="Tagged document content here.")]
        chunks = normalize(elements, doc)
        assert "legal" in chunks[0]["tags"]
        assert "Q4-2024" in chunks[0]["tags"]

    def test_empty_tags_when_doc_has_none(self):
        doc = _make_doc(tags=None)
        elements = [_make_element(text="No tags document.")]
        chunks = normalize(elements, doc)
        assert chunks[0]["tags"] == []


# ---------------------------------------------------------------------------
# normalize() — indices and counts
# ---------------------------------------------------------------------------

class TestNormalizeIndices:
    def test_chunk_index_sequential(self):
        doc = _make_doc()
        elements = [
            _make_element(text="First chunk content here."),
            _make_element(text="Second chunk content here."),
            _make_element(text="Third chunk content here."),
        ]
        chunks = normalize(elements, doc)
        assert [c["chunk_index"] for c in chunks] == [0, 1, 2]

    def test_total_chunks_correct(self):
        doc = _make_doc()
        elements = [
            _make_element(text="First chunk."),
            _make_element(text="Second chunk."),
        ]
        chunks = normalize(elements, doc)
        assert chunks[0]["total_chunks"] == 2
        assert chunks[1]["total_chunks"] == 2

    def test_empty_elements_skipped(self):
        doc = _make_doc()
        elements = [
            _make_element(text=""),
            _make_element(text="   "),
            _make_element(text="Valid text content here."),
        ]
        chunks = normalize(elements, doc)
        # Empty elements should be skipped
        assert len(chunks) == 1

    def test_single_element(self):
        doc = _make_doc()
        elements = [_make_element(text="Single chunk content.")]
        chunks = normalize(elements, doc)
        assert len(chunks) == 1
        assert chunks[0]["chunk_index"] == 0
        assert chunks[0]["total_chunks"] == 1


# ---------------------------------------------------------------------------
# normalize() — text metrics
# ---------------------------------------------------------------------------

class TestNormalizeTextMetrics:
    def test_char_count_correct(self):
        text = "Hello world"
        doc = _make_doc()
        elements = [_make_element(text=text)]
        chunks = normalize(elements, doc)
        assert chunks[0]["char_count"] == len(text)

    def test_token_estimate_positive(self):
        doc = _make_doc()
        elements = [_make_element(text="This has several words in it.")]
        chunks = normalize(elements, doc)
        assert chunks[0]["token_estimate"] > 0

    def test_section_title_text_prepended(self):
        doc = _make_doc()
        elements = [
            _make_element(
                text="Body text content.",
                section_title="Introduction",
            )
        ]
        chunks = normalize(elements, doc)
        assert chunks[0]["section_title_text"] == "Introduction Body text content."

    def test_section_title_text_without_title(self):
        doc = _make_doc()
        elements = [_make_element(text="Just body text.")]
        chunks = normalize(elements, doc)
        assert chunks[0]["section_title_text"] == "Just body text."


# ---------------------------------------------------------------------------
# normalize() — language detection
# ---------------------------------------------------------------------------

class TestNormalizeLanguageDetection:
    def test_english_text_ltr(self):
        doc = _make_doc()
        elements = [
            _make_element(
                text="This is a standard English language text for testing purposes.",
                language_hint="en",
            )
        ]
        chunks = normalize(elements, doc)
        assert chunks[0]["script_direction"] == "ltr"

    def test_persian_text_rtl(self):
        """Persian text should be detected as RTL regardless of lingua availability."""
        doc = _make_doc()
        elements = [
            _make_element(
                text="سلام دنیا این یک متن فارسی است برای آزمایش.",
                language_hint="fa",
            )
        ]
        chunks = normalize(elements, doc)
        assert chunks[0]["script_direction"] == "rtl"
        assert chunks[0]["language"] == "fa"

    def test_arabic_text_rtl(self):
        doc = _make_doc()
        elements = [
            _make_element(
                text="مرحبا بالعالم هذا نص عربي للاختبار.",
                language_hint="ar",
            )
        ]
        chunks = normalize(elements, doc)
        assert chunks[0]["script_direction"] == "rtl"
        assert chunks[0]["language"] == "ar"

    def test_hebrew_text_rtl(self):
        doc = _make_doc()
        elements = [
            _make_element(
                text="שלום עולם זהו טקסט עברי לבדיקה.",
                language_hint="he",
            )
        ]
        chunks = normalize(elements, doc)
        assert chunks[0]["script_direction"] == "rtl"

    def test_rtl_languages_set(self):
        assert "fa" in _RTL_LANGUAGES
        assert "ar" in _RTL_LANGUAGES
        assert "ur" in _RTL_LANGUAGES
        assert "he" in _RTL_LANGUAGES
        assert "en" not in _RTL_LANGUAGES
        assert "fr" not in _RTL_LANGUAGES

    def test_language_hint_overrides_detection(self):
        """When language_hint is provided, it should be respected."""
        doc = _make_doc()
        elements = [
            _make_element(
                text="Mixed text that could be ambiguous.",
                language_hint="fa",  # Force Persian
            )
        ]
        chunks = normalize(elements, doc)
        # With hint="fa" and it being in known codes, should use RTL
        assert chunks[0]["script_direction"] == "rtl"


# ---------------------------------------------------------------------------
# _detect_language helper
# ---------------------------------------------------------------------------

class TestDetectLanguage:
    def test_returns_tuple(self):
        lang, conf = _detect_language("hello world", 0.8)
        assert isinstance(lang, str)
        assert conf is None or isinstance(conf, float)

    def test_hint_passthrough_when_known(self):
        lang, conf = _detect_language("any text", 0.8, hint="fa")
        assert lang == "fa"
        assert conf == 1.0

    def test_short_text_returns_unknown_or_hint(self):
        lang, conf = _detect_language("hi", 0.8)
        # Too short for reliable detection
        assert lang in ("unknown", "en", "hi")  # any of these is acceptable

    def test_unknown_when_no_lingua(self):
        with patch("ingestion.normalizer._get_detector", return_value=None):
            lang, conf = _detect_language("Hello world", 0.8)
            assert lang == "unknown"
            assert conf is None


# ---------------------------------------------------------------------------
# _classify_by_bbox helper
# ---------------------------------------------------------------------------

class TestClassifyByBbox:
    def test_no_bbox_returns_original_type(self):
        el = ParsedElement(text="x", parser_name="p", element_type="text")
        assert _classify_by_bbox(el) == "text"

    def test_low_font_size_near_bottom_is_footnote(self):
        el = ParsedElement(
            text="fn",
            parser_name="p",
            element_type="text",
            bounding_box={"x0": 0, "y0": 780, "x1": 500, "y1": 800},
            raw_metadata={"font_size": 8.0},
        )
        result = _classify_by_bbox(el, page_height=842.0)
        assert result == "footnote"

    def test_normal_text_unchanged(self):
        el = ParsedElement(
            text="normal paragraph",
            parser_name="p",
            element_type="text",
            bounding_box={"x0": 0, "y0": 300, "x1": 500, "y1": 320},
            raw_metadata={"font_size": 12.0},
        )
        result = _classify_by_bbox(el, page_height=842.0)
        assert result == "text"

    def test_very_bottom_is_footnote(self):
        el = ParsedElement(
            text="footer text",
            parser_name="p",
            element_type="text",
            bounding_box={"x0": 0, "y0": 800, "x1": 500, "y1": 830},
            raw_metadata={"font_size": 12.0},  # Normal font but at bottom
        )
        result = _classify_by_bbox(el, page_height=842.0)
        assert result == "footnote"


# ---------------------------------------------------------------------------
# _estimate_tokens helper
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    def test_empty_string(self):
        assert _estimate_tokens("") == 0

    def test_single_word(self):
        assert _estimate_tokens("hello") >= 1

    def test_multiple_words(self):
        count = _estimate_tokens("hello world foo bar baz")
        assert count > 3  # At least word count

    def test_proportional_to_length(self):
        short = _estimate_tokens("hello world")
        long = _estimate_tokens("hello world " * 10)
        assert long > short


# ---------------------------------------------------------------------------
# RTL sorting
# ---------------------------------------------------------------------------

class TestRTLSorting:
    def test_sort_right_to_left_within_band(self):
        elements = [
            ParsedElement(text="A", parser_name="p", bounding_box={"x0": 100, "y0": 100, "x1": 200, "y1": 120}),
            ParsedElement(text="B", parser_name="p", bounding_box={"x0": 400, "y0": 100, "x1": 500, "y1": 120}),
            ParsedElement(text="C", parser_name="p", bounding_box={"x0": 250, "y0": 100, "x1": 350, "y1": 120}),
        ]
        sorted_elements = _sort_rtl_elements(elements)
        # B (x0=400) should come first, then C (x0=250), then A (x0=100)
        assert sorted_elements[0].text == "B"
        assert sorted_elements[1].text == "C"
        assert sorted_elements[2].text == "A"

    def test_elements_without_bbox_appended(self):
        with_bbox = ParsedElement(
            text="Has bbox",
            parser_name="p",
            bounding_box={"x0": 200, "y0": 100, "x1": 400, "y1": 120},
        )
        without_bbox = ParsedElement(text="No bbox", parser_name="p")
        result = _sort_rtl_elements([with_bbox, without_bbox])
        assert result[-1].text == "No bbox"

    def test_empty_list(self):
        assert _sort_rtl_elements([]) == []

    def test_no_bbox_elements_unchanged(self):
        elements = [
            ParsedElement(text="A", parser_name="p"),
            ParsedElement(text="B", parser_name="p"),
        ]
        result = _sort_rtl_elements(elements)
        # All without bbox — returned as-is
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Section path preservation
# ---------------------------------------------------------------------------

class TestSectionPathPreservation:
    def test_section_path_copied(self):
        doc = _make_doc()
        elements = [
            _make_element(
                text="Content under section.",
                section_path=["Chapter 1", "Section 1.1"],
                section_title="Section 1.1",
            )
        ]
        chunks = normalize(elements, doc)
        assert chunks[0]["section_path"] == ["Chapter 1", "Section 1.1"]
        assert chunks[0]["section_title"] == "Section 1.1"

    def test_empty_section_path(self):
        doc = _make_doc()
        elements = [_make_element(text="Preamble text without section.")]
        chunks = normalize(elements, doc)
        assert chunks[0]["section_path"] == []

    def test_is_table_preserved(self):
        doc = _make_doc()
        elements = [
            _make_element(
                text="Col1 | Col2\n1 | 2",
                element_type="table",
                is_table=True,
                table_markdown="| Col1 | Col2 |\n| 1 | 2 |",
            )
        ]
        chunks = normalize(elements, doc)
        assert chunks[0]["is_table"] is True
        assert chunks[0]["table_markdown"] is not None

    def test_page_number_preserved(self):
        doc = _make_doc()
        elements = [_make_element(text="Page three content.", page_number=3)]
        chunks = normalize(elements, doc)
        assert chunks[0]["page_number"] == 3
