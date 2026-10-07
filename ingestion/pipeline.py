"""Ingestion pipeline: file in, chunks with embeddings out.

Synchronous for M1. The Postgres-backed job queue and the worker process
arrive at M4; this function is what the worker will call, so the split is a
matter of who invokes it, not a rewrite.

Order matters and is not arbitrary:

    classify -> hash -> store -> load+convert -> chunk -> embed -> commit

Hashing before storing is what makes re-running the whole folder cheap: an
identical file is recognised before any work happens. Embedding last is what
makes a crash survivable -- everything before it is fast to redo, and
embedding on a CPU-only laptop is measured in minutes per document.
"""

from __future__ import annotations

import hashlib
import shutil
import time
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import get_settings
from database.models import Chunk, DocStatus, Document, DocumentVersion
from embeddings.registry import embedding_identity, get_embedder
from ingestion.chunker import chunk_pages
from ingestion.classify import DIGITAL
from ingestion.loaders.pdf import load_pdf


class IngestionRefused(RuntimeError):
    """The document is outside what M1 can handle correctly."""


@dataclass
class IngestResult:
    document_id: int | None = None
    version_id: int | None = None
    skipped: bool = False
    reason: str = ""
    doc_class: str = ""
    pages: int = 0
    chunks_created: int = 0
    chunks_reused: int = 0
    legacy_fonts: set[str] = field(default_factory=set)
    spans_converted: int = 0
    seconds_total: float = 0.0
    seconds_embedding: float = 0.0

    @property
    def chunks_per_second(self) -> float:
        if self.seconds_embedding <= 0:
            return 0.0
        return self.chunks_created / self.seconds_embedding


def sha256_file(path: Path, buf: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(buf):
            h.update(chunk)
    return h.hexdigest()


def ingest_pdf(
    session: Session,
    path: Path,
    *,
    owner_id: int,
    title: str | None = None,
    department: str | None = None,
    is_public: bool = True,
) -> IngestResult:
    """Ingest one PDF. Idempotent on file content."""
    settings = get_settings()
    settings.ensure_dirs()
    started = time.perf_counter()
    result = IngestResult()

    # --- 1. identical bytes are never ingested twice ---------------------
    digest = sha256_file(path)
    existing = session.scalar(
        select(DocumentVersion).where(DocumentVersion.sha256 == digest)
    )
    if existing:
        result.skipped = True
        result.reason = "already ingested (same sha256)"
        result.document_id = existing.document_id
        result.version_id = existing.id
        result.seconds_total = time.perf_counter() - started
        return result

    # --- 2. route by measurement, never by assumption --------------------
    pages, cls, load_report = load_pdf(path, convert_legacy=True)
    result.doc_class = cls.doc_class
    result.pages = len(pages)
    result.legacy_fonts = load_report.legacy_fonts
    result.spans_converted = load_report.spans_converted

    if settings.ingest_digital_only and cls.doc_class != DIGITAL:
        raise IngestionRefused(
            f"{path.name} is {cls.doc_class} ({cls.chars_per_page} chars/page). "
            f"M1 ingests DIGITAL documents only, where native extraction is "
            f"exact. OCR lands at M3; ingesting this now would store text at "
            f"the measured 0.307 CER and there would be no way to tell "
            f"afterwards which answers came from it."
        )

    # --- 3. store under a generated name, never the user's filename ------
    stored = settings.storage_dir / f"{uuid.uuid4()}{path.suffix.lower()}"
    shutil.copy2(path, stored)

    document = Document(
        title=title or path.stem,
        original_filename=path.name,
        department=department,
        owner_id=owner_id,
        is_public=is_public,
        status=DocStatus.processing,
    )
    session.add(document)
    session.flush()

    version = DocumentVersion(
        document_id=document.id,
        version_no=1,
        sha256=digest,
        storage_path=str(stored),
        mime_type="application/pdf",
        size_bytes=path.stat().st_size,
        page_count=len(pages),
        doc_class=cls.doc_class,
    )
    session.add(version)
    session.flush()
    document.current_version_id = version.id

    # --- 4. chunk with the embedding model's own tokeniser ---------------
    embedder = get_embedder()
    pieces = chunk_pages(
        pages,
        target_tokens=settings.chunk_target_tokens,
        overlap_tokens=settings.chunk_overlap_tokens,
        count_tokens=embedder.count_tokens,
    )
    if not pieces:
        raise IngestionRefused(f"{path.name} produced no chunks -- empty text layer?")

    # --- 5. reuse embeddings for chunks we have already seen -------------
    model, rev = embedding_identity()
    hashes = [p.sha256 for p in pieces]
    known: dict[str, list[float]] = {}
    if hashes:
        rows = session.execute(
            select(Chunk.content_sha256, Chunk.embedding)
            .where(Chunk.content_sha256.in_(hashes))
            .where(Chunk.embedding_model == model)
            .where(Chunk.embedding_rev == rev)
            .where(Chunk.embedding.is_not(None))
        ).all()
        known = {h: e for h, e in rows if h}

    todo = [p for p in pieces if p.sha256 not in known]
    embed_started = time.perf_counter()
    if todo:
        vectors = embedder.embed_documents([p.text for p in todo])
        for piece, vec in zip(todo, vectors, strict=True):
            known[piece.sha256] = vec
    result.seconds_embedding = time.perf_counter() - embed_started
    result.chunks_created = len(todo)
    result.chunks_reused = len(pieces) - len(todo)

    for piece in pieces:
        session.add(Chunk(
            document_id=document.id,
            version_id=version.id,
            chunk_index=piece.chunk_index,
            page_no=piece.page_no,
            section=piece.section,
            content=piece.text,
            content_sha256=piece.sha256,
            token_count=piece.token_count,
            lang=piece.lang,
            embedding=known[piece.sha256],
            embedding_model=model,
            embedding_rev=rev,
        ))

    document.status = DocStatus.ready
    version.ingested_at = datetime.now(timezone.utc)
    session.flush()

    result.document_id = document.id
    result.version_id = version.id
    result.seconds_total = time.perf_counter() - started
    return result