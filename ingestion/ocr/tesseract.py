"""Tesseract adapter, promoted from the M0 bake-off harness.

M0 measurement on real DUET scans: mean CER 0.307, median 0.246. Usable for
nothing yet -- recorded here so the number travels with the code.
"""

from __future__ import annotations

import time
from pathlib import Path

from config.settings import get_settings
from ingestion.ocr.base import OCRResult


class TesseractEngine:
    name = "tesseract"

    def __init__(self, langs: str | None = None, psm: int = 3) -> None:
        self.langs = langs or get_settings().ocr_langs
        self.psm = psm
        self._t = None

    def available(self) -> tuple[bool, str]:
        try:
            import pytesseract
        except ImportError as e:
            return False, f"pip install pytesseract pillow ({e})"
        try:
            installed = pytesseract.get_languages(config="")
        except Exception as e:  # noqa: BLE001
            return False, f"tesseract binary not on PATH ({e})"
        missing = [x for x in self.langs.split("+") if x not in installed]
        if missing:
            return False, f"missing traineddata: {missing} (apt install tesseract-ocr-ben)"
        self._t = pytesseract
        return True, f"langs={self.langs}"

    def extract(self, image_path: Path) -> OCRResult:
        ok, detail = self.available()
        if not ok:
            raise RuntimeError(f"tesseract unavailable: {detail}")
        from PIL import Image

        t0 = time.perf_counter()
        with Image.open(image_path) as im:
            text = self._t.image_to_string(im, lang=self.langs,
                                           config=f"--psm {self.psm}")
        return OCRResult(text=text, engine=self.name,
                         seconds=round(time.perf_counter() - t0, 3))
