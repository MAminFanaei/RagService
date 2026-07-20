"""
Phase 1 tests — File intake (intake_file function).

Covers:
  - Valid file: Document row created, file on disk, correct return dict
  - Unknown MIME type → HTTPException 415
  - File too large → HTTPException 413
  - Duplicate (READY)  → returns {status: "duplicate"}, no new DB row
  - Duplicate (FAILED) → old record deleted, new ingest proceeds
  - Duplicate (in-flight PENDING) → returns duplicate without re-inserting
  - content_hash is correct SHA-256

Uses SQLite in-memory via SQLAlchemy async (no real MySQL needed).
Storage is backed by a tmp_path LocalStorage.
python-magic is mocked so tests run without libmagic installed.
"""

import hashlib
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from ingestion.models import Document, DocumentStatus
from ingestion.database import Base


# --------------------------------------------------------------------------- #
# In-memory SQLite engine for tests                                            #
# --------------------------------------------------------------------------- #

@pytest_asyncio.fixture(scope="function")
async def db_session(tmp_path):
    """
    Async SQLite in-memory session.
    Creates all ingestion tables fresh per test function.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session

    await engine.dispose()


# --------------------------------------------------------------------------- #
# Storage fixture                                                              #
# --------------------------------------------------------------------------- #

@pytest_asyncio.fixture
async def local_storage_fixture(tmp_path):
    from ingestion.storage import LocalStorage
    return LocalStorage(str(tmp_path)), tmp_path


# --------------------------------------------------------------------------- #
# Helper: build minimal valid PDF magic bytes                                  #
# --------------------------------------------------------------------------- #

PDF_MAGIC = b"%PDF-1.4 minimal fake pdf content for testing purposes"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# Shared patch context                                                          #
# --------------------------------------------------------------------------- #

def _patch_mime(mime: str):
    """Patch _detect_mime to return a fixed MIME type."""
    return patch("ingestion.intake._detect_mime", return_value=mime)


def _patch_storage(storage_instance):
    """Patch get_storage() to return our test LocalStorage."""
    return patch("ingestion.intake.get_storage", return_value=storage_instance)


# --------------------------------------------------------------------------- #
# Tests                                                                        #
# --------------------------------------------------------------------------- #

class TestIntakeFileAccepted:

    @pytest.mark.asyncio
    async def test_valid_pdf_returns_accepted(self, db_session, local_storage_fixture):
        storage, tmp_path = local_storage_fixture
        with _patch_mime("application/pdf"), _patch_storage(storage):
            from ingestion.intake import intake_file
            result = await intake_file(
                data=PDF_MAGIC,
                filename="contract.pdf",
                db=db_session,
                tags=["legal"],
            )
        assert result["status"] == "accepted"
        assert result["mime_type"] == "application/pdf"
        assert result["parser_type"] == "pdf"
        assert "doc_id" in result
        assert "storage_path" in result

    @pytest.mark.asyncio
    async def test_document_row_created_in_db(self, db_session, local_storage_fixture):
        storage, _ = local_storage_fixture
        with _patch_mime("application/pdf"), _patch_storage(storage):
            from ingestion.intake import intake_file
            result = await intake_file(
                data=PDF_MAGIC, filename="doc.pdf", db=db_session
            )
        from sqlalchemy import select
        row = await db_session.execute(
            select(Document).where(Document.id == result["doc_id"])
        )
        doc = row.scalar_one_or_none()
        assert doc is not None
        assert doc.status == DocumentStatus.PENDING
        assert doc.original_filename == "doc.pdf"
        assert doc.mime_type == "application/pdf"

    @pytest.mark.asyncio
    async def test_file_written_to_storage(self, db_session, local_storage_fixture):
        storage, _ = local_storage_fixture
        with _patch_mime("application/pdf"), _patch_storage(storage):
            from ingestion.intake import intake_file
            result = await intake_file(
                data=PDF_MAGIC, filename="saved.pdf", db=db_session
            )
        assert await storage.exists(result["storage_path"]) is True

    @pytest.mark.asyncio
    async def test_storage_path_format(self, db_session, local_storage_fixture):
        """Path must be {year}/{month:02d}/{doc_id}/{filename}."""
        storage, _ = local_storage_fixture
        with _patch_mime("application/pdf"), _patch_storage(storage):
            from ingestion.intake import intake_file
            result = await intake_file(
                data=PDF_MAGIC, filename="report.pdf", db=db_session
            )
        parts = result["storage_path"].split("/")
        assert len(parts) == 4          # year / month / doc_id / filename
        assert parts[3] == "report.pdf"
        assert len(parts[0]) == 4       # year
        assert len(parts[1]) == 2       # zero-padded month

    @pytest.mark.asyncio
    async def test_content_hash_is_sha256(self, db_session, local_storage_fixture):
        storage, _ = local_storage_fixture
        data = b"unique content " + uuid.uuid4().bytes
        expected_hash = _sha256(data)
        with _patch_mime("application/pdf"), _patch_storage(storage):
            from ingestion.intake import intake_file
            result = await intake_file(data=data, filename="f.pdf", db=db_session)
        from sqlalchemy import select
        doc = (await db_session.execute(
            select(Document).where(Document.id == result["doc_id"])
        )).scalar_one()
        assert doc.content_hash == expected_hash

    @pytest.mark.asyncio
    async def test_tags_stored_on_document(self, db_session, local_storage_fixture):
        storage, _ = local_storage_fixture
        with _patch_mime("application/pdf"), _patch_storage(storage):
            from ingestion.intake import intake_file
            result = await intake_file(
                data=PDF_MAGIC, filename="tagged.pdf",
                db=db_session, tags=["finance", "2024"]
            )
        from sqlalchemy import select
        doc = (await db_session.execute(
            select(Document).where(Document.id == result["doc_id"])
        )).scalar_one()
        assert doc.tags == ["finance", "2024"]

    @pytest.mark.asyncio
    async def test_docx_mime_accepted(self, db_session, local_storage_fixture):
        storage, _ = local_storage_fixture
        docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        with _patch_mime(docx_mime), _patch_storage(storage):
            from ingestion.intake import intake_file
            result = await intake_file(
                data=b"PK fake docx", filename="report.docx", db=db_session
            )
        assert result["status"] == "accepted"
        assert result["parser_type"] == "docx"

    @pytest.mark.asyncio
    async def test_image_png_accepted(self, db_session, local_storage_fixture):
        storage, _ = local_storage_fixture
        with _patch_mime("image/png"), _patch_storage(storage):
            from ingestion.intake import intake_file
            result = await intake_file(
                data=b"\x89PNG fake", filename="scan.png", db=db_session
            )
        assert result["status"] == "accepted"
        assert result["parser_type"] == "ocr_direct"


class TestIntakeMimeRejection:

    @pytest.mark.asyncio
    async def test_unknown_mime_raises_415(self, db_session, local_storage_fixture):
        storage, _ = local_storage_fixture
        with _patch_mime("application/x-executable"), _patch_storage(storage):
            from ingestion.intake import intake_file
            with pytest.raises(HTTPException) as exc_info:
                await intake_file(data=b"ELF", filename="evil.exe", db=db_session)
        assert exc_info.value.status_code == 415

    @pytest.mark.asyncio
    async def test_415_detail_contains_mime(self, db_session, local_storage_fixture):
        storage, _ = local_storage_fixture
        with _patch_mime("video/mp4"), _patch_storage(storage):
            from ingestion.intake import intake_file
            with pytest.raises(HTTPException) as exc_info:
                await intake_file(data=b"...", filename="movie.mp4", db=db_session)
        assert "video/mp4" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_octet_stream_raises_415(self, db_session, local_storage_fixture):
        """application/octet-stream (magic fallback) is not in allowlist."""
        storage, _ = local_storage_fixture
        with _patch_mime("application/octet-stream"), _patch_storage(storage):
            from ingestion.intake import intake_file
            with pytest.raises(HTTPException) as exc_info:
                await intake_file(data=b"\x00\x01", filename="blob.bin", db=db_session)
        assert exc_info.value.status_code == 415


class TestIntakeSizeRejection:

    @pytest.mark.asyncio
    async def test_oversized_file_raises_413(self, db_session, local_storage_fixture):
        storage, _ = local_storage_fixture
        # Create data 1 byte over the limit
        from ingestion.config import settings
        oversized = b"x" * (settings.MAX_UPLOAD_FILE_SIZE_MB * 1024 * 1024 + 1)
        with _patch_mime("application/pdf"), _patch_storage(storage):
            from ingestion.intake import intake_file
            with pytest.raises(HTTPException) as exc_info:
                await intake_file(data=oversized, filename="big.pdf", db=db_session)
        assert exc_info.value.status_code == 413

    @pytest.mark.asyncio
    async def test_413_detail_mentions_size(self, db_session, local_storage_fixture):
        storage, _ = local_storage_fixture
        from ingestion.config import settings
        oversized = b"x" * (settings.MAX_UPLOAD_FILE_SIZE_MB * 1024 * 1024 + 1)
        with _patch_mime("application/pdf"), _patch_storage(storage):
            from ingestion.intake import intake_file
            with pytest.raises(HTTPException) as exc_info:
                await intake_file(data=oversized, filename="big.pdf", db=db_session)
        assert "MB" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_exactly_at_limit_is_accepted(self, db_session, local_storage_fixture):
        """A file exactly at the max size limit must pass."""
        storage, _ = local_storage_fixture
        from ingestion.config import settings
        exact = b"x" * (settings.MAX_UPLOAD_FILE_SIZE_MB * 1024 * 1024)
        with _patch_mime("application/pdf"), _patch_storage(storage):
            from ingestion.intake import intake_file
            result = await intake_file(data=exact, filename="exact.pdf", db=db_session)
        assert result["status"] == "accepted"


class TestIntakeDuplication:

    @pytest.mark.asyncio
    async def test_duplicate_ready_returns_duplicate_status(
        self, db_session, local_storage_fixture
    ):
        storage, _ = local_storage_fixture
        data = b"duplicate content " + uuid.uuid4().bytes
        content_hash = _sha256(data)

        # Pre-insert a READY document with this hash
        existing = Document(
            original_filename="existing.pdf",
            mime_type="application/pdf",
            file_size_bytes=len(data),
            content_hash=content_hash,
            storage_path="old/path/existing.pdf",
            status=DocumentStatus.READY,
        )
        db_session.add(existing)
        await db_session.commit()

        with _patch_mime("application/pdf"), _patch_storage(storage):
            from ingestion.intake import intake_file
            result = await intake_file(
                data=data, filename="duplicate.pdf", db=db_session
            )

        assert result["status"] == "duplicate"
        assert result["doc_id"] == existing.id

    @pytest.mark.asyncio
    async def test_duplicate_ready_no_new_db_row(
        self, db_session, local_storage_fixture
    ):
        storage, _ = local_storage_fixture
        data = b"dup no new row " + uuid.uuid4().bytes
        content_hash = _sha256(data)

        existing = Document(
            original_filename="e.pdf",
            mime_type="application/pdf",
            file_size_bytes=1,
            content_hash=content_hash,
            storage_path="p",
            status=DocumentStatus.READY,
        )
        db_session.add(existing)
        await db_session.commit()

        with _patch_mime("application/pdf"), _patch_storage(storage):
            from ingestion.intake import intake_file
            await intake_file(data=data, filename="dup.pdf", db=db_session)

        from sqlalchemy import select, func
        count = (await db_session.execute(
            select(func.count()).select_from(Document)
        )).scalar()
        assert count == 1   # still only the original

    @pytest.mark.asyncio
    async def test_duplicate_failed_re_ingests(
        self, db_session, local_storage_fixture
    ):
        """
        A FAILED document with the same hash must be deleted and
        re-ingested as a fresh PENDING document.
        """
        storage, _ = local_storage_fixture
        data = b"failed before " + uuid.uuid4().bytes
        content_hash = _sha256(data)

        # Save a stale file so delete doesn't error
        stale_path = "stale/file.pdf"
        await storage.save(b"stale", stale_path)

        failed_doc = Document(
            original_filename="failed.pdf",
            mime_type="application/pdf",
            file_size_bytes=1,
            content_hash=content_hash,
            storage_path=stale_path,
            status=DocumentStatus.FAILED,
        )
        db_session.add(failed_doc)
        await db_session.commit()

        with _patch_mime("application/pdf"), _patch_storage(storage):
            from ingestion.intake import intake_file
            result = await intake_file(
                data=data, filename="retry.pdf", db=db_session
            )

        assert result["status"] == "accepted"
        # The new doc_id must differ from the old failed one
        assert result["doc_id"] != failed_doc.id

    @pytest.mark.asyncio
    async def test_duplicate_pending_returns_duplicate(
        self, db_session, local_storage_fixture
    ):
        """In-flight PENDING document → return duplicate to avoid races."""
        storage, _ = local_storage_fixture
        data = b"in-flight " + uuid.uuid4().bytes
        content_hash = _sha256(data)

        pending = Document(
            original_filename="processing.pdf",
            mime_type="application/pdf",
            file_size_bytes=1,
            content_hash=content_hash,
            storage_path="p",
            status=DocumentStatus.PENDING,
        )
        db_session.add(pending)
        await db_session.commit()

        with _patch_mime("application/pdf"), _patch_storage(storage):
            from ingestion.intake import intake_file
            result = await intake_file(
                data=data, filename="same.pdf", db=db_session
            )

        assert result["status"] == "duplicate"
        assert result["doc_id"] == pending.id
