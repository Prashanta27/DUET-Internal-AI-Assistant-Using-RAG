#!/usr/bin/env python3
"""Show which documents M1 can actually ingest.

M1 handles DIGITAL documents only -- ones with a real text layer, where
native extraction is exact. Everything else waits for OCR at M3.

    python scripts/classify_folder.py m0_reality_check/samples
    python scripts/classify_folder.py m0_reality_check/samples --show-text

This is the same chars-per-page measurement the M0 bake-off printed, run
through the production classifier. If the two ever disagree, the production
one is what matters.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Run from anywhere: python scripts/classify_folder.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion.classify import DIGITAL, MIXED, SCANNED, classify_pdf


def main() -> int:
    ap = argparse.ArgumentParser(description="Classify PDFs as DIGITAL / MIXED / SCANNED")
    ap.add_argument("folder", nargs="?", default="m0_reality_check/samples")
    ap.add_argument("--show-text", action="store_true",
                    help="print the first 200 extracted characters of page 1")
    args = ap.parse_args()

    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"No such folder: {folder}", file=sys.stderr)
        return 1

    pdfs = sorted(folder.rglob("*.pdf"))
    if not pdfs:
        print(f"No PDFs in {folder}", file=sys.stderr)
        return 1

    buckets: dict[str, list[Path]] = {}
    print(f"{'class':<9} {'chars/pg':>9} {'pages':>6}  file")
    print("-" * 74)
    for pdf in pdfs:
        cls = classify_pdf(pdf)
        buckets.setdefault(cls.doc_class, []).append(pdf)
        cpp = "-" if cls.chars_per_page is None else f"{cls.chars_per_page:.0f}"
        pages = "-" if cls.page_count is None else str(cls.page_count)
        print(f"{cls.doc_class:<9} {cpp:>9} {pages:>6}  {pdf.name}")

        if args.show_text and cls.doc_class != SCANNED:
            from ingestion.loaders.pdf import load_pdf

            got, _ = load_pdf(pdf)
            first = next((p.text.strip() for p in got if p.text.strip()), "")
            print(f"          | {first[:200]!r}")

    print()
    digital = buckets.get(DIGITAL, [])
    print(f"DIGITAL  {len(digital):3d}   <- M1 can ingest these today")
    print(f"MIXED    {len(buckets.get(MIXED, [])):3d}   <- partial text; M3")
    print(f"SCANNED  {len(buckets.get(SCANNED, [])):3d}   <- needs OCR; M3")

    if not digital:
        print()
        print("No DIGITAL documents here. M1's vertical slice needs at least a")
        print("few, because its whole point is proving retrieval and citation")
        print("work on text that is known to be exact. Sources worth trying:")
        print("  - notices published as Word exports rather than scans")
        print("  - the academic calendar / syllabus PDFs from the DUET website")
        print("  - any circular that was emailed as a PDF rather than photocopied")
        print()
        print("If the real corpus is genuinely almost all scans, that is a")
        print("finding, not a setback -- write it in docs/M1_report.md and raise")
        print("it with your adviser, because it moves OCR from an M3 concern to")
        print("the project's critical path.")
    else:
        print()
        print("Start the M1 slice with these:")
        for p in digital[:10]:
            print(f"  {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())