#!/usr/bin/env python3
"""
M0 / Step 2 -- OCR bake-off for the DUET local RAG assistant.

WHY THIS EXISTS
---------------
OCR quality is the ceiling of the entire system. If OCR turns a Bangla notice
into garbage, no embedding model, reranker or LLM can recover it. So before
writing a single line of the RAG pipeline, measure which local OCR engine
actually reads YOUR documents.

WHAT IT DOES
------------
1. Rasterises sample PDFs / images into page images.
2. Runs every available local OCR engine over every page, timing each one.
3. Compares each engine's output against hand-typed ground truth.
4. Reports CER (character error rate), WER (word error rate) and sec/page.

ENGINES
-------
  tesseract  Tesseract 5 with ben+eng traineddata. CPU-friendly baseline.
  easyocr    EasyOCR with the 'bn' model. Slower, often better on noisy scans.
  surya      Surya OCR. Modern multilingual detection + recognition.
  vlm        Any vision model served by your local Ollama (e.g. qwen2.5vl).
             Slowest, but usually the best at preserving table structure.
  paddle     PaddleOCR. NOTE: PaddleOCR has no official Bangla recognition
             model -- include it only to score your ENGLISH-language pages,
             or for its PP-Structure layout/table detection (language-agnostic).

Any engine whose package is not installed is skipped automatically. Install
only what you want to test.

TYPICAL RUN
-----------
    # 0. put 8-15 representative documents in samples/
    #    (mix: digital PDF, clean scan, bad photocopy, a page with a table)

    python ocr_bakeoff.py --make-truth-stubs
    # -> creates one empty .txt per page in ground_truth/. Type the correct
    #    text into 10-20 of them by hand. This is the boring part. Do it.

    python ocr_bakeoff.py --engines tesseract,easyocr,surya
    python ocr_bakeoff.py --engines vlm --vlm-model qwen2.5vl:7b

    # Shortcut for typing ground truth: generate a draft from one engine,
    # then CORRECT it by hand before moving it into ground_truth/.
    python ocr_bakeoff.py --engines tesseract --seed-truth-from tesseract

READING THE RESULT
------------------
    CER < 0.05   excellent -- usable as-is
    CER 0.05-0.15  usable, but expect retrieval misses on rare terms
    CER 0.15-0.30  marginal -- retrieval will suffer badly
    CER > 0.30   unusable -- change engine, or drop scanned docs from scope

Report the numbers you measure. Never estimate them.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import re
import os
import statistics
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
PDF_EXTS = {".pdf"}

# Zero-width and formatting characters that OCR engines emit inconsistently
# in Bangla text. They must not count as errors.
INVISIBLES = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u00ad\u2060"), None)

PUNCT_MAP = {
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u2212": "-",
    # Danda variants. OCR engines emit the Bangla full stop inconsistently as
    # U+0964, U+09F7 or an ASCII pipe. They are visually near-identical and
    # carry the same meaning, so counting them as errors would inflate CER
    # by roughly one error per sentence without telling you anything about
    # whether the text is usable. Normalised to a single form.
    "|": "\u0964",
    "\u09f7": "\u0964",
    "\u0965": "\u0964",      # double danda -> danda
}

# Bangla and Latin digits are NOT normalised against each other. A notice that
# says 07 is not the same as one that says 09, and this project exists to
# answer questions about exactly those numbers.


# ==========================================================================
# Text normalisation + metrics
# ==========================================================================

def normalise(text: str, fold_case: bool = True) -> str:
    """Canonicalise text so cosmetic differences don't count as OCR errors."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = text.translate(INVISIBLES)
    for src, dst in PUNCT_MAP.items():
        text = text.replace(src, dst)
    if fold_case:
        text = text.lower()
    # collapse all whitespace runs to a single space
    return " ".join(text.split())


def _levenshtein(a: str, b: str) -> int:
    """Edit distance. Uses rapidfuzz when available, else a two-row DP."""
    try:
        from rapidfuzz.distance import Levenshtein  # type: ignore

        return Levenshtein.distance(a, b)
    except Exception:
        pass
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _token_levenshtein(a: list[str], b: list[str]) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ta in enumerate(a, 1):
        cur = [i]
        for j, tb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ta != tb)))
        prev = cur
    return prev[-1]


