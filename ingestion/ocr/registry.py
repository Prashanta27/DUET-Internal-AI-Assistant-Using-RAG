from __future__ import annotations

from functools import lru_cache

from config.settings import get_settings
from ingestion.ocr.base import OCREngine
from ingestion.ocr.tesseract import TesseractEngine

_ENGINES = {"tesseract": TesseractEngine}


@lru_cache
def get_ocr_engine() -> OCREngine:
    name = get_settings().ocr_engine
    try:
        return _ENGINES[name]()
    except KeyError:
        raise ValueError(
            f"Unknown OCR_ENGINE={name!r}. Available: {sorted(_ENGINES)}"
        ) from None
