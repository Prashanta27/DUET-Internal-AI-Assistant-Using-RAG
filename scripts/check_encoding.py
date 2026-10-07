#!/usr/bin/env python3
"""Measure how much of the corpus is legacy ASCII-mapped Bangla.

    python scripts/check_encoding.py m0_reality_check/samples
    python scripts/check_encoding.py m0_reality_check/samples --pages

A document can be DIGITAL and still be unusable for Bangla retrieval, if its
Bangla is stored as ASCII bytes drawn by a Bijoy-family font. That failure is
invisible downstream, so it gets a number here before anything is embedded.

Read the samples with your own eyes. The detector says "this is not Unicode
Bangla"; only you can say which font map it needs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion.classify import DIGITAL, MIXED, classify_pdf  # noqa: E402
from ingestion.legacy_font import analyse_pages  # noqa: E402
from ingestion.loaders.pdf import load_pdf  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Detect legacy Bangla font encoding")
    ap.add_argument("folder", nargs="?", default="m0_reality_check/samples")
    ap.add_argument("--pages", action="store_true",
                    help="report every page, not just the document")
    args = ap.parse_args()

    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"No such folder: {folder}", file=sys.stderr)
        return 1

    counts: dict[str, int] = {}
    legacy_docs: list[Path] = []
    checked = 0

    for pdf in sorted(folder.rglob("*.pdf")):
        cls = classify_pdf(pdf)
        if cls.doc_class not in (DIGITAL, MIXED):
            continue  # no text layer to inspect; OCR decides at M3
        checked += 1

        pages, _ = load_pdf(pdf)
        doc, per_page = analyse_pages([p.text for p in pages])
        counts[doc.verdict] = counts.get(doc.verdict, 0) + 1
        if doc.is_legacy:
            legacy_docs.append(pdf)

        print(f"{doc.verdict:<15} legacy_lines={doc.legacy_lines}/{doc.judged_lines}"
              f"  bn_chars={doc.bengali_chars}  {pdf.name}")
        print(f"                | {doc.sample}")

        if args.pages:
            for p, rep in zip(pages, per_page, strict=True):
                if rep.judged_lines:
                    print(f"                p{p.page_no:<3} {rep.verdict:<15} "
                          f"{rep.legacy_lines}/{rep.judged_lines}")
        print()

    if not checked:
        print("No DIGITAL or MIXED documents to inspect.")
        return 0

    print("=" * 70)
    print(f"Documents with a text layer : {checked}")
    for label, key in [
        ("LEGACY (all Bangla ASCII-mapped)", "LEGACY"),
        ("PARTIAL_LEGACY (some lines)     ", "PARTIAL_LEGACY"),
        ("MIXED (Unicode + legacy)        ", "MIXED"),
        ("UNICODE_BN                      ", "UNICODE_BN"),
        ("LATIN_ONLY (English)            ", "LATIN_ONLY"),
    ]:
        print(f"  {label}: {counts.get(key, 0)}")
    print("=" * 70)

    if legacy_docs:
        print()
        print("These are DIGITAL -- their text layer is exact -- but their Bangla")
        print("is not Bengali codepoints, so no Bangla query will ever retrieve")
        print("them. They need transliteration back to Unicode, NOT OCR:")
        print("rasterising and OCR-ing exact text would trade a solvable mapping")
        print("problem for a 0.307 CER problem.")
        for p in legacy_docs:
            print(f"  {p}")
        print()
        print("Record this count in docs/M1_report.md. If it is most of the")
        print("Bangla corpus, legacy-font conversion belongs in M1, not later.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())