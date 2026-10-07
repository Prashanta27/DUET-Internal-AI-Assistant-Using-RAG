"""bge-m3 via sentence-transformers. The only module that loads the model.

The model is loaded lazily, on first use. Importing this module must stay
cheap so that tests and the API can import it without pulling ~2 GB into
memory.
"""

from __future__ import annotations

from config.settings import get_settings


class BGEM3Embeddings:
    name = "bge-m3"

    def __init__(self, model_path: str | None = None, device: str | None = None,
                 batch_size: int | None = None) -> None:
        s = get_settings()
        self.model_path = model_path or s.embedding_model_path
        self.device = device or s.embedding_device
        self.batch_size = batch_size or s.embedding_batch_size
        self.dim = s.embedding_dim
        self._model = None

    def _ensure(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_path, device=self.device)
            got = self._model.get_sentence_embedding_dimension()
            if got != self.dim:
                raise ValueError(
                    f"{self.model_path} produces {got}-dim vectors but the schema "
                    f"declares VECTOR({self.dim}). This is a migration plus a full "
                    f"re-embed, not a config change."
                )
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._ensure()
        vecs = model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,  # cosine distance in pgvector
            show_progress_bar=False,
        )
        return [v.tolist() for v in vecs]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def count_tokens(self, text: str) -> int:
        model = self._ensure()
        return len(model.tokenizer.encode(text, add_special_tokens=False))

    def health(self) -> tuple[bool, str]:
        try:
            self._ensure()
            return True, f"{self.model_path} on {self.device}, dim={self.dim}"
        except Exception as e:  # noqa: BLE001
            return False, str(e)[:200]