def looks_restructured(text: str) -> str | None:
    """Heuristic: does this ground-truth file look like a reformatted summary
    rather than a literal transcription?

    These checks are cheap and catch the common failure: pasting an AI
    assistant's tidied-up reading of a page into ground_truth/. That inflates
    CER enormously while telling you nothing about the OCR engine.
    """
    if re.search(r"^\s*\|?\s*:?-{3,}", text, re.M):
        return "contains a markdown table separator"
    if re.search(r"^#{1,6}\s", text, re.M):
        return "contains markdown headings"
    for ch in text:
        o = ord(ch)
        if 0x1F300 <= o <= 0x1FAFF or 0x2700 <= o <= 0x27BF:
            return f"contains an emoji ({ch!r})"
    if "**" in text:
        return "contains markdown bold markers"
    return None


def cer(ref: str, hyp: str, fold_case: bool = True) -> float:
    """Character error rate: edit distance / reference length."""
    ref_n, hyp_n = normalise(ref, fold_case), normalise(hyp, fold_case)
    if not ref_n:
        return 0.0 if not hyp_n else 1.0
    return _levenshtein(ref_n, hyp_n) / len(ref_n)


def wer(ref: str, hyp: str, fold_case: bool = True) -> float:
    """Word error rate over whitespace-separated tokens."""
    ref_t = normalise(ref, fold_case).split()
    hyp_t = normalise(hyp, fold_case).split()
    if not ref_t:
        return 0.0 if not hyp_t else 1.0
    return _token_levenshtein(ref_t, hyp_t) / len(ref_t)


# ==========================================================================
# Engines
# ==========================================================================

class Engine:
    name = "base"

    def available(self) -> tuple[bool, str]:
        return False, "not implemented"

    def run(self, image_path: Path) -> str:
        raise NotImplementedError


class TesseractEngine(Engine):
    name = "tesseract"

    def __init__(self, langs: str = "ben+eng", psm: int = 3):
        self.langs, self.psm = langs, psm
        self._t = None

    def available(self):
        try:
            import pytesseract  # type: ignore
            from PIL import Image  # noqa: F401
        except ImportError as e:
            return False, f"pip install pytesseract pillow ({e})"
        try:
            langs = pytesseract.get_languages(config="")
        except Exception as e:
            return False, f"tesseract binary not found or not on PATH ({e})"
        missing = [l for l in self.langs.split("+") if l not in langs]
        if missing:
            return False, (f"missing traineddata: {missing}. Install the Bengali pack "
                           f"(Linux: apt install tesseract-ocr-ben / "
                           f"Windows: tick Bengali in the installer)")
        self._t = pytesseract
        return True, f"langs={self.langs}"

    def run(self, image_path: Path) -> str:
        from PIL import Image  # type: ignore

        with Image.open(image_path) as im:
            return self._t.image_to_string(
                im, lang=self.langs, config=f"--psm {self.psm}"
            )


class EasyOCREngine(Engine):
    name = "easyocr"

    def __init__(self, langs: tuple[str, ...] = ("bn", "en"), gpu: bool = True):
        self.langs, self.gpu = list(langs), gpu
        self._reader = None

    def available(self):
        try:
            import easyocr  # type: ignore  # noqa: F401
        except ImportError as e:
            return False, f"pip install easyocr ({e})"
        return True, f"langs={'+'.join(self.langs)} (first run downloads models)"

    def _ensure(self):
        if self._reader is None:
            import easyocr  # type: ignore

            self._reader = easyocr.Reader(self.langs, gpu=self.gpu, verbose=False)

    def run(self, image_path: Path) -> str:
        self._ensure()
        # Pass pixels, not a path. EasyOCR internally uses cv2.imread(), which
        # cannot open non-ASCII paths on Windows -- every Bangla-named file
        # fails with "NoneType has no attribute shape". Reading through PIL
        # sidesteps it entirely.
        import numpy as np
        from PIL import Image

        with Image.open(image_path) as im:
            arr = np.array(im.convert("RGB"))
        # detail=0 -> plain strings; paragraph=True groups lines sensibly
        lines = self._reader.readtext(arr, detail=0, paragraph=True)
        return "\n".join(lines)


