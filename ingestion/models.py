"""
SQLAlchemy ORM models for the ingestion microservice.

Tables:
  - ingestion_documents  → one per uploaded file
  - ingestion_chunks     → many per document, created after parsing

Uses ingestion.database.Base (fully independent from app/).
Alembic picks these up via alembic/env.py — see ingestion/alembic_note.md.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    BigInteger,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ingestion.database import Base


# --------------------------------------------------------------------------- #
# Enums                                                                        #
# --------------------------------------------------------------------------- #

class DocumentStatus(str, enum.Enum):
    """
    Lifecycle states a document moves through.

    PENDING    → file received, not yet processed
    PROCESSING → Celery worker is parsing / OCR-ing
    REVIEW     → chunks saved to SQL, awaiting admin approval
    INDEXING   → approved chunks being embedded + indexed into ES
    READY      → fully indexed, searchable
    FAILED     → unrecoverable error (see error_message)
    """
    PENDING    = "PENDING"
    PROCESSING = "PROCESSING"
    REVIEW     = "REVIEW"
    INDEXING   = "INDEXING"
    READY      = "READY"
    FAILED     = "FAILED"


# --------------------------------------------------------------------------- #
# Document model                                                               #
# --------------------------------------------------------------------------- #

class Document(Base):
    """
    One row per uploaded file.

    content_hash (SHA-256) carries a UNIQUE constraint so we can detect
    duplicate uploads before spending compute on them.

    tags is stored as a JSON array (list[str]) — the column type is JSON
    which accepts both lists and dicts; we always write lists.
    """
    __tablename__ = "ingestion_documents"

    # -- Identity ----------------------------------------------------------- #
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # -- File info ---------------------------------------------------------- #
    original_filename: Mapped[str]  = mapped_column(String(512), nullable=False)
    mime_type:         Mapped[str]  = mapped_column(String(128), nullable=False)
    file_size_bytes:   Mapped[int]  = mapped_column(BigInteger,  nullable=False)

    # SHA-256 hex digest of raw file bytes — unique for dedup
    content_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )

    # -- Storage ------------------------------------------------------------ #
    storage_backend: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="local"
    )
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)

    # -- Pipeline state ----------------------------------------------------- #
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus),
        nullable=False,
        default=DocumentStatus.PENDING,
        server_default=DocumentStatus.PENDING.value,
        index=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- Parsing results ---------------------------------------------------- #
    total_pages:       Mapped[int | None] = mapped_column(Integer,     nullable=True)
    total_chunks:      Mapped[int | None] = mapped_column(Integer,     nullable=True)
    detected_language: Mapped[str | None] = mapped_column(String(16),  nullable=True)
    parser_used:       Mapped[str | None] = mapped_column(String(64),  nullable=True)
    ocr_used:          Mapped[str | None] = mapped_column(String(64),  nullable=True)

    # -- Metadata ----------------------------------------------------------- #
    # tags: stored as JSON array e.g. ["legal", "Q4-2024"]
    # Using JSON column (not ARRAY) for MySQL/SQLite compatibility.
    # Always write as list[str], never as dict.
    tags:   Mapped[list | None] = mapped_column(JSON, nullable=True)
    custom: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    ingestion_version: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="1.0"
    )

    # -- Timestamps --------------------------------------------------------- #
    created_at:    Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at:    Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now()
    )
    processed_at:  Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # -- Relationships ------------------------------------------------------ #
    # lazy="noload": safe for async — never triggers implicit IO
    chunks: Mapped[list["Chunk"]] = relationship(
        "Chunk",
        back_populates="document",
        cascade="all, delete-orphan",
        lazy="noload",
    )

    def __repr__(self) -> str:
        return (
            f"<Document id={self.id!r} file={self.original_filename!r} "
            f"status={self.status.value!r}>"
        )


# --------------------------------------------------------------------------- #
# Chunk model                                                                  #
# --------------------------------------------------------------------------- #

class Chunk(Base):
    """
    One row per parsed/chunked text segment.

    Created with approved=False after parsing.
    Admin reviews in the REVIEW stage and sets approved=True.
    Only approved chunks are embedded and indexed into Elasticsearch.

    vector_id stores the ES document _id after indexing — used for
    delete / reindex operations.
    """
    __tablename__ = "ingestion_chunks"

    # -- Identity ----------------------------------------------------------- #
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    doc_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("ingestion_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # -- Location in source document ---------------------------------------- #
    page_number:   Mapped[int | None]  = mapped_column(Integer,      nullable=True)
    page_range:    Mapped[list | None] = mapped_column(JSON,         nullable=True)  # [start, end]
    bounding_box:  Mapped[dict | None] = mapped_column(JSON,         nullable=True)  # {x0,y0,x1,y1}

    # -- Section / heading hierarchy ---------------------------------------- #
    section_title: Mapped[str | None]  = mapped_column(String(512),  nullable=True)
    section_path:  Mapped[list | None] = mapped_column(JSON,         nullable=True)  # ["Ch1", "1.2"]
    heading_level: Mapped[int | None]  = mapped_column(Integer,      nullable=True)  # 1-6

    # -- Element type ------------------------------------------------------- #
    element_type:   Mapped[str]          = mapped_column(String(32),  nullable=False, server_default="text")
    is_footnote:    Mapped[bool]         = mapped_column(Boolean,     nullable=False, default=False)
    is_table:       Mapped[bool]         = mapped_column(Boolean,     nullable=False, default=False)
    table_markdown: Mapped[str | None]   = mapped_column(Text,        nullable=True)

    # -- Content ------------------------------------------------------------ #
    text:           Mapped[str]      = mapped_column(Text,    nullable=False)
    char_count:     Mapped[int]      = mapped_column(Integer, nullable=False)
    token_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # -- Language ----------------------------------------------------------- #
    language:         Mapped[str | None] = mapped_column(String(16), nullable=True)  # en|fa|ar|mixed|unknown
    script_direction: Mapped[str | None] = mapped_column(String(4),  nullable=True)  # ltr|rtl

    # -- Ordering ----------------------------------------------------------- #
    chunk_index:  Mapped[int]        = mapped_column(Integer, nullable=False, default=0)
    total_chunks: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # -- Elasticsearch link ------------------------------------------------- #
    # Populated after indexing; None means not yet indexed
    vector_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )

    # -- Review workflow ---------------------------------------------------- #
    approved:        Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    edited_by_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # -- Timestamps --------------------------------------------------------- #
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # -- Relationships ------------------------------------------------------ #
    document: Mapped["Document"] = relationship(
        "Document",
        back_populates="chunks",
        lazy="noload",
    )

    # -- Table-level indexes ------------------------------------------------ #
    __table_args__ = (
        Index("ix_ingestion_chunks_doc_approved", "doc_id", "approved"),
        Index("ix_ingestion_chunks_doc_index",    "doc_id", "chunk_index"),
    )

    def __repr__(self) -> str:
        return (
            f"<Chunk id={self.id!r} doc_id={self.doc_id!r} "
            f"index={self.chunk_index} approved={self.approved}>"
        )
