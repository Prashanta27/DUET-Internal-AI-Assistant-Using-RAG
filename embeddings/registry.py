from __future__ import annotations

from functools import lru_cache

from config.settings import get_settings
from embeddings.base import EmbeddingBackend
from embeddings.bge_m3 import BGEM3Embeddings

_BACKENDS = {"bge-m3": BGEM3Embeddings}


@lru_cache
def get_embedder() -> EmbeddingBackend:
    name = get_settings().embedding_model
    try:
        return _BACKENDS[name]()
    except KeyError:
        raise ValueError(
            f"Unknown EMBEDDING_MODEL={name!r}. Available: {sorted(_BACKENDS)}. "
            f"Adding one is fine -- but changing the configured model requires "
            f"running scripts/reindex.py afterwards."
        ) from None


def embedding_identity() -> tuple[str, int]:
    """(embedding_model, embedding_rev) -- written onto every chunk."""
    s = get_settings()
    return s.embedding_model, s.embedding_rev
