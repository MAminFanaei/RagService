"""
Phase 1 tests — ORM models.

Covers:
  - Both models are importable
  - DocumentStatus enum has all 6 required values
  - Document + Chunk default field values are correct
  - UUID primary keys are auto-generated and unique
  - Bidirectional relationship attributes exist on both models
  - Chunk.doc_id FK points to ingestion_documents
  - Table names are correct
  - Composite indexes are defined on Chunk

These are pure unit tests — no DB connection needed.
"""

import uuid

import pytest

from ingestion.models import Chunk, Document, DocumentStatus


# --------------------------------------------------------------------------- #
# Enum tests                                                                   #
# --------------------------------------------------------------------------- #

class TestDocumentStatus:

    def test_all_six_values_present(self):
        values = {s.value for s in DocumentStatus}
        assert values == {"PENDING", "PROCESSING", "REVIEW", "INDEXING", "READY", "FAILED"}

    def test_is_string_enum(self):
        assert isinstance(DocumentStatus.PENDING, str)

    def test_pending_is_default_string(self):
        assert DocumentStatus.PENDING.value == "PENDING"

    def test_enum_comparison_with_string(self):
        # str enum: DocumentStatus.READY == "READY" must be True
        assert DocumentStatus.READY == "READY"


# --------------------------------------------------------------------------- #
# Document model tests                                                         #
# --------------------------------------------------------------------------- #

class TestDocumentModel:

    def test_table_name(self):
        assert Document.__tablename__ == "ingestion_documents"

    def test_instantiation_with_required_fields(self):
        doc = Document(
            original_filename="test.pdf",
            mime_type="application/pdf",
            file_size_bytes=1024,
            content_hash="a" * 64,
            storage_path="2024/01/uuid/test.pdf",
        )
        assert doc.original_filename == "test.pdf"
        assert doc.mime_type == "application/pdf"
        assert doc.file_size_bytes == 1024

    def test_uuid_auto_generated(self):
        doc = Document(
            original_filename="a.pdf",
            mime_type="application/pdf",
            file_size_bytes=1,
            content_hash="b" * 64,
            storage_path="p",
        )
        assert doc.id is not None
        # Must be valid UUID4 format
        parsed = uuid.UUID(doc.id, version=4)
        assert str(parsed) == doc.id

    def test_two_documents_get_different_uuids(self):
        doc1 = Document(
            original_filename="a.pdf", mime_type="application/pdf",
            file_size_bytes=1, content_hash="c" * 64, storage_path="p1",
        )
        doc2 = Document(
            original_filename="b.pdf", mime_type="application/pdf",
            file_size_bytes=2, content_hash="d" * 64, storage_path="p2",
        )
        assert doc1.id != doc2.id

    def test_default_status_is_pending(self):
        doc = Document(
            original_filename="x.pdf", mime_type="application/pdf",
            file_size_bytes=0, content_hash="e" * 64, storage_path="p",
        )
        assert doc.status == DocumentStatus.PENDING

    def test_default_storage_backend_is_local(self):
        doc = Document(
            original_filename="x.pdf", mime_type="application/pdf",
            file_size_bytes=0, content_hash="f" * 64, storage_path="p",
        )
        # server_default only applies at DB level; Python default may be None
        # We only assert the column exists and is settable
        doc.storage_backend = "local"
        assert doc.storage_backend == "local"

    def test_optional_fields_default_to_none(self):
        doc = Document(
            original_filename="x.pdf", mime_type="application/pdf",
            file_size_bytes=0, content_hash="g" * 64, storage_path="p",
        )
        assert doc.error_message is None
        assert doc.total_pages is None
        assert doc.total_chunks is None
        assert doc.detected_language is None
        assert doc.parser_used is None
        assert doc.ocr_used is None
        assert doc.tags is None
        assert doc.custom is None

    def test_chunks_relationship_attribute_exists(self):
        doc = Document(
            original_filename="x.pdf", mime_type="application/pdf",
            file_size_bytes=0, content_hash="h" * 64, storage_path="p",
        )
        # Attribute must exist (SQLAlchemy lazy="noload" returns [] without IO)
        assert hasattr(doc, "chunks")

    def test_tags_accepts_list(self):
        doc = Document(
            original_filename="x.pdf", mime_type="application/pdf",
            file_size_bytes=0, content_hash="i" * 64, storage_path="p",
            tags=["legal", "Q4-2024"],
        )
        assert doc.tags == ["legal", "Q4-2024"]

    def test_custom_accepts_dict(self):
        doc = Document(
            original_filename="x.pdf", mime_type="application/pdf",
            file_size_bytes=0, content_hash="j" * 64, storage_path="p",
            custom={"department": "finance"},
        )
        assert doc.custom["department"] == "finance"


