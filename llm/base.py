"""The LLM boundary.

The LLM is stateless: nothing stored in the database depends on which model
answered. Swapping a 3B laptop model for a 32B server model is a config
change and costs nothing. That is only true while every caller goes through
this interface.

No module outside llm/ may import an LLM client library or read
settings.llm_model. tests/test_import_graph.py enforces it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class LLMError(RuntimeError):
    pass


@runtime_checkable
class LLMBackend(Protocol):
    name: str

    def generate(self, system: str, user: str, *, temperature: float = 0.0,
                 max_tokens: int = 1024) -> str:
        ...

    def health(self) -> tuple[bool, str]:
        """(reachable, detail) -- used by GET /health."""
        ...
