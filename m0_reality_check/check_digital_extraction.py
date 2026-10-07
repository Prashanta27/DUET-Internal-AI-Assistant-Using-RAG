#!/usr/bin/env python3
"""
M0 / Step 3b -- native extraction check for DIGITAL documents.

WHY THIS EXISTS
---------------
A PDF classified DIGITAL already contains real text, authored by whoever made
the file. Production never runs OCR on it, so its OCR error rate is not a
number worth having. The question that actually matters is different:

    does native extraction preserve the TABLE?

That is the M3 question, and for a class routine or a course list it decides
the whole table architecture. This script answers it before you write any
ingestion code.

It also gives you free, exact ground truth: for a DIGITAL page, the embedded
text layer IS the truth. You do not type it and you do not ask a model for it.

    python m0_reality_check/check_digital_extraction.py
    python m0_reality_check/check_digital_extraction.py --write-truth

WHAT IT PRODUCES
    results_digital/text/<page>.txt      native text, reading order
    results_digital/tables/<page>.md     each detected table as markdown
    results_digital/tables/<page>.json   each detected table as rows/columns

Then YOU open a table and judge it. A table that comes back with the right
rows and columns means that document needs no OCR and no vision model --
it needs a parser, which is a far easier problem.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIGITAL_THRESHOLD = 200      # chars/page, same threshold as ocr_bakeoff.py


def import_pymupdf():
    try:
        import pymupdf
        return pymupdf
    except ImportError:
        pass
    try:
        import fitz
        return fitz
    except ImportError:
        return None


def chars_per_page(doc, sample: int = 5) -> float:
    n = min(len(doc), sample)
    if n == 0:
        return 0.0
    return sum(len(doc[i].get_text().strip()) for i in range(n)) / n


def to_markdown(table: list[list]) -> str:
    """Render an extracted table as markdown, padding ragged rows."""
    rows = [[("" if c is None else str(c).replace("\n", " ").strip()) for c in r]
            for r in table if r]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |",
           "|" + "|".join("---" for _ in range(width)) + "|"]
    for r in rows[1:]:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Check native text and table extraction on DIGITAL PDFs")
    ap.add_argument("--samples", default=str(HERE / "samples"))
    ap.add_argument("--out", default=str(HERE / "results_digital"))
    ap.add_argument("--max-pages", type=int, default=5)
    ap.add_argument("--all", action="store_true",
                    help="also process SCANNED documents (normally pointless)")
    ap.add_argument("--write-truth", action="store_true",
                    help="also copy the native text into ground_truth/ as exact "
                         "ground truth for these pages")
    ap.add_argument("--truth", default=str(HERE / "ground_truth"))
    args = ap.parse_args()

    fitz = import_pymupdf()
    if fitz is None:
        sys.exit("PyMuPDF missing. pip install pymupdf")

    try:
        import pdfplumber
    except ImportError:
        pdfplumber = None
        print("! pdfplumber not installed -- text will be extracted but tables")
        print("! will not be detected. pip install pdfplumber\n")

    samples = Path(args.samples)
    out = Path(args.out)
    (out / "text").mkdir(parents=True, exist_ok=True)
    (out / "tables").mkdir(parents=True, exist_ok=True)

    pdfs = sorted(p for p in samples.iterdir() if p.suffix.lower() == ".pdf")
    if not pdfs:
        sys.exit(f"No PDFs in {samples}")

    grand_tables = 0
    processed = 0
    truth_written = 0

    for src in pdfs:
        stem = src.stem.replace(" ", "_")
        with fitz.open(src) as doc:
            cpp = chars_per_page(doc)
            if cpp < DIGITAL_THRESHOLD and not args.all:
                print(f"SKIP     {src.name}  ({cpp:.0f} chars/page -- not DIGITAL)")
                continue
            print(f"\n=== {src.name}  ({cpp:.0f} chars/page, {len(doc)} pages) ===")
            processed += 1
            n = min(len(doc), args.max_pages)
            for i in range(n):
                page_id = f"{stem}_p{i + 1}"
                text = doc[i].get_text()
                (out / "text" / f"{page_id}.txt").write_text(text, encoding="utf-8")
                if args.write_truth and text.strip():
                    tp = Path(args.truth) / f"{page_id}.txt"
                    tp.parent.mkdir(parents=True, exist_ok=True)
                    tp.write_text(text, encoding="utf-8")
                    truth_written += 1
                print(f"  page {i + 1}: {len(text):6d} chars of native text")

        if pdfplumber is None:
            continue

        with pdfplumber.open(src) as pdf:
            for i, page in enumerate(pdf.pages[:args.max_pages]):
                page_id = f"{stem}_p{i + 1}"
                try:
                    tables = page.extract_tables()
                except Exception as e:
                    print(f"  page {i + 1}: table extraction failed -- {str(e)[:60]}")
                    continue
                if not tables:
                    continue
                grand_tables += len(tables)
                md_parts, js = [], []
                for t_i, table in enumerate(tables, 1):
                    rows = len(table)
                    cols = max((len(r) for r in table), default=0)
                    print(f"  page {i + 1}: TABLE {t_i} -> {rows} rows x {cols} cols")
                    md_parts.append(f"## Table {t_i} ({rows}x{cols})\n\n"
                                    + to_markdown(table))
                    js.append({"table_index": t_i, "rows": rows, "cols": cols,
                               "data": table})
                (out / "tables" / f"{page_id}.md").write_text(
                    "\n\n".join(md_parts), encoding="utf-8")
                (out / "tables" / f"{page_id}.json").write_text(
                    json.dumps(js, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 70)
    print(f"  {processed} DIGITAL document(s) processed, "
          f"{grand_tables} table(s) detected")
    if args.write_truth:
        print(f"  {truth_written} exact ground-truth file(s) written to {args.truth}")
    print("=" * 70)
    print(f"  Native text  : {out / 'text'}")
    print(f"  Tables       : {out / 'tables'}")
    print()
    print("NOW JUDGE IT YOURSELF. Open a .md table beside the real page:")
    print("  - right number of rows and columns?  -> native parsing works, no OCR")
    print("  - merged or shifted cells?           -> needs a table-aware parser")
    print("  - nothing detected?                  -> the table has no ruling lines;")
    print("                                          try pdfplumber table_settings")
    print("                                          with 'text' strategy at M3")
    print()
    print("Write the answer into docs/M0_report.md. It decides the M3 design.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())