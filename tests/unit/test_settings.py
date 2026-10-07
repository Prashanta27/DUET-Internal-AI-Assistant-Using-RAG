from __future__ import annotations

import pytest


def test_embedding_dim_cannot_drift_from_the_schema(monkeypatch):
    """A wrong EMBEDDING_DIM in .env must fail loudly at startup, not
    produce a database full of unusable vectors."""
    from config.settings import Settings

    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    monkeypatch.setenv("EMBEDDING_DIM", "768")
    with pytest.raises(ValueError, match="re-embed"):
        Settings(_env_file=None)


def test_query_text_logging_is_off_by_default(monkeypatch):
    from config.settings import Settings

    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    s = Settings(_env_file=None)
    assert s.log_query_text is False
    assert s.ingest_digital_only is True  # M1 default
