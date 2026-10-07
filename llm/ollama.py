"""Ollama backend. The only module that knows Ollama exists."""

from __future__ import annotations

import httpx

from config.settings import get_settings
from llm.base import LLMError


class OllamaBackend:
    name = "ollama"

    def __init__(self, model: str | None = None, host: str | None = None,
                 timeout: int | None = None) -> None:
        s = get_settings()
        self.model = model or s.llm_model
        self.host = (host or s.ollama_host).rstrip("/")
        self.timeout = timeout or s.llm_timeout_s

    def generate(self, system: str, user: str, *, temperature: float = 0.0,
                 max_tokens: int = 1024) -> str:
        payload = {
            "model": self.model,
            "system": system,
            "prompt": user,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                # Deterministic decoding. Every reported number must be
                # reproducible from a committed script.
                "top_p": 1.0,
                "seed": 0,
            },
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                r = client.post(f"{self.host}/api/generate", json=payload)
                r.raise_for_status()
                return r.json().get("response", "")
        except httpx.HTTPError as e:
            raise LLMError(f"Ollama request failed ({self.host}, {self.model}): {e}") from e

    def health(self) -> tuple[bool, str]:
        try:
            with httpx.Client(timeout=5) as client:
                r = client.get(f"{self.host}/api/tags")
                r.raise_for_status()
                names = [m.get("name", "") for m in r.json().get("models", [])]
        except httpx.HTTPError as e:
            return False, f"unreachable at {self.host}: {e}"
        base = self.model.split(":")[0]
        if not any(n == self.model or n.startswith(base) for n in names):
            return False, f"model '{self.model}' not pulled (ollama pull {self.model})"
        return True, f"model={self.model}"
