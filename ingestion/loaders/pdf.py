"""Native PDF text extraction, span aware.

Text is read span by span rather than page by page because a DUET notice
mixes scripts inside a single line:

    সকল Check Box টিক চিহ্ন দিয়া

The Bangla there is Bijoy bytes drawn in SutonnyMJ; `Check Box` is real
English drawn in TimesNewRomanPSMT. Line-level conversion had to choose one
answer for the whole line, so it either destroyed the English (Check Box ->
ঈযবপশ ইড়ী) or left the Bangla unsearchable. The PDF already records which
font drew which span, so the choice does not have to be guessed.

No OCR here and none called from here. M1 handles DIGITAL documents only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ingestion.chunker import Page
from ingestion.classify import Classification, _import_pymupdf, classify_pdf
from ingestion.legacy_convert import BijoyConverter, convert_fragment
from ingestion.legacy_font import font_verdicts


class UnsupportedDocument(RuntimeError):
    pass


@dataclass
class LoadReport:
    legacy_fonts: set[str] = field(default_factory=set)
    spans_total: int = 0
    spans_converted: int = 0
    spans_failed: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def converted_any(self) -> bool:
        return self.spans_converted > 0


def _read_spans(doc, max_pages: int | None = None) -> list[list[list[tuple[str, str]]]]:
    """pages -> lines -> [(font, text)], preserving reading order."""
    pages: list[list[list[tuple[str, str]]]] = []
    for i, page in enumerate(doc, start=1):
        if max_pages and i > max_pages:
            break
        lines: list[list[tuple[str, str]]] = []
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                spans = [(s.get("font", "?"), s.get("text", ""))
                         for s in line.get("spans", []) if s.get("text")]
                if spans:
                    lines.append(spans)
            lines.append([])          # blank line between blocks: the chunker
        pages.append(lines)           # splits on these
    return pages


def _merge_runs(spans: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Join neighbouring spans that share a font.

    A word can be split across spans by a formatting change. Converting the
    halves separately breaks conjuncts that straddle the join, so runs are
    reassembled before the glyph map sees them.
    """
    merged: list[tuple[str, str]] = []
    for font, text in spans:
        if merged and merged[-1][0] == font:
            merged[-1] = (font, merged[-1][1] + text)
        else:
            merged.append((font, text))
    return merged


def load_pdf(
    path: Path,
    *,
    convert_legacy: bool = True,
    max_pages: int | None = None,
) -> tuple[list[Page], Classification, LoadReport]:
    """Extract text page by page, converting legacy-font spans to Unicode.

    Pass convert_legacy=False to see the raw text layer -- the bake-off
    needs the before and after side by side.
    """
    fitz = _import_pymupdf()
    if fitz is None:
        raise UnsupportedDocument("PyMuPDF is not installed (pip install pymupdf)")

    cls = classify_pdf(path)
    report = LoadReport()

    with fitz.open(path) as doc:
        raw_pages = _read_spans(doc, max_pages)

    flat = [(f, t) for page in raw_pages for line in page for f, t in line]
    report.spans_total = len(flat)

    converter = None
    if convert_legacy:
        report.legacy_fonts = font_verdicts(flat)
        if report.legacy_fonts:
            conv = BijoyConverter()
            ok, detail = conv.available()
            if not ok:
                raise UnsupportedDocument(
                    f"{path.name} uses legacy Bangla fonts "
                    f"{sorted(report.legacy_fonts)} but the converter is "
                    f"unavailable: {detail}. Ingesting it now would store "
                    f"text no Bangla query can ever match."
                )
            converter = conv

    pages: list[Page] = []
    for page_no, lines in enumerate(raw_pages, start=1):
        out_lines: list[str] = []
        for spans in lines:
            if not spans:
                out_lines.append("")
                continue
            parts: list[str] = []
            for font, text in _merge_runs(spans):
                if converter and font in report.legacy_fonts:
                    converted, ok = convert_fragment(text, converter)
                    if ok:
                        report.spans_converted += 1
                    else:
                        report.spans_failed += 1
                        if len(report.failures) < 20:
                            report.failures.append(f"p{page_no} [{font}] {text[:50]}")
                    parts.append(converted)
                else:
                    parts.append(text)
            out_lines.append("".join(parts))
        pages.append(Page(page_no=page_no, text="\n".join(out_lines)))

    return pages, cls, report


def page_count(path: Path) -> int:
    fitz = _import_pymupdf()
    if fitz is None:
        raise UnsupportedDocument("PyMuPDF is not installed")
    with fitz.open(path) as doc:
        return len(doc)