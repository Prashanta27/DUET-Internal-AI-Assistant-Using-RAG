"""Application settings.

Every value comes from the environment. There are no hard-coded paths,
hosts or secrets anywhere else in the codebase.

IMPORTANT: the model-related fields below (llm_model, embedding_model,
ocr_engine, reranker_model) may only be read inside llm/, embeddings/ and
ingestion/ocr/. tests/test_import_graph.py enforces this. An abstraction
that is not tested is an abstraction that gets bypassed under deadline
pressure.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The embedding dimension is fixed by the database schema (VECTOR(1024)).
# Changing it is an Alembic migration plus a full re-embed, not a config
# change. bge-m3 and multilingual-e5-large are both 1024 -- staying inside
# that family keeps every future embedding change to a reindex.
SCHEMA_EMBEDDING_DIM = 1024

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- app ---------------------------------------------------------
    app_env: str = "dev"
    debug: bool = False
    secret_key: str = Field(..., min_length=32)

    # --- database ----------------------------------------------------
    database_url: str
    test_database_url: str | None = None
    db_echo: bool = False

    # --- storage -----------------------------------------------------
    # Files are stored under a generated UUID name, never the user's
    # filename. The original filename is a database column only.
    storage_dir: Path = REPO_ROOT / "storage" / "documents"
    page_cache_dir: Path = REPO_ROOT / "storage" / "page_cache"
    page_render_dpi: int = 150
    page_cache_max_mb: int = 512
    max_upload_mb: int = 100

    # --- LLM: swap freely, nothing stored depends on it --------------
    llm_backend: str = "ollama"
    llm_model: str = "llama3.2:3b"
    ollama_host: str = "http://localhost:11434"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 1024
    llm_timeout_s: int = 180

    # --- embeddings: changing these costs a full reindex -------------
    embedding_model: str = "bge-m3"
    embedding_model_path: str = "BAAI/bge-m3"
    embedding_dim: int = SCHEMA_EMBEDDING_DIM
    embedding_rev: int = 1
    embedding_batch_size: int = 8
    embedding_device: str = "cpu"

    # --- reranker: free to toggle, query-time only -------------------
    reranker_enabled: bool = False
    reranker_model: str = "BAAI/bge-reranker-v2-m3"

    # --- ingestion ---------------------------------------------------
    ocr_engine: str = "tesseract"
    ocr_langs: str = "ben+eng"
    ingest_digital_only: bool = True  # M1 default; false from M3
    digital_chars_per_page: int = 200  # >= this -> DIGITAL, no OCR
    scanned_chars_per_page: int = 20   # <  this -> SCANNED, OCR every page

    # --- chunking: pinned, do not drift ------------------------------
    chunk_target_tokens: int = 450
    chunk_overlap_tokens: int = 80

    # --- retrieval ---------------------------------------------------
    dense_top_k: int = 30
    lexical_top_k: int = 30
    rrf_k: int = 60
    final_top_k: int = 6
    hnsw_ef_search: int = 200
    refusal_score_threshold: float = 0.30

    # --- auth --------------------------------------------------------
    access_token_minutes: int = 15
    refresh_token_days: int = 7

    # --- logging -----------------------------------------------------
    log_query_text: bool = False  # when false, only a question hash is stored
    log_level: str = "INFO"

    @field_validator("embedding_dim")
    @classmethod
    def _dim_must_match_schema(cls, v: int) -> int:
        if v != SCHEMA_EMBEDDING_DIM:
            raise ValueError(
                f"EMBEDDING_DIM={v} but the schema declares "
                f"VECTOR({SCHEMA_EMBEDDING_DIM}). Changing the dimension "
                f"requires an Alembic migration AND a full re-embed of every "
                f"chunk. Do that deliberately, not by editing .env."
            )
        return v

    @field_validator("storage_dir", "page_cache_dir")
    @classmethod
    def _absolute(cls, v: Path) -> Path:
        return v if v.is_absolute() else (REPO_ROOT / v).resolve()

    def ensure_dirs(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.page_cache_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
