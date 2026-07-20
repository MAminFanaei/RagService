"""
Tests for ingestion/pipeline.py — the orchestrator.

All external dependencies (DB, storage, parsers, OCR, normalizer, dedup,
chunker, vector_store) are mocked so tests run without infrastructure.

Covers:
  - run_process_pipeline: full happy path, OCR branch, error → FAILED status
  - run_index_pipeline: happy path, empty approved chunks, error → FAILED
  - Status transitions are correct at each step
  - issue #8: parser_used is set before select_chunker is called
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now():
    return datetime.now(tz=timezone.utc)


def _make_doc(status="PENDING", parser_used=None, ocr_used=None, total_pages=None):
    """Build a mock Document ORM object."""
    doc = MagicMock()
    doc.id               = "doc-test-001"
    doc.original_filename = "test.pdf"
    doc.mime_type        = "application/pdf"
    doc.storage_path     = "2024/01/doc-test-001/test.pdf"
    doc.status           = status
    doc.parser_used      = parser_used
    doc.ocr_used         = ocr_used
    doc.total_pages      = total_pages
    doc.total_chunks     = None
    doc.error_message    = None
    doc.processed_at     = None
    doc.ingestion_version = "1.0"
    doc.tags             = []
    doc.custom           = {}
    return doc


def _make_parsed_element(
    text="Hello world", parser_name="pdf_docling", element_type="text", page_number=1
):
    from ingestion.parsers.base import ParsedElement
    return ParsedElement(
        text=text,
        parser_name=parser_name,
        element_type=element_type,
        page_number=page_number,
        section_path=[],
    )


def _make_chunk_dict(chunk_id=None, doc_id="doc-test-001"):
    cid = chunk_id or str(uuid.uuid4())
    return {
        "chunk_id":     cid,
        "doc_id":       doc_id,
        "text":         "Normalized chunk text",
        "char_count":   23,
        "token_estimate": 5,
        "element_type": "text",
        "is_table":     False,
        "is_footnote":  False,
        "section_path": [],
        "section_title": None,
        "section_title_text": "Normalized chunk text",
        "page_number":  1,
        "language":     "en",
        "script_direction": "ltr",
        "chunk_index":  0,
        "total_chunks": 1,
        "tags":         [],
        "ingestion_version": "1.0",
        "dense_vector": None,
        "sparse_vector": None,
        "bounding_box": None,
        "heading_level": None,
        "table_markdown": None,
        "source_file":  "test.pdf",
        "doc_title":    "test.pdf",
    }


def _make_chunk_orm(chunk_id=None, doc_id="doc-test-001", approved=True):
    chunk = MagicMock()
    chunk.id             = chunk_id or str(uuid.uuid4())
    chunk.doc_id         = doc_id
    chunk.text           = "Approved chunk text"
    chunk.char_count     = 19
    chunk.token_estimate = 4
    chunk.element_type   = "text"
    chunk.is_table       = False
    chunk.is_footnote    = False
    chunk.section_title  = None
    chunk.section_path   = []
    chunk.page_number    = 1
    chunk.language       = "en"
    chunk.script_direction = "ltr"
    chunk.chunk_index    = 0
    chunk.total_chunks   = 1
    chunk.table_markdown = None
    chunk.approved       = approved
    chunk.vector_id      = None
    return chunk


# ===========================================================================
# run_process_pipeline
# ===========================================================================

class TestRunProcessPipeline:

    @pytest.mark.asyncio
    async def test_happy_path_sets_review_status(self):
        """Full pipeline runs successfully → status becomes REVIEW."""
        doc = _make_doc()
        parsed_elements = [_make_parsed_element()]
        normalized      = [_make_chunk_dict()]
        chunked         = [_make_chunk_dict()]

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=doc))
        mock_session.commit   = AsyncMock()
        mock_session.refresh  = AsyncMock()
        mock_session.add      = MagicMock()

        mock_session_factory = MagicMock()
        mock_session_factory.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.__aexit__  = AsyncMock(return_value=False)

        mock_chunker = MagicMock()
        mock_chunker.chunk.return_value = chunked
        mock_chunker.name = "fixed"

        with (
            patch("ingestion.pipeline.AsyncSessionLocal", return_value=mock_session_factory),
            patch("ingestion.pipeline.get_storage") as mock_get_storage,
            patch("ingestion.pipeline.route_and_parse", new_callable=AsyncMock, return_value=parsed_elements),
            patch("ingestion.pipeline.normalize", return_value=normalized),
            patch("ingestion.pipeline.filter_chunks", return_value=normalized),
            patch("ingestion.pipeline.select_chunker", return_value=mock_chunker),
        ):
            mock_get_storage.return_value.load = AsyncMock(return_value=b"fake-pdf-bytes")
            result = await __import__(
                "ingestion.pipeline", fromlist=["run_process_pipeline"]
            ).run_process_pipeline("doc-test-001")

        assert result["status"] == "REVIEW"
        assert result["chunk_count"] == 1
        assert doc.status.value if hasattr(doc.status, "value") else doc.status

    @pytest.mark.asyncio
    async def test_parser_used_set_before_chunker_selection(self):
        """
        Issue #8 fix: parser_used must be set on the doc BEFORE select_chunker
        is called so the OCR branch can trigger correctly.
        """
        doc = _make_doc()
        parsed_elements = [_make_parsed_element(parser_name="deepdoc")]
        normalized      = [_make_chunk_dict()]
        chunked         = [_make_chunk_dict()]

        captured_doc_at_select = {}

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=doc))
        mock_session.commit  = AsyncMock()
        mock_session.refresh = AsyncMock()
        mock_session.add     = MagicMock()

        mock_session_factory = MagicMock()
        mock_session_factory.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.__aexit__  = AsyncMock(return_value=False)

        mock_chunker = MagicMock()
        mock_chunker.chunk.return_value = chunked
        mock_chunker.name = "semantic"

        def capture_select(doc_arg, els_arg):
            captured_doc_at_select["parser_used"] = doc_arg.parser_used
            return mock_chunker

        with (
            patch("ingestion.pipeline.AsyncSessionLocal", return_value=mock_session_factory),
            patch("ingestion.pipeline.get_storage") as mock_get_storage,
            patch("ingestion.pipeline.route_and_parse", new_callable=AsyncMock, return_value=parsed_elements),
            patch("ingestion.pipeline.normalize", return_value=normalized),
            patch("ingestion.pipeline.filter_chunks", return_value=normalized),
            patch("ingestion.pipeline.select_chunker", side_effect=capture_select),
        ):
            mock_get_storage.return_value.load = AsyncMock(return_value=b"fake-pdf")
            await __import__(
                "ingestion.pipeline", fromlist=["run_process_pipeline"]
            ).run_process_pipeline("doc-test-001")

        # parser_used was "deepdoc" (from parsed_elements[0].parser_name)
        assert captured_doc_at_select["parser_used"] == "deepdoc"

    @pytest.mark.asyncio
    async def test_ocr_invoked_for_image_pages(self):
        """Image-page elements trigger the OCR router."""
        doc = _make_doc()
        image_element = _make_parsed_element(
            text="", element_type="image_page", page_number=1
        )
        image_element.raw_metadata = {"image_bytes": b"\xff\xd8\xff"}  # fake JPEG

        ocr_result = MagicMock()
        ocr_result.text = "OCR extracted text"
        ocr_result.engine_name = "dots_ocr"
        ocr_result.language_detected = "en"
        ocr_result.structured_elements = None

        normalized = [_make_chunk_dict()]
        mock_chunker = MagicMock()
        mock_chunker.chunk.return_value = [_make_chunk_dict()]
        mock_chunker.name = "fixed"

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=doc))
        mock_session.commit  = AsyncMock()
        mock_session.refresh = AsyncMock()
        mock_session.add     = MagicMock()

        mock_session_factory = MagicMock()
        mock_session_factory.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.__aexit__  = AsyncMock(return_value=False)

        with (
            patch("ingestion.pipeline.AsyncSessionLocal", return_value=mock_session_factory),
            patch("ingestion.pipeline.get_storage") as mock_get_storage,
            patch("ingestion.pipeline.route_and_parse", new_callable=AsyncMock, return_value=[image_element]),
            patch("ingestion.pipeline.route_and_ocr", new_callable=AsyncMock, return_value=ocr_result) as mock_ocr,
            patch("ingestion.pipeline.normalize", return_value=normalized),
            patch("ingestion.pipeline.filter_chunks", return_value=normalized),
            patch("ingestion.pipeline.select_chunker", return_value=mock_chunker),
        ):
            mock_get_storage.return_value.load = AsyncMock(return_value=b"fake-pdf")
            result = await __import__(
                "ingestion.pipeline", fromlist=["run_process_pipeline"]
            ).run_process_pipeline("doc-test-001")

        mock_ocr.assert_called_once()
        assert doc.ocr_used == "dots_ocr"

    @pytest.mark.asyncio
    async def test_parser_failure_sets_failed_status(self):
        """When route_and_parse raises, document status becomes FAILED."""
        doc = _make_doc()

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=doc))
        mock_session.commit  = AsyncMock()
        mock_session.refresh = AsyncMock()

        mock_session_factory = MagicMock()
        mock_session_factory.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.__aexit__  = AsyncMock(return_value=False)

        with (
            patch("ingestion.pipeline.AsyncSessionLocal", return_value=mock_session_factory),
            patch("ingestion.pipeline.get_storage") as mock_get_storage,
            patch("ingestion.pipeline.route_and_parse", new_callable=AsyncMock,
                  side_effect=RuntimeError("Parser exploded")),
        ):
            mock_get_storage.return_value.load = AsyncMock(return_value=b"pdf")
            with pytest.raises(RuntimeError, match="Parser exploded"):
                await __import__(
                    "ingestion.pipeline", fromlist=["run_process_pipeline"]
                ).run_process_pipeline("doc-test-001")

        from ingestion.models import DocumentStatus
        assert doc.status == DocumentStatus.FAILED
        assert "Parser exploded" in doc.error_message

    @pytest.mark.asyncio
    async def test_document_not_found_raises(self):
        mock_session = AsyncMock()
        mock_execute_result = MagicMock()
        mock_execute_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_execute_result

        mock_session_factory = MagicMock()
        mock_session_factory.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.__aexit__  = AsyncMock(return_value=False)

        with patch("ingestion.pipeline.AsyncSessionLocal", return_value=mock_session_factory):
            with pytest.raises(ValueError, match="not found"):
                await __import__(
                    "ingestion.pipeline", fromlist=["run_process_pipeline"]
                ).run_process_pipeline("nonexistent-id")


# ===========================================================================
# run_index_pipeline
# ===========================================================================

class TestRunIndexPipeline:

    @pytest.mark.asyncio
    async def test_happy_path_sets_ready_status(self):
        doc = _make_doc(status="INDEXING")
        chunks = [_make_chunk_orm(), _make_chunk_orm()]

        mock_session = AsyncMock()
        # First execute: Document lookup
        # Second execute: Chunk lookup
        execute_results = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=doc)),
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=chunks)))),
        ]
        mock_session.execute.side_effect = execute_results
        mock_session.commit  = AsyncMock()
        mock_session.refresh = AsyncMock()

        mock_session_factory = MagicMock()
        mock_session_factory.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.__aexit__  = AsyncMock(return_value=False)

        mock_vs = AsyncMock()
        mock_vs.add_chunks.return_value = [c.id for c in chunks]

        with (
            patch("ingestion.pipeline.AsyncSessionLocal", return_value=mock_session_factory),
            patch("ingestion.pipeline.get_vector_store", return_value=mock_vs),
        ):
            result = await __import__(
                "ingestion.pipeline", fromlist=["run_index_pipeline"]
            ).run_index_pipeline("doc-test-001")

        assert result["indexed_count"] == 2
        assert result["status"] == "READY"
        mock_vs.add_chunks.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_approved_chunks_sets_ready_with_zero(self):
        """If no approved chunks exist, document goes READY with 0 indexed."""
        doc = _make_doc()

        mock_session = AsyncMock()
        execute_results = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=doc)),
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
        ]
        mock_session.execute.side_effect = execute_results
        mock_session.commit  = AsyncMock()
        mock_session.refresh = AsyncMock()

        mock_session_factory = MagicMock()
        mock_session_factory.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.__aexit__  = AsyncMock(return_value=False)

        with (
            patch("ingestion.pipeline.AsyncSessionLocal", return_value=mock_session_factory),
            patch("ingestion.pipeline.get_vector_store"),
        ):
            result = await __import__(
                "ingestion.pipeline", fromlist=["run_index_pipeline"]
            ).run_index_pipeline("doc-test-001")

        assert result["indexed_count"] == 0
        assert result["status"] == "READY"

    @pytest.mark.asyncio
    async def test_vector_id_updated_after_indexing(self):
        """Each chunk.vector_id should be set to the corresponding ES _id."""
        doc = _make_doc()
        chunk_a = _make_chunk_orm(chunk_id="c-aaa")
        chunk_b = _make_chunk_orm(chunk_id="c-bbb")

        mock_session = AsyncMock()
        execute_results = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=doc)),
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[chunk_a, chunk_b])))),
        ]
        mock_session.execute.side_effect = execute_results
        mock_session.commit  = AsyncMock()
        mock_session.refresh = AsyncMock()

        mock_session_factory = MagicMock()
        mock_session_factory.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.__aexit__  = AsyncMock(return_value=False)

        mock_vs = AsyncMock()
        # ES returns IDs in the same order as input
        mock_vs.add_chunks.return_value = ["c-aaa", "c-bbb"]

        with (
            patch("ingestion.pipeline.AsyncSessionLocal", return_value=mock_session_factory),
            patch("ingestion.pipeline.get_vector_store", return_value=mock_vs),
        ):
            await __import__(
                "ingestion.pipeline", fromlist=["run_index_pipeline"]
            ).run_index_pipeline("doc-test-001")

        assert chunk_a.vector_id == "c-aaa"
        assert chunk_b.vector_id == "c-bbb"

    @pytest.mark.asyncio
    async def test_es_failure_sets_failed_status(self):
        doc = _make_doc()
        chunks = [_make_chunk_orm()]

        mock_session = AsyncMock()
        execute_results = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=doc)),
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=chunks)))),
        ]
        mock_session.execute.side_effect = execute_results
        mock_session.commit  = AsyncMock()
        mock_session.refresh = AsyncMock()

        mock_session_factory = MagicMock()
        mock_session_factory.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.__aexit__  = AsyncMock(return_value=False)

        mock_vs = AsyncMock()
        mock_vs.add_chunks.side_effect = RuntimeError("ES is down")

        with (
            patch("ingestion.pipeline.AsyncSessionLocal", return_value=mock_session_factory),
            patch("ingestion.pipeline.get_vector_store", return_value=mock_vs),
        ):
            with pytest.raises(RuntimeError, match="ES is down"):
                await __import__(
                    "ingestion.pipeline", fromlist=["run_index_pipeline"]
                ).run_index_pipeline("doc-test-001")

        from ingestion.models import DocumentStatus
        assert doc.status == DocumentStatus.FAILED
        assert "ES is down" in doc.error_message
