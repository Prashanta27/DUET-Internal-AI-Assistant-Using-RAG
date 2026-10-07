"""DIGITAL / MIXED / SCANNED classification.

Measured characters per page decides the route -- never assume. This is the
same measurement the M0 bake-off printed, reused in production.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config.settings import get_settings

DIGITAL = "DIGITAL"
MIXED = "MIXED"
SCANNED = "SCANNED"
UNKNOWN = "UNKNOWN"


@dataclass
class Classification:
    doc_class: str
    chars_per_page: float | None
    page_count: int | None

    @property
    def needs_ocr(self) -> bool:
        return self.doc_class in (MIXED, SCANNED, UNKNOWN)


def _import_pymupdf():
    """PyMuPDF renamed its module from `fitz` to `pymupdf`. Support both."""
    try:
        import pymupdf

        return pymupdf
    except ImportError:
        try:
            import fitz

            return fitz
        except ImportError:
            return None


def classify_pdf(path: Path, sample_pages: int = 5) -> Classification:
    s = get_settings()
    fitz = _import_pymupdf()
    if fitz is None:
        return Classification(UNKNOWN, None, None)
    try:
        with fitz.open(path) as doc:
            n = len(doc)
            if n == 0:
                return Classification(UNKNOWN, None, 0)
            sample = min(n, sample_pages)
            chars = sum(len(doc[i].get_text().strip()) for i in range(sample))
            cpp = chars / sample
    except Exception:  # noqa: BLE001
        return Classification(UNKNOWN, None, None)

    if cpp >= s.digital_chars_per_page:
        cls = DIGITAL
    elif cpp < s.scanned_chars_per_page:
        cls = SCANNED
    else:
        cls = MIXED
    return Classification(cls, round(cpp, 1), n)
