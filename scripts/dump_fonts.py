#!/usr/bin/env python3
"""Show which fonts a PDF uses, and what text each font draws.

Why this exists: line-level detection of legacy Bangla is too coarse for
real DUET documents. A sentence like

    সকল Check Box টিক চিহ্ন দিয়া

is one line containing both Bijoy bytes and genuine English. Converting the
whole line destroys the English; skipping the whole line leaves the Bangla
unsearchable. Neither is acceptable.

A PDF records the font for every span of text it draws. Bijoy Bangla is
drawn in SutonnyMJ and relatives; the English words beside it are drawn in
Times or Arial. If that holds in your documents, gating conversion by font
is a fact rather than a guess -- and it fixes both failure directions at
once.

    python scripts/dump_fonts.py "m0_reality_check/samples/DUET admission information session 2024-2025.pdf"
    python scripts/dump_fonts.py "<pdf>" --page 3 --spans
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion.classify import _import_pymupdf  # noqa: E402
from ingestion.legacy_font import _marker_ratio  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="List fonts and the text they draw")
    ap.add_argument("pdf")
    ap.add_argument("--page", type=int, default=None, help="limit to one page")
    ap.add_argument("--spans", action="store_true",
                    help="print every span, not just a summary")
    ap.add_argument("--max-pages", type=int, default=5)
    args = ap.parse_args()

    fitz = _import_pymupdf()
    if fitz is None:
        print("PyMuPDF not installed", file=sys.stderr)
        return 1

    pdf = Path(args.pdf)
    if not pdf.is_file():
        print(f"No such file: {pdf}", file=sys.stderr)
        return 1

    counts: Counter[str] = Counter()
    markers: defaultdict[str, list[float]] = defaultdict(list)
    samples: dict[str, str] = {}

    with fitz.open(pdf) as doc:
        pages = ([args.page] if args.page
                 else range(1, min(len(doc), args.max_pages) + 1))
        for n in pages:
            page = doc[n - 1]
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "")
                        if not text.strip():
                            continue
                        font = span.get("font", "?")
                        ratio, bengali, total = _marker_ratio(text)
                        counts[font] += len(text)
                        markers[font].append(ratio)
                        if font not in samples and len(text.strip()) > 3:
                            samples[font] = text.strip()[:60]
                        if args.spans:
                            print(f"  p{n} [{font:<28}] "
                                  f"markers={ratio:.3f} bn={bengali}  {text[:70]!r}")

    if not counts:
        print("No text spans found -- is this a scanned PDF?")
        return 0

    print()
    print(f"{'font':<32} {'chars':>8} {'mean markers':>13}  sample")
    print("-" * 100)
    for font, n in counts.most_common():
        mean = sum(markers[font]) / len(markers[font])
        print(f"{font:<32} {n:>8} {mean:>13.3f}  {samples.get(font, '')!r}")

    print()
    legacy_fonts = [f for f in counts
                    if sum(markers[f]) / len(markers[f]) >= 0.03]
    clean_fonts = [f for f in counts if f not in legacy_fonts]
    print("Fonts whose text looks like Bijoy (convert these):")
    for f in legacy_fonts:
        print(f"  {f}")
    print("Fonts whose text does not (leave these alone):")
    for f in clean_fonts:
        print(f"  {f}")
    print()
    if legacy_fonts and clean_fonts:
        print("Both kinds are present, which is the good outcome: conversion can")
        print("be gated by font instead of by line, so English words inside a")
        print("Bangla sentence survive and Bangla words inside an English-heavy")
        print("line still get converted.")
    elif legacy_fonts:
        print("Only legacy fonts found on these pages. Check more pages before")
        print("concluding -- the English spans may live elsewhere in the file.")
    else:
        print("No legacy-looking font found. Either these pages are clean, or")
        print("the marker heuristic missed something -- read the samples above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())