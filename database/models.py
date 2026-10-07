"""The full schema from ARCHITECTURE.md §5.

Everything is here from day 1, including the tables nothing uses until M4.
Permission filtering is part of retrieval; retrofitting it later forces a
full re-index. This is the one thing that must not be deferred.

Two additions to the DDL in §5, both flagged in docs/M1_checklist.md:

  * chunks.content_sha256 -- §6 requires chunk-level incremental ingestion
    ("chunks whose hash already exists carry their embedding across
    unchanged") but the DDL had no column to store the hash in.
  * ingestion_jobs (status, created_at) index -- the worker polls this table
    with FOR UPDATE SKIP LOCKED on every loop.
"""

from __future__ import annotations

import enum
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBEDDING_DIM = 1024  # pinned; see config.settings.SCHEMA_EMBEDDING_DIM


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------
# Enums -- names must match the CREATE TYPE statements in the migration
# --------------------------------------------------------------------------

class UserRole(str, enum.Enum):
    admin = "admin"
    teacher = "teacher"


class DocStatus(str, enum.Enum):
    uploaded = "uploaded"
    processing = "processing"
    ready = "ready"
    failed = "failed"
    archived = "archived"


class ChunkKind(str, enum.Enum):
    text = "text"
    table = "table"
    ocr_text = "ocr_text"
    caption = "caption"


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


def _pg_enum(py_enum: type[enum.Enum], name: str) -> Enum:
    """Reference an existing Postgres type; the migration creates it."""
    return Enum(
        py_enum,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        create_type=False,
    )


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False,
                         server_default=func.now())


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[UserRole] = mapped_column(
        _pg_enum(UserRole, "user_role"), nullable=False, default=UserRole.teacher
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _ts()
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    documents: Mapped[list["Document"]] = relationship(
        back_populates="owner", foreign_keys="Document.owner_id"
    )


# --------------------------------------------------------------------------
# Documents and versions
# --------------------------------------------------------------------------

class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    department: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    #: visible to every authenticated teacher without an explicit grant
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[DocStatus] = mapped_column(
        _pg_enum(DocStatus, "doc_status"), nullable=False, default=DocStatus.uploaded
    )
    current_version_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("document_versions.id", use_alter=True,
                   name="documents_current_version_fk"),
        nullable=True,
    )
    created_at: Mapped[datetime] = _ts()

    owner: Mapped[User] = relationship(back_populates="documents",
                                       foreign_keys=[owner_id])
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document",
        foreign_keys="DocumentVersion.document_id",
        cascade="all, delete-orphan",
    )
    permissions: Mapped[list["DocumentPermission"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_no", name="uq_docver_doc_no"),
        # The same bytes are never ingested twice. This is what makes
        # "drop the whole folder in again" safe and near-instant.
        UniqueConstraint("sha256", name="uq_docver_sha256"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    #: which engine produced the text, for reproducibility
    ocr_engine: Mapped[str | None] = mapped_column(Text)
    #: DIGITAL | MIXED | SCANNED, as measured by ingestion.classify
    doc_class: Mapped[str | None] = mapped_column(Text)
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts()

    document: Mapped[Document] = relationship(
        back_populates="versions", foreign_keys=[document_id]
    )


class DocumentPermission(Base):
    """A grant is to a user OR to a role, never both."""

    __tablename__ = "document_permissions"
    __table_args__ = (
        CheckConstraint("(user_id IS NULL) <> (role IS NULL)",
                        name="ck_permission_user_xor_role"),
        Index("idx_perm_user", "user_id"),
        Index("idx_perm_doc", "document_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE")
    )
    role: Mapped[UserRole | None] = mapped_column(_pg_enum(UserRole, "user_role"))
    granted_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = _ts()

    document: Mapped[Document] = relationship(back_populates="permissions")


# --------------------------------------------------------------------------
# Tables and chunks
# --------------------------------------------------------------------------

class DocTable(Base):
    """Structured record of an extracted table. M3, but the schema is here now."""

    __tablename__ = "doc_tables"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    table_index: Mapped[int] = mapped_column(Integer, nullable=False)
    caption: Mapped[str | None] = mapped_column(Text)
    #: ["Course Code", "Course Name", "Credit"]
    columns: Mapped[list] = mapped_column(JSONB, nullable=False)
    #: [["CSE1101", "Programming", "3"], ...]
    rows: Mapped[list] = mapped_column(JSONB, nullable=False)
    #: serialised form that gets embedded
    markdown: Mapped[str] = mapped_column(Text, nullable=False)


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        Index(
            "chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
        ),
        Index("chunks_tsv", "content_tsv", postgresql_using="gin"),
        Index("chunks_trgm", "content", postgresql_using="gin",
              postgresql_ops={"content": "gin_trgm_ops"}),
        Index("chunks_doc", "document_id", "version_id"),
        Index("chunks_content_sha", "content_sha256"),
        Index("chunks_model_rev", "embedding_model", "embedding_rev"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    table_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("doc_tables.id", ondelete="CASCADE")
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[ChunkKind] = mapped_column(
        _pg_enum(ChunkKind, "chunk_kind"), nullable=False, default=ChunkKind.text
    )
    page_no: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple', content)", persisted=True)
    )
    #: sha256 of the NORMALISED text -- lets a new document version carry an
    #: unchanged chunk's embedding across instead of re-embedding it
    content_sha256: Mapped[str | None] = mapped_column(CHAR(64))
    token_count: Mapped[int | None] = mapped_column(Integer)
    lang: Mapped[str | None] = mapped_column(Text)  # 'bn' | 'en' | 'mixed'
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str] = mapped_column(Text, nullable=False)
    #: bump EMBEDDING_REV in .env to force re-embedding via scripts/reindex.py
    embedding_rev: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = _ts()


# --------------------------------------------------------------------------
# Jobs and logs
# --------------------------------------------------------------------------

class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        # The worker polls with FOR UPDATE SKIP LOCKED on every loop.
        Index("idx_jobs_poll", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[JobStatus] = mapped_column(
        _pg_enum(JobStatus, "job_status"), nullable=False, default=JobStatus.queued
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: last COMPLETED stage, so a crash during embedding resumes at embedding
    #: instead of re-running OCR on 200 pages
    stage: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    locked_by: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("idx_audit_ts", "ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = _ts()
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(Text, nullable=False)
    object_type: Mapped[str | None] = mapped_column(Text)
    object_id: Mapped[int | None] = mapped_column(BigInteger)
    ip: Mapped[str | None] = mapped_column(INET)
    #: never raw document content
    detail: Mapped[dict | None] = mapped_column(JSONB)


class QueryLog(Base):
    __tablename__ = "query_logs"
    __table_args__ = (Index("idx_query_ts", "ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = _ts()
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    question_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    #: NULL unless LOG_QUERY_TEXT=true
    question_text: Mapped[str | None] = mapped_column(Text)
    lang: Mapped[str | None] = mapped_column(Text)
    route: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    n_sources: Mapped[int | None] = mapped_column(Integer)
    refused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
