"""The embedding boundary.

Unlike the LLM, the embedding model is NOT stateless. Every vector in the
database lives in one model's space, and vectors from two models cannot be
compared. Changing EMBEDDING_MODEL means re-embedding every chunk; changing
the dimension means a migration as well.

That asymmetry is why this interface exists separately from LLMBackend, and
why every chunk records embedding_model and embedding_rev.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingBackend(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        ...

    def embed_query(self, text: str) -> list[float]:
        ...

    def count_tokens(self, text: str) -> int:
        """Chunk sizes are measured with the embedding model's own tokeniser,
        not by whitespace. A Bangla word is several tokens."""
        ...
