"""The OCR boundary.

M1 ingests DIGITAL documents only, where native extraction is exact, so no
engine is called yet. The interface exists now because the M0 bake-off
measured mean CER 0.307 / median 0.246 on scanned pages with Tesseract --
right at the "unusable" threshold. The engine decision is deferred to M3,
when server hardware is known, and deferring a decision is only cheap if
the seam is already there.

The adapters in m0_reality_check/ocr_bakeoff.py are promoted into this
package at M3. They already have this shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class OCRResult:
    text: str
    engine: str
    seconds: float = 0.0
    #: mean per-line confidence where the engine reports one, else None
    confidence: float | None = None
    meta: dict = field(default_factory=dict)


@runtime_checkable
class OCREngine(Protocol):
    name: str

    def available(self) -> tuple[bool, str]:
        ...

    def extract(self, image_path: Path) -> OCRResult:
        ...
