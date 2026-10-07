"""Test fixtures.

Schema tests run against a REAL Postgres with pgvector, never SQLite.
Half of what this schema does -- vector columns, generated tsvector,
HNSW indexes, the XOR check constraint -- does not exist in SQLite, so a
SQLite test would pass while production is broken.

The URL comes from TEST_DATABASE_URL, or from .env when the environment
does not set it. If neither has it, those tests skip rather than fail; the
unit tests (import graph, settings, chunker) still run everywhere.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker


def _test_database_url() -> str | None:
    """Find the test database, preferring the environment over .env.

    Reading .env as a fallback matters more than it looks. An exported shell
    variable dies with the terminal, so "89 passed" on Monday becomes
    "82 passed, 7 skipped" on Tuesday with nothing having changed -- and a
    skipped schema test looks close enough to a passing one that you stop
    noticing. The database tests should run because the project is
    configured, not because you remembered to type an export.
    """
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        return url
    try:
        from config.settings import get_settings

        return get_settings().test_database_url
    except Exception:  # noqa: BLE001 -- no .env yet, or it is incomplete
        return None


TEST_URL = _test_database_url()


@pytest.fixture(scope="session")
def engine():
    if not TEST_URL:
        pytest.skip(
            "No test database. Set TEST_DATABASE_URL in .env (or the "
            "environment) and run: docker compose up -d"
        )
    eng = create_engine(TEST_URL, future=True)
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"test database unreachable: {e}")
    return eng


@pytest.fixture(scope="session")
def schema(engine):
    """Create the schema once per session, from the models."""
    from database.models import Base

    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        for name, labels in [
            ("user_role", ["admin", "teacher"]),
            ("doc_status", ["uploaded", "processing", "ready", "failed", "archived"]),
            ("chunk_kind", ["text", "table", "ocr_text", "caption"]),
            ("job_status", ["queued", "running", "done", "failed"]),
        ]:
            vals = ", ".join(f"'{x}'" for x in labels)
            conn.execute(text(
                f"DO $$ BEGIN CREATE TYPE {name} AS ENUM ({vals}); "
                f"EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
            ))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def db(engine, schema) -> Session:
    """A session inside a transaction that is rolled back after each test.

    join_transaction_mode="create_savepoint" matters here. Three of the
    schema tests deliberately provoke an IntegrityError and then call
    db.rollback() to continue. Without a savepoint that rollback unwinds the
    OUTER transaction too, so the fixture's own rollback finds nothing left
    to roll back -- which is the "transaction already deassociated from
    connection" warning, and, worse, means those tests are no longer
    isolated from each other. With savepoints the test rolls back only its
    own failed statement and the outer transaction survives to be discarded
    here.
    """
    conn = engine.connect()
    trans = conn.begin()
    session = sessionmaker(
        bind=conn,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )()
    try:
        yield session
    finally:
        session.close()
        if trans.is_active:
            trans.rollback()
        conn.close()