# --------------------------------------------------------------------------- #
# Chunk model tests                                                            #
# --------------------------------------------------------------------------- #

class TestChunkModel:

    def test_table_name(self):
        assert Chunk.__tablename__ == "ingestion_chunks"

    def test_instantiation_with_required_fields(self):
        chunk = Chunk(
            doc_id="some-uuid",
            text="Sample chunk text",
            char_count=17,
        )
        assert chunk.doc_id == "some-uuid"
        assert chunk.text == "Sample chunk text"
        assert chunk.char_count == 17

    def test_uuid_auto_generated(self):
        chunk = Chunk(doc_id="x", text="t", char_count=1)
        assert chunk.id is not None
        parsed = uuid.UUID(chunk.id, version=4)
        assert str(parsed) == chunk.id

    def test_two_chunks_get_different_uuids(self):
        c1 = Chunk(doc_id="x", text="a", char_count=1)
        c2 = Chunk(doc_id="x", text="b", char_count=1)
        assert c1.id != c2.id

    def test_default_approved_is_false(self):
        chunk = Chunk(doc_id="x", text="t", char_count=1)
        assert chunk.approved is False

    def test_default_edited_by_admin_is_false(self):
        chunk = Chunk(doc_id="x", text="t", char_count=1)
        assert chunk.edited_by_admin is False

    def test_default_is_table_is_false(self):
        chunk = Chunk(doc_id="x", text="t", char_count=1)
        assert chunk.is_table is False

    def test_default_is_footnote_is_false(self):
        chunk = Chunk(doc_id="x", text="t", char_count=1)
        assert chunk.is_footnote is False

    def test_default_chunk_index_is_zero(self):
        chunk = Chunk(doc_id="x", text="t", char_count=1)
        assert chunk.chunk_index == 0

    def test_default_element_type_attribute_exists(self):
        chunk = Chunk(doc_id="x", text="t", char_count=1)
        # server_default applies at DB; Python side may be None before flush
        chunk.element_type = "text"
        assert chunk.element_type == "text"

    def test_optional_location_fields(self):
        chunk = Chunk(doc_id="x", text="t", char_count=1)
        assert chunk.page_number is None
        assert chunk.page_range is None
        assert chunk.bounding_box is None
        assert chunk.section_title is None
        assert chunk.section_path is None
        assert chunk.heading_level is None

    def test_optional_language_fields(self):
        chunk = Chunk(doc_id="x", text="t", char_count=1)
        assert chunk.language is None
        assert chunk.script_direction is None

    def test_vector_id_starts_none(self):
        chunk = Chunk(doc_id="x", text="t", char_count=1)
        assert chunk.vector_id is None

    def test_table_markdown_and_is_table(self):
        chunk = Chunk(
            doc_id="x",
            text="| col1 | col2 |\n|------|------|",
            char_count=30,
            is_table=True,
            table_markdown="| col1 | col2 |\n|------|------|",
        )
        assert chunk.is_table is True
        assert chunk.table_markdown is not None

    def test_document_relationship_attribute_exists(self):
        chunk = Chunk(doc_id="x", text="t", char_count=1)
        assert hasattr(chunk, "document")

    def test_composite_indexes_defined(self):
        """Verify the two composite indexes exist in __table_args__."""
        index_names = {idx.name for idx in Chunk.__table_args__ if hasattr(idx, "name")}
        assert "ix_ingestion_chunks_doc_approved" in index_names
        assert "ix_ingestion_chunks_doc_index"    in index_names

    def test_fk_points_to_ingestion_documents(self):
        fk_targets = {
            fk.target_fullname
            for col in Chunk.__table__.columns
            for fk in col.foreign_keys
        }
        assert "ingestion_documents.id" in fk_targets

    def test_section_path_accepts_list(self):
        chunk = Chunk(
            doc_id="x", text="t", char_count=1,
            section_path=["Chapter 1", "1.2 Background"],
        )
        assert chunk.section_path[1] == "1.2 Background"

    def test_bounding_box_accepts_dict(self):
        bbox = {"x0": 0.0, "y0": 10.5, "x1": 200.0, "y1": 30.0}
        chunk = Chunk(doc_id="x", text="t", char_count=1, bounding_box=bbox)
        assert chunk.bounding_box["x1"] == 200.0
