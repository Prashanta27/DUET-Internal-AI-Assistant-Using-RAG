"""Ingestion pipeline tests against a real database.

These use a fake embedder so they run in under a second. Nothing here
measures embedding quality -- that is what the golden set is for. What they
pin is the behaviour that is easy to get wrong and silent when it is:
deduplication, embedding reuse, and refusing documents M1 cannot read.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from database.models import Chunk, Document, DocumentVersion, User, UserRole
from ingestion.pipeline import IngestionRefused, ingest_pdf, sha256_file

pytest.importorskip("pymupdf")


class FakeEmbedder:
    """Deterministic, instant, and 1024-dim. Real enough for plumbing."""

    name = "bge-m3"
    dim = 1024
    calls = 0

    def embed_documents(self, texts):
        FakeEmbedder.calls += len(texts)
        return [[float((hash(t) % 100)) / 100.0] * 1024 for t in texts]

    def embed_query(self, text):
        return self.embed_documents([text])[0]

    def count_tokens(self, text):
        return max(1, len(text) // 4)


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch):
    import ingestion.pipeline as pipeline

    monkeypatch.setattr(pipeline, "get_embedder", lambda: FakeEmbedder())
    FakeEmbedder.calls = 0


@pytest.fixture
def digital_pdf(tmp_path) -> Path:
    import pymupdf as pm

    doc = pm.open()
    for n in range(2):
        page = doc.new_page()
        y = 60
        for line in [f"DUET Academic Calendar 2026 page {n + 1}",
                     "Examination begins on 12 March 2026 for all programmes.",
                     "Registration closes on 28 February 2026. Late fees apply.",
                     "Results must be submitted within 21 days of the last exam."]:
            page.insert_text((50, y), line, fontsize=12, fontname="helv")
            y += 24
    out = tmp_path / "calendar.pdf"
    doc.save(out)
    doc.close()
    return out


@pytest.fixture
def owner(db) -> User:
    u = User(email="owner@duet.ac.bd", full_name="Owner",
             password_hash="x", role=UserRole.admin)
    db.add(u)
    db.flush()
    return u


def test_a_digital_pdf_becomes_chunks_with_citable_pages(db, digital_pdf, owner):
    r = ingest_pdf(db, digital_pdf, owner_id=owner.id)

    assert not r.skipped
    assert r.doc_class == "DIGITAL"
    assert r.chunks_created > 0

    chunks = db.scalars(select(Chunk).where(Chunk.document_id == r.document_id)).all()
    assert chunks
    for c in chunks:
        assert c.page_no in (1, 2)          # every citation resolves to a page
        assert c.embedding is not None
        assert len(c.embedding) == 1024
        assert c.content_sha256


def test_the_same_file_twice_does_no_work_the_second_time(db, digital_pdf, owner):
    first = ingest_pdf(db, digital_pdf, owner_id=owner.id)
    calls_after_first = FakeEmbedder.calls

    second = ingest_pdf(db, digital_pdf, owner_id=owner.id)

    assert second.skipped
    assert second.document_id == first.document_id
    assert FakeEmbedder.calls == calls_after_first, "re-embedded an identical file"
    assert db.scalar(select(func.count()).select_from(Document)) == 1


def test_identical_chunks_in_a_different_file_reuse_their_embedding(
    db, digital_pdf, owner, tmp_path
):
    """A re-issued notice with one corrected line must cost one embedding,
    not forty."""
    ingest_pdf(db, digital_pdf, owner_id=owner.id)
    before = FakeEmbedder.calls

    # same content, different bytes -> different sha256, same chunk hashes
    twin = tmp_path / "calendar_copy.pdf"
    twin.write_bytes(digital_pdf.read_bytes() + b"\n% cosmetic difference\n")
    assert sha256_file(twin) != sha256_file(digital_pdf)

    r = ingest_pdf(db, twin, owner_id=owner.id)
    assert not r.skipped
    assert r.chunks_reused > 0, "identical chunks were embedded again"
    assert FakeEmbedder.calls - before < r.chunks_reused + r.chunks_created


def test_a_scanned_document_is_refused_not_silently_ingested(db, owner, tmp_path):
    """Ingesting a scan now would store text at the measured 0.307 CER with
    no way to tell afterwards which answers came from it."""
    import pymupdf as pm

    src = pm.open()
    page = src.new_page()
    page.insert_text((50, 60), "Some text", fontsize=12, fontname="helv")
    pix = page.get_pixmap(dpi=100)
    out = pm.open()
    pg = out.new_page(width=pix.width, height=pix.height)
    pg.insert_image(pm.Rect(0, 0, pix.width, pix.height), pixmap=pix)
    scanned = tmp_path / "scan.pdf"
    out.save(scanned)
    out.close()
    src.close()

    with pytest.raises(IngestionRefused, match="DIGITAL"):
        ingest_pdf(db, scanned, owner_id=owner.id)


def test_a_refused_document_leaves_nothing_behind(db, owner, tmp_path):
    import pymupdf as pm

    empty = tmp_path / "blank.pdf"
    doc = pm.open()
    doc.new_page()
    doc.save(empty)
    doc.close()

    with pytest.raises(IngestionRefused):
        ingest_pdf(db, empty, owner_id=owner.id)
    db.rollback()
    assert db.scalar(select(func.count()).select_from(DocumentVersion)) == 0


def test_the_stored_file_is_named_by_uuid_not_by_the_user(db, digital_pdf, owner):
    """Path traversal defence: the original filename is a column, never a
    filesystem path."""
    r = ingest_pdf(db, digital_pdf, owner_id=owner.id)
    version = db.get(DocumentVersion, r.version_id)
    assert "calendar" not in Path(version.storage_path).name
    document = db.get(Document, r.document_id)
    assert document.original_filename == "calendar.pdf"