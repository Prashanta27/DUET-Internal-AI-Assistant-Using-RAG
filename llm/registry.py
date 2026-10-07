"""Chooses the backend from config. Import this, never a concrete backend."""

from __future__ import annotations

from functools import lru_cache

from config.settings import get_settings
from llm.base import LLMBackend
from llm.ollama import OllamaBackend

_BACKENDS = {"ollama": OllamaBackend}


@lru_cache
def get_llm() -> LLMBackend:
    name = get_settings().llm_backend
    try:
        return _BACKENDS[name]()
    except KeyError:
        raise ValueError(
            f"Unknown LLM_BACKEND={name!r}. Available: {sorted(_BACKENDS)}"
        ) from None
