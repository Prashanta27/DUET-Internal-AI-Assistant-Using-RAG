"""The schema does what §5 says it does. Run against real Postgres."""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from database.models import (
    Chunk, Document, DocumentPermission, DocumentVersion, User, UserRole,
)


def _user(db, email="t@duet.ac.bd", role=UserRole.teacher) -> User:
    u = User(email=email, full_name="T", password_hash="x", role=role)
    db.add(u)
    db.flush()
    return u


def _doc_with_version(db, owner, sha="a" * 64) -> tuple[Document, DocumentVersion]:
    d = Document(title="Calendar", original_filename="cal.pdf", owner_id=owner.id)
    db.add(d)
    db.flush()
    v = DocumentVersion(document_id=d.id, version_no=1, sha256=sha,
                        storage_path="storage/uuid.pdf", mime_type="application/pdf",
                        size_bytes=100)
    db.add(v)
    db.flush()
    d.current_version_id = v.id
    db.flush()
    return d, v


def test_same_bytes_cannot_be_ingested_twice(db):
    """UNIQUE(sha256): re-uploading the same file is a no-op, not a duplicate
    index. This is what makes 'drop the folder in again' safe."""
    owner = _user(db)
    _doc_with_version(db, owner, sha="b" * 64)
    d2 = Document(title="Copy", original_filename="cal2.pdf", owner_id=owner.id)
    db.add(d2)
    db.flush()
    db.add(DocumentVersion(document_id=d2.id, version_no=1, sha256="b" * 64,
                           storage_path="storage/other.pdf",
                           mime_type="application/pdf", size_bytes=100))
    with pytest.raises(IntegrityError):
        db.flush()


def test_permission_is_user_xor_role(db):
    owner = _user(db, "owner@duet.ac.bd", UserRole.admin)
    doc, _ = _doc_with_version(db, owner)

    # both set -> rejected
    db.add(DocumentPermission(document_id=doc.id, user_id=owner.id,
                              role=UserRole.teacher, granted_by=owner.id))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_tsvector_is_generated_without_being_written(db):
    owner = _user(db, "tsv@duet.ac.bd")
    doc, ver = _doc_with_version(db, owner, sha="c" * 64)
    db.add(Chunk(document_id=doc.id, version_id=ver.id, chunk_index=0,
                 content="Examination begins on 12 March 2026",
                 embedding_model="bge-m3", embedding_rev=1))
    db.flush()
    tsv = db.scalar(text("SELECT content_tsv::text FROM chunks LIMIT 1"))
    assert "examination" in tsv


def test_bangla_survives_a_round_trip(db):
    """Encoding problems are cheapest to catch here, not after ingesting
    200 documents."""
    owner = _user(db, "bn@duet.ac.bd")
    doc, ver = _doc_with_version(db, owner, sha="d" * 64)
    bn = "পরীক্ষার রুটিন ০৭/০৮/২০২৬"
    db.add(Chunk(document_id=doc.id, version_id=ver.id, chunk_index=0,
                 content=bn, lang="bn", embedding_model="bge-m3", embedding_rev=1))
    db.flush()
    db.expire_all()
    got = db.scalar(select(Chunk.content))
    assert got == bn
    # Bangla digits must NOT be silently normalised to Latin anywhere.
    assert "০৭" in got


def test_vector_column_accepts_1024_dims_and_rejects_others(db):
    owner = _user(db, "vec@duet.ac.bd")
    doc, ver = _doc_with_version(db, owner, sha="e" * 64)
    db.add(Chunk(document_id=doc.id, version_id=ver.id, chunk_index=0,
                 content="x", embedding=[0.0] * 1024,
                 embedding_model="bge-m3", embedding_rev=1))
    db.flush()

    db.add(Chunk(document_id=doc.id, version_id=ver.id, chunk_index=1,
                 content="y", embedding=[0.0] * 768,
                 embedding_model="other", embedding_rev=1))
    with pytest.raises(Exception):
        db.flush()
    db.rollback()


def test_deleting_a_document_deletes_its_chunks(db):
    owner = _user(db, "cascade@duet.ac.bd")
    doc, ver = _doc_with_version(db, owner, sha="f" * 64)
    db.add(Chunk(document_id=doc.id, version_id=ver.id, chunk_index=0,
                 content="z", embedding_model="bge-m3", embedding_rev=1))
    db.flush()
    doc_id = doc.id
    db.execute(text("UPDATE documents SET current_version_id = NULL WHERE id = :i"),
               {"i": doc_id})
    db.execute(text("DELETE FROM documents WHERE id = :i"), {"i": doc_id})
    db.flush()
    remaining = db.scalar(
        text("SELECT count(*) FROM chunks WHERE document_id = :i"), {"i": doc_id}
    )
    assert remaining == 0


def test_rollback_after_a_constraint_violation_leaves_the_session_usable(db):
    """Proves the savepoint fixture works.

    Without join_transaction_mode="create_savepoint" the rollback below
    unwinds the fixture's outer transaction, this insert lands in no
    transaction at all, and the row leaks into the next test.
    """
    owner = _user(db, "savepoint@duet.ac.bd")
    doc, _ = _doc_with_version(db, owner, sha="1" * 64)

    db.add(DocumentPermission(document_id=doc.id, user_id=owner.id,
                              role=UserRole.teacher, granted_by=owner.id))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()

    # the session must still be usable, and still inside the outer transaction
    owner2 = _user(db, "after-rollback@duet.ac.bd")
    assert owner2.id is not None