class SuryaEngine(Engine):
    name = "surya"

    def __init__(self, langs: tuple[str, ...] = ("bn", "en")):
        self.langs = list(langs)
        self._rec = None
        self._det = None

    def available(self):
        try:
            import surya  # type: ignore  # noqa: F401
        except ImportError as e:
            return False, f"pip install surya-ocr ({e})"
        return True, f"langs={'+'.join(self.langs)}"

    def _ensure(self):
        if self._rec is not None:
            return
        # Surya's public API has moved between releases; try newest first.
        try:
            from surya.detection import DetectionPredictor  # type: ignore
            from surya.recognition import RecognitionPredictor  # type: ignore

            self._rec = RecognitionPredictor()
            self._det = DetectionPredictor()
            self._api = "predictor"
            return
        except Exception:
            pass
        from surya.model.detection.model import (  # type: ignore
            load_model as load_det, load_processor as load_det_proc,
        )
        from surya.model.recognition.model import load_model as load_rec  # type: ignore
        from surya.model.recognition.processor import load_processor as load_rec_proc  # type: ignore

        self._det = (load_det(), load_det_proc())
        self._rec = (load_rec(), load_rec_proc())
        self._api = "legacy"

    def run(self, image_path: Path) -> str:
        from PIL import Image  # type: ignore

        self._ensure()
        image = Image.open(image_path).convert("RGB")
        if self._api == "predictor":
            preds = self._rec([image], [self.langs], self._det)
        else:
            from surya.ocr import run_ocr  # type: ignore

            det_model, det_proc = self._det
            rec_model, rec_proc = self._rec
            preds = run_ocr([image], [self.langs], det_model, det_proc,
                            rec_model, rec_proc)
        out = []
        for page in preds:
            for line in getattr(page, "text_lines", []):
                out.append(getattr(line, "text", ""))
        return "\n".join(out)


class PaddleEngine(Engine):
    """PaddleOCR. No official Bangla recognition model -- English pages only,
    or use PP-Structure separately for layout/table detection."""

    name = "paddle"

    def __init__(self, lang: str = "en"):
        self.lang = lang
        self._ocr = None

    def available(self):
        try:
            import paddleocr  # type: ignore  # noqa: F401
        except ImportError as e:
            return False, f"pip install paddleocr paddlepaddle ({e})"
        if self.lang in {"bn", "ben", "bengali"}:
            return False, ("PaddleOCR has no official Bangla recognition model. "
                           "Run it with --paddle-lang en on English pages instead.")
        return True, f"lang={self.lang} (NOT valid for Bangla text)"

    def _ensure(self):
        if self._ocr is None:
            from paddleocr import PaddleOCR  # type: ignore

            self._ocr = PaddleOCR(lang=self.lang, show_log=False)

    def run(self, image_path: Path) -> str:
        self._ensure()
        # Same non-ASCII path problem as EasyOCR -- pass pixels instead.
        import numpy as np
        from PIL import Image

        with Image.open(image_path) as im:
            arr = np.array(im.convert("RGB"))
        result = self._ocr.ocr(arr)
        lines = []
        for page in result or []:
            for item in page or []:
                try:
                    lines.append(item[1][0])
                except Exception:
                    continue
        return "\n".join(lines)


class OllamaVLMEngine(Engine):
    """Any vision-language model served locally by Ollama."""

    name = "vlm"

    PROMPT = (
        "Transcribe ALL text in this document image exactly as it appears. "
        "Preserve line breaks. If there is a table, reproduce it as a markdown "
        "table with the same rows and columns. Do not translate. Do not "
        "summarise. Do not add any commentary -- output only the transcription."
    )

    def __init__(self, model: str = "qwen2.5vl:7b",
                 host: str = "http://localhost:11434", timeout: int = 300):
        self.model, self.host, self.timeout = model, host.rstrip("/"), timeout

    def available(self):
        import urllib.error
        import urllib.request

        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=5) as r:
                tags = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            return False, f"Ollama not reachable at {self.host} ({e})"
        names = [m.get("name", "") for m in tags.get("models", [])]
        if not any(n == self.model or n.startswith(self.model.split(":")[0])
                   for n in names):
            return False, (f"model '{self.model}' not pulled. "
                           f"Run: ollama pull {self.model}")
        return True, f"model={self.model}"

    def run(self, image_path: Path) -> str:
        import urllib.request

        b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        payload = json.dumps({
            "model": self.model,
            "prompt": self.PROMPT,
            "images": [b64],
            "stream": False,
            "options": {"temperature": 0},
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.host}/api/generate", data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8")).get("response", "")


