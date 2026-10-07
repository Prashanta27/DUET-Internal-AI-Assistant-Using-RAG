"""Engine and session factory. The only module that constructs an engine."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from config.settings import get_settings

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        s = get_settings()
        _engine = create_engine(
            s.database_url,
            echo=s.db_echo,
            pool_pre_ping=True,
            future=True,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(), autoflush=False, expire_on_commit=False
        )
    return _SessionLocal


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def tune_vector_search(session: Session) -> None:
    """Per-session pgvector settings for filtered ANN search.

    An HNSW scan combined with a WHERE clause can return fewer than k rows
    when the permission filter is selective. Iterative scan plus a larger
    ef_search is the documented fix. Verify with a filtered-search test
    rather than assuming -- see tests/security/.
    """
    s = get_settings()
    session.execute(text(f"SET LOCAL hnsw.ef_search = {int(s.hnsw_ef_search)}"))
    session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
