"""Enforce the model abstraction boundaries.

ARCHITECTURE.md §9: "No module outside llm/, embeddings/ and
ingestion/ocr/ may import Ollama, sentence-transformers or any OCR library.
Enforce this with a test that greps the import graph -- it is the only
reliable way to keep an abstraction honest over three months."

This test is that grep. It runs without a database, without a model and
without the network, so there is no excuse to skip it.

When it fails the fix is almost never to widen ALLOWED_PACKAGES. It is to
move the code behind the interface.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# The application code this test governs, named explicitly.
#
# This is a whitelist on purpose. The first version blacklisted ".venv" and
# "venv", and a virtualenv named anything else -- duet_ai_assistant/, env/,
# a stray site-packages -- walked straight through the filter and got
# scanned as if it were project code. easyocr's own craft.py really does
# import torch, so the test failed hundreds of times while telling you
# nothing about your code.
#
# A blacklist has to anticipate every directory that should not be scanned.
# A whitelist only has to name the ones that should. Adding a package here
# is a deliberate act; forgetting to is caught by
# test_every_application_package_is_scanned below.
APP_PACKAGES = (
    "api", "config", "database", "embeddings", "ingestion", "jobs",
    "llm", "rag", "retrieval", "security",
)

# Loose .py files that are application code but not importable packages.
APP_SCRIPT_DIRS = ("scripts",)

# Package-looking directories at the repo root that are deliberately NOT
# governed by these rules, with the reason.
UNGOVERNED = {
    "tests": "the tests themselves",
    "migrations": "alembic templates and generated revisions",
    "m0_reality_check": "the M0 harness -- it imports every OCR engine by design",
    "evaluation": "benchmark scripts may load models directly",
}

# top-level module name -> package prefixes allowed to import it
MODEL_LIBRARIES: dict[str, tuple[str, ...]] = {
    # LLM
    "ollama": ("llm",),
    # embeddings and reranking
    "sentence_transformers": ("embeddings", "retrieval"),
    "FlagEmbedding": ("embeddings", "retrieval"),
    "transformers": ("embeddings", "retrieval"),
    "torch": ("embeddings", "retrieval"),
    # OCR
    "pytesseract": ("ingestion.ocr",),
    # legacy Bangla font map: one adapter, same rule as the OCR engines
    "bijoy2unicode": ("ingestion.legacy_convert",),
    "easyocr": ("ingestion.ocr",),
    "surya": ("ingestion.ocr",),
    "paddleocr": ("ingestion.ocr",),
}

# settings attribute -> package prefixes allowed to read it
MODEL_SETTINGS: dict[str, tuple[str, ...]] = {
    "llm_model": ("llm",),
    "llm_backend": ("llm",),
    "ollama_host": ("llm",),
    "embedding_model_path": ("embeddings",),
    "embedding_device": ("embeddings",),
    "reranker_model": ("retrieval",),
    "ocr_engine": ("ingestion.ocr",),
    "ocr_langs": ("ingestion.ocr",),
}


def _module_name(path: Path) -> str:
    rel = path.relative_to(REPO_ROOT).with_suffix("")
    parts = [p for p in rel.parts if p != "__init__"]
    return ".".join(parts)


def _is_virtualenv(d: Path) -> bool:
    return (d / "pyvenv.cfg").exists() or (d / "Scripts" / "activate").exists()


def _source_files() -> list[Path]:
    out: list[Path] = []
    for name in APP_PACKAGES + APP_SCRIPT_DIRS:
        root = REPO_ROOT / name
        if not root.is_dir():
            continue
        for p in root.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            out.append(p)
    return sorted(out)


def _allowed(module: str, prefixes: tuple[str, ...]) -> bool:
    return any(module == p or module.startswith(p + ".") for p in prefixes)


def _imported_top_levels(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def test_source_files_are_discovered() -> None:
    """A grep that greps nothing always passes. Guard against that."""
    files = _source_files()
    assert len(files) >= 10, (
        f"only found {len(files)} source files -- is APP_PACKAGES still correct?"
    )


def test_no_third_party_code_is_scanned() -> None:
    """The scan must never reach site-packages.

    If it does, every failure is about somebody else's library and the real
    signal is buried. This is the exact bug the whitelist replaced.
    """
    strays = [str(p.relative_to(REPO_ROOT)) for p in _source_files()
              if "site-packages" in p.parts or "Lib" in p.parts]
    assert not strays, f"scanning third-party code: {strays[:5]}"


def test_every_application_package_is_scanned() -> None:
    """A whitelist silently stops governing code you forget to add to it.

    Any new importable package at the repo root must be listed in either
    APP_PACKAGES or UNGOVERNED -- with a reason, deliberately.
    """
    known = set(APP_PACKAGES) | set(APP_SCRIPT_DIRS) | set(UNGOVERNED)
    missing = []
    for d in REPO_ROOT.iterdir():
        if not d.is_dir() or d.name.startswith(".") or d.name in known:
            continue
        if _is_virtualenv(d):
            continue
        if (d / "__init__.py").exists():
            missing.append(d.name)
    assert not missing, (
        f"package(s) {missing} are not covered by the import-graph test. "
        f"Add them to APP_PACKAGES, or to UNGOVERNED with a reason."
    )


@pytest.mark.parametrize("path", _source_files(), ids=_module_name)
def test_no_model_library_outside_its_package(path: Path) -> None:
    module = _module_name(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    violations = []
    for name in _imported_top_levels(tree):
        allowed = MODEL_LIBRARIES.get(name)
        if allowed is None:
            continue
        if not _allowed(module, allowed):
            violations.append(
                f"{module} imports {name!r}; only {allowed} may. "
                f"Go through the interface instead."
            )
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize("path", _source_files(), ids=_module_name)
def test_no_model_setting_read_outside_its_package(path: Path) -> None:
    module = _module_name(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        allowed = MODEL_SETTINGS.get(node.attr)
        if allowed is None:
            continue
        if module.startswith("config"):
            continue
        if not _allowed(module, allowed):
            violations.append(
                f"{module}:{node.lineno} reads settings.{node.attr}; "
                f"only {allowed} may."
            )
    assert not violations, "\n".join(violations)


def test_embedding_dimension_is_pinned_in_one_place() -> None:
    """The schema, the settings validator and the model must agree on 1024.

    They are three files, so they can drift. A drift here is not a crash --
    it is silently wrong retrieval, which is worse.
    """
    from config.settings import SCHEMA_EMBEDDING_DIM
    from database.models import EMBEDDING_DIM

    migration = (REPO_ROOT / "migrations" / "versions" /
                 "0001_initial_schema.py").read_text(encoding="utf-8")

    assert SCHEMA_EMBEDDING_DIM == EMBEDDING_DIM == 1024
    assert "EMBEDDING_DIM = 1024" in migration