# ==========================================================================
# Rasterisation
# ==========================================================================

def _import_pymupdf():
    """PyMuPDF renamed its module from `fitz` to `pymupdf`. Support both."""
    try:
        import pymupdf  # type: ignore

        return pymupdf
    except ImportError:
        pass
    try:
        import fitz  # type: ignore

        return fitz
    except ImportError:
        return None


def pdf_page_count(src: Path) -> int | None:
    if src.suffix.lower() not in PDF_EXTS:
        return None
    fitz = _import_pymupdf()
    if fitz is None:
        return None
    try:
        with fitz.open(src) as doc:
            return len(doc)
    except Exception:
        return None


def rasterise(src: Path, out_dir: Path, dpi: int, max_pages: int) -> list[tuple[str, Path]]:
    """Turn one source file into (page_id, png_path) pairs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = src.stem.replace(" ", "_")

    if src.suffix.lower() in IMAGE_EXTS:
        dst = out_dir / f"{stem}_p1.png"
        if not dst.exists():
            try:
                from PIL import Image  # type: ignore

                with Image.open(src) as im:
                    im.convert("RGB").save(dst)
            except ImportError:
                dst = src  # use the original file directly
        return [(f"{stem}_p1", dst)]

    if src.suffix.lower() not in PDF_EXTS:
        return []

    fitz = _import_pymupdf()
    if fitz is None:
        print("  ! PyMuPDF missing -- cannot rasterise PDFs. pip install pymupdf",
              file=sys.stderr)
        return []

    pages: list[tuple[str, Path]] = []
    zoom = dpi / 72.0
    with fitz.open(src) as doc:
        for i, page in enumerate(doc, 1):
            if i > max_pages:
                break
            page_id = f"{stem}_p{i}"
            dst = out_dir / f"{page_id}.png"
            if not dst.exists():
                page.get_pixmap(matrix=fitz.Matrix(zoom, zoom)).save(dst)
            pages.append((page_id, dst))
    return pages


def text_layer_chars_per_page(src: Path) -> float | None:
    """Average extractable characters per page, or None if not a readable PDF.

    A digital PDF typically yields hundreds to thousands. A scan yields ~0.
    Anything in between usually means a mixed document (digital text plus
    scanned inserts) -- inspect those by hand.
    """
    if src.suffix.lower() not in PDF_EXTS:
        return None
    fitz = _import_pymupdf()
    if fitz is None:
        return None
    try:
        with fitz.open(src) as doc:
            sample = min(len(doc), 5)
            if sample == 0:
                return None
            chars = sum(len(doc[i].get_text().strip()) for i in range(sample))
        return chars / sample
    except Exception:
        return None


# ==========================================================================
# Runner
# ==========================================================================

@dataclass
class PageResult:
    page_id: str
    engine: str
    seconds: float
    chars_out: int
    cer: float | None = None
    wer: float | None = None
    doc_class: str = ""
    error: str = ""


@dataclass
class EngineStats:
    name: str
    pages: int = 0
    failures: int = 0
    seconds: list[float] = field(default_factory=list)
    cers: list[float] = field(default_factory=list)
    wers: list[float] = field(default_factory=list)
    by_class: dict = field(default_factory=dict)   # class -> {"cers":[], "wers":[], "secs":[]}

    def bucket(self, cls: str) -> dict:
        return self.by_class.setdefault(
            cls or "UNKNOWN", {"cers": [], "wers": [], "secs": []})


def build_engines(args) -> list[Engine]:
    wanted = [e.strip() for e in args.engines.split(",") if e.strip()]
    catalog = {
        "tesseract": lambda: TesseractEngine(args.tess_langs, args.tess_psm),
        "easyocr": lambda: EasyOCREngine(tuple(args.easy_langs.split("+")), not args.no_gpu),
        "surya": lambda: SuryaEngine(tuple(args.surya_langs.split("+"))),
        "paddle": lambda: PaddleEngine(args.paddle_lang),
        "vlm": lambda: OllamaVLMEngine(args.vlm_model, args.ollama_host, args.vlm_timeout),
    }
    unknown = [w for w in wanted if w not in catalog]
    if unknown:
        sys.exit(f"Unknown engine(s): {unknown}. Choose from {sorted(catalog)}")
    return [catalog[w]() for w in wanted]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="OCR bake-off for Bangla/English DUET documents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Defaults are relative to THIS SCRIPT, not the shell's working directory,
    # so the tool behaves identically from the repo root or from inside
    # m0_reality_check/.
    ap.add_argument("--samples", default=str(HERE / "samples"),
                    help="folder of PDFs/images (default: m0_reality_check/samples)")
    ap.add_argument("--truth", default=str(HERE / "ground_truth"),
                    help="folder of hand-typed .txt (default: m0_reality_check/ground_truth)")
    ap.add_argument("--out", default=str(HERE / "results"),
                    help="output folder (default: m0_reality_check/results)")
    ap.add_argument("--engines", default="tesseract",
                    help="comma-separated: tesseract,easyocr,surya,paddle,vlm")
    ap.add_argument("--dpi", type=int, default=300, help="rasterisation DPI (300 is a good default)")
    ap.add_argument("--max-pages", type=int, default=5,
                    help="max pages sampled per document (default 5). The "
                         "bake-off samples representative pages; it is not an "
                         "ingestion run")
    ap.add_argument("--skip-digital", action="store_true",
                    help="do not OCR documents that already have a text layer. "
                         "Production will not OCR them either, so OCR-ing them "
                         "here only wastes time and dilutes the result")
    ap.add_argument("--make-truth-stubs", action="store_true",
                    help="create empty ground-truth files and exit")
    ap.add_argument("--seed-truth-from", metavar="ENGINE", default=None,
                    help="write this engine's output into ground_truth_draft/ "
                         "as a starting point for hand-correction")
    ap.add_argument("--no-case-fold", action="store_true",
                    help="treat upper/lower case differences as errors")
    # engine options
    ap.add_argument("--tess-langs", default="ben+eng")
    ap.add_argument("--tess-psm", type=int, default=3)
    ap.add_argument("--easy-langs", default="bn+en")
    ap.add_argument("--surya-langs", default="bn+en")
    ap.add_argument("--paddle-lang", default="en")
    ap.add_argument("--vlm-model", default="qwen2.5vl:7b")
    ap.add_argument("--vlm-timeout", type=int, default=300)
    ap.add_argument("--ollama-host", default="http://localhost:11434")
    ap.add_argument("--no-gpu", action="store_true")
    args = ap.parse_args()

    samples_dir = Path(args.samples)
    truth_dir = Path(args.truth)
    out_dir = Path(args.out)
    pages_dir = out_dir / "pages"

    if not samples_dir.is_dir():
        sys.exit(f"No samples folder at '{samples_dir}'. Create it and add real DUET documents.")

    sources = sorted(
        p for p in samples_dir.iterdir()
        if p.suffix.lower() in (IMAGE_EXTS | PDF_EXTS)
    )
    if not sources:
        sys.exit(f"'{samples_dir}' is empty. Add 8-15 representative documents first.")

    # ---- rasterise -------------------------------------------------------
    print(f"Rasterising {len(sources)} source file(s) at {args.dpi} DPI ...")
    all_pages: list[tuple[str, Path]] = []
    corpus = {"DIGITAL": 0, "MIXED": 0, "SCANNED": 0, "UNKNOWN": 0}
    page_class: dict[str, str] = {}
    for src in sources:
        cpp = text_layer_chars_per_page(src)
        if cpp is None:
            cls, flag = "UNKNOWN", ""
        elif cpp < 20:
            cls = "SCANNED"
            flag = f"  [SCANNED ({cpp:.0f} chars/page) -- OCR required]"
        elif cpp < 200:
            cls = "MIXED"
            flag = f"  [MIXED ({cpp:.0f} chars/page) -- inspect by hand]"
        else:
            cls = "DIGITAL"
            flag = f"  [DIGITAL ({cpp:.0f} chars/page) -- extract text, skip OCR]"
        corpus[cls] += 1

        total = pdf_page_count(src)
        if args.skip_digital and cls == "DIGITAL":
            print(f"  {src.name}: SKIPPED (--skip-digital){flag}")
            continue

        pages = rasterise(src, pages_dir, args.dpi, args.max_pages)
        shown = f"{len(pages)} page(s)"
        if total and total > len(pages):
            shown = (f"{len(pages)} of {total} page(s)"
                     f"  <-- truncated by --max-pages {args.max_pages}")
        print(f"  {src.name}: {shown}{flag}")
        for pid, _ in pages:
            page_class[pid] = cls
        all_pages.extend(pages)

    n_docs = sum(corpus.values())
    print()
    print("Corpus split (record this in docs/M0_report.md):")
    for cls in ("DIGITAL", "MIXED", "SCANNED", "UNKNOWN"):
        if corpus[cls]:
            pct = 100 * corpus[cls] / n_docs
            print(f"  {cls:<8} {corpus[cls]:3d} document(s)  ({pct:.0f}%)")
    print()

    if not all_pages:
        sys.exit("No pages produced. Is PyMuPDF installed? (pip install pymupdf)")
    print(f"Pages queued for OCR: {len(all_pages)}\n")

    # ---- ground-truth stubs ---------------------------------------------
    if args.make_truth_stubs:
        truth_dir.mkdir(parents=True, exist_ok=True)
        created = 0
        for page_id, _ in all_pages:
            f = truth_dir / f"{page_id}.txt"
            if not f.exists():
                f.write_text("", encoding="utf-8")
                created += 1
        print(f"Created {created} empty ground-truth file(s) in '{truth_dir}'.")
        print("\nNEXT: open the page images in", pages_dir)
        print("      and type the correct text into the matching .txt file.")
        print("      UTF-8 only. 10-20 pages is enough. Leave the rest empty --")
        print("      empty files are skipped when scoring.\n")
        return 0

    # ---- engines ---------------------------------------------------------
    engines = build_engines(args)
    active: list[Engine] = []
    print("Engine availability:")
    for eng in engines:
        ok, msg = eng.available()
        print(f"  {'OK  ' if ok else 'SKIP'} {eng.name:<10} {msg}")
        if ok:
            active.append(eng)
    print()
    if not active:
        sys.exit("No engines available. Install at least one, then re-run.")

    # ---- load ground truth ----------------------------------------------
    truth: dict[str, str] = {}
    suspect: list[tuple[str, str]] = []
    if truth_dir.is_dir():
        for f in truth_dir.glob("*.txt"):
            content = f.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                truth[f.stem] = content
                reason = looks_restructured(content)
                if reason:
                    suspect.append((f.name, reason))
    print(f"Ground truth available for {len(truth)}/{len(all_pages)} page(s).")
    if suspect:
        print()
        print("!! GROUND TRUTH LOOKS RESTRUCTURED, NOT TRANSCRIBED:")
        for name, reason in suspect:
            print(f"!!   {name}  --  {reason}")
        print("!!")
        print("!! Ground truth must be what is literally printed on the page, in")
        print("!! reading order. A cleaned-up, reformatted or AI-summarised version")
        print("!! is a DIFFERENT TEXT, so the CER you get back measures the gap")
        print("!! between two documents, not the accuracy of an OCR engine.")
        print("!! Retype these by hand, or delete them and exclude those pages.")
        print("!! For a DIGITAL page you never need to type anything: run")
        print("!! check_digital_extraction.py --write-truth to take the exact text")
        print("!! layer as truth.")
    if not truth:
        print("  ! No ground truth -> timings only, no accuracy scores.")
        print("  ! Run with --make-truth-stubs first.\n")
    else:
        print()

    # ---- run -------------------------------------------------------------
    results: list[PageResult] = []
    stats: dict[str, EngineStats] = {e.name: EngineStats(e.name) for e in active}
    fold = not args.no_case_fold

    for eng in active:
        raw_dir = out_dir / "raw" / eng.name
        raw_dir.mkdir(parents=True, exist_ok=True)
        print(f"--- {eng.name} ---")
        for page_id, img in all_pages:
            t0 = time.perf_counter()
            try:
                text = eng.run(img)
                elapsed = time.perf_counter() - t0
                (raw_dir / f"{page_id}.txt").write_text(text, encoding="utf-8")
                cls = page_class.get(page_id, "UNKNOWN")
                r = PageResult(page_id, eng.name, round(elapsed, 2), len(text),
                               doc_class=cls)
                if page_id in truth:
                    ref = truth[page_id]
                    r.cer = round(cer(ref, text, fold), 4)
                    r.wer = round(wer(ref, text, fold), 4)
                    stats[eng.name].cers.append(r.cer)
                    stats[eng.name].wers.append(r.wer)
                    b = stats[eng.name].bucket(cls)
                    b["cers"].append(r.cer)
                    b["wers"].append(r.wer)
                stats[eng.name].bucket(cls)["secs"].append(elapsed)
                stats[eng.name].seconds.append(elapsed)
                stats[eng.name].pages += 1
                score = f"CER {r.cer:.3f}  WER {r.wer:.3f}" if r.cer is not None else "no truth"
                print(f"  {page_id:<38} {elapsed:6.2f}s  {len(text):6d} chars  {score}")
            except Exception as e:
                elapsed = time.perf_counter() - t0
                stats[eng.name].failures += 1
                results.append(PageResult(page_id, eng.name, round(elapsed, 2), 0,
                                          doc_class=page_class.get(page_id, "UNKNOWN"),
                                          error=str(e)[:200]))
                print(f"  {page_id:<38} FAILED: {str(e)[:80]}")
                continue
            results.append(r)
        print()

    # ---- optional: seed draft ground truth -------------------------------
    if args.seed_truth_from:
        src_dir = out_dir / "raw" / args.seed_truth_from
        draft_dir = HERE / "ground_truth_draft"
        if src_dir.is_dir():
            draft_dir.mkdir(exist_ok=True)
            n = 0
            for f in src_dir.glob("*.txt"):
                (draft_dir / f.name).write_text(
                    f.read_text(encoding="utf-8"), encoding="utf-8")
                n += 1
            print("=" * 74)
            print(f"Wrote {n} DRAFT ground-truth file(s) to '{draft_dir}'.")
            print("!! These are machine output, NOT ground truth. Correct every")
            print("!! one by hand against the page image, THEN move it into")
            print(f"!! '{truth_dir}'. Scoring an engine against its own uncorrected")
            print("!! output produces a fake CER of 0.000 and invalidates the study.")
            print("=" * 74 + "\n")

    # ---- write CSVs -------------------------------------------------------
    out_dir.mkdir(parents=True, exist_ok=True)
    per_page = out_dir / "per_page.csv"
    with per_page.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["page_id", "engine", "doc_class", "seconds", "chars_out",
                    "cer", "wer", "error"])
        for r in results:
            w.writerow([r.page_id, r.engine, r.doc_class, r.seconds, r.chars_out,
                        "" if r.cer is None else r.cer,
                        "" if r.wer is None else r.wer, r.error])

    summary = out_dir / "summary.csv"
    rows = []
    for name, s in stats.items():
        rows.append({
            "engine": name,
            "pages_run": s.pages,
            "failures": s.failures,
            "pages_scored": len(s.cers),
            "mean_cer": round(statistics.fmean(s.cers), 4) if s.cers else "",
            "median_cer": round(statistics.median(s.cers), 4) if s.cers else "",
            "worst_cer": round(max(s.cers), 4) if s.cers else "",
            "mean_wer": round(statistics.fmean(s.wers), 4) if s.wers else "",
            "mean_sec_per_page": round(statistics.fmean(s.seconds), 2) if s.seconds else "",
        })
    with summary.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # ---- print markdown table --------------------------------------------
    any_scored = any(r["pages_scored"] for r in rows)

    print("=" * 74)
    if any_scored:
        print("  SUMMARY  (paste this into your M0 report)")
    else:
        print("  SUMMARY  --  SPEED ONLY, NO ACCURACY MEASURED")
    print("=" * 74)

    if not any_scored:
        print("!! Every ground-truth file is empty, so CER and WER could not be")
        print("!! computed. The only real number below is sec/page, which is")
        print("!! SPEED, not accuracy. Do not paste this into your report as an")
        print("!! accuracy result.")
        print("!!")
        print("!! Adding more documents will NOT produce CER. Only hand-typed")
        print("!! ground truth will. See step 3b in README_M0.md.")
        print()

    hdr = ["engine", "pages", "fail", "scored", "mean CER", "median CER",
           "worst CER", "mean WER", "sec/page"]
    print("| " + " | ".join(hdr) + " |")
    print("|" + "|".join("---" for _ in hdr) + "|")
    for r in sorted(rows, key=lambda x: (x["mean_cer"] == "", x["mean_cer"])):
        cells = []
        for k in ["engine", "pages_run", "failures", "pages_scored", "mean_cer",
                  "median_cer", "worst_cer", "mean_wer", "mean_sec_per_page"]:
            v = r[k]
            cells.append("n/a" if v == "" else str(v))
        print("| " + " | ".join(cells) + " |")
    print()
    print("  CER / WER : accuracy. 0.00 is perfect, 1.00 is total garbage.")
    print("  sec/page  : speed. Not a quality measure.")
    print("  n/a       : not measured -- no ground truth for those pages.")
    print()

    # ---- breakdown by document class -------------------------------------
    if any_scored:
        print("-" * 74)
        print("  BY DOCUMENT CLASS  --  this is the number that matters")
        print("-" * 74)
        print("| engine | class | scored | mean CER | median CER | worst CER | sec/page |")
        print("|---|---|---|---|---|---|---|")
        class_rows = []
        for name, st in stats.items():
            for cls in ("SCANNED", "MIXED", "DIGITAL", "UNKNOWN"):
                b = st.by_class.get(cls)
                if not b or not b["cers"]:
                    continue
                class_rows.append((name, cls, len(b["cers"]),
                                   statistics.fmean(b["cers"]),
                                   statistics.median(b["cers"]),
                                   max(b["cers"]),
                                   statistics.fmean(b["secs"]) if b["secs"] else 0))
                print(f"| {name} | {cls} | {len(b['cers'])} | "
                      f"{statistics.fmean(b['cers']):.3f} | "
                      f"{statistics.median(b['cers']):.3f} | "
                      f"{max(b['cers']):.3f} | "
                      f"{statistics.fmean(b['secs']):.2f} |")
        print()

        digital_scored = sum(r[2] for r in class_rows if r[1] == "DIGITAL")
        if digital_scored:
            print(f"!! {digital_scored} scored page(s) came from DIGITAL documents.")
            print("!! Production extracts their text natively and never runs OCR on")
            print("!! them, so their CER does not describe production behaviour and")
            print("!! should not be in your headline number. Re-run with")
            print("!! --skip-digital for the figure to report.")
            print()

        worst = sorted((r for r in results if r.cer is not None),
                       key=lambda r: -r.cer)[:3]
        if worst:
            print("Worst pages -- open these and find out WHY before choosing an engine:")
            for r in worst:
                print(f"  CER {r.cer:.3f}  {r.doc_class:<8} {r.page_id}")
            print("  (In Bangla notices the usual answer is: it is a table, or it is")
            print("   handwriting/stamps. Those are different problems with different")
            print("   fixes -- do not average them together.)")
            print()
    print(f"Per-page detail : {per_page}")
    print(f"Summary         : {summary}")
    print(f"Raw OCR output  : {out_dir / 'raw'}")
    print()
    print("Now DO THIS: open results/raw/<engine>/ and read a page with a table.")
    print("CER measures characters, not structure. An engine can win on CER and")
    print("still destroy every table. Judge tables with your own eyes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())