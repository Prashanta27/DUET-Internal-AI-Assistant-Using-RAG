#!/usr/bin/env python3
"""Measure how accurate the legacy-font conversion actually is.

The same shape as the M0 OCR bake-off, on a smaller scale, because the same
trap applies: converted text LOOKS like Bangla, so it is easy to assume it
is correct. It is not. Measured gaps already known -- conjuncts and reph:

    cixÿv          -> পরীÿা        should be পরীক্ষা
    AvÛviMÖ¨vRy‡qU -> আন্ডারগর্যাজুয়েট  should be আন্ডারগ্র্যাজুয়েট

Those are exactly the words teachers search for. A number is needed.

STEP 1 -- sample and render
    python scripts/legacy_bakeoff.py "m0_reality_check/samples/DUET admission information session 2024-2025.pdf"

    Writes legacy_check/<doc>/
        pages/p12.png     the source pages, so you correct against the PAGE
        context.txt       page number + original Bijoy bytes for each line
        draft.txt         the converter's output, one line per sample

STEP 2 -- type the truth
    Copy draft.txt to truth.txt. Then, reading the PAGE IMAGES, correct
    every line to what is actually printed.

    Correct against the image, never against draft.txt. Approving the
    converter's own output produces a CER of 0.000 and measures nothing --
    the same failure the M0 harness warns about for seeded OCR truth.

STEP 3 -- score
    python scripts/legacy_bakeoff.py "<same pdf>" --score
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.text_metrics import cer, verdict, wer  # noqa: E402
from ingestion.classify import _import_pymupdf  # noqa: E402
from ingestion.legacy_convert import BijoyConverter  # noqa: E402
from ingestion.loaders.pdf import load_pdf  # noqa: E402

OUT_ROOT = Path("legacy_check")


def sample_lines(pdf: Path, n: int) -> tuple[list[tuple[int, str, str]], set[str]]:
    """(page_no, raw_line, converted_line) spread across the document.

    The document is loaded twice -- once raw, once converted -- and the two
    are paired line for line. Only span TEXT changes between the passes, so
    the line structure is identical.
    """
    raw_pages, _, _ = load_pdf(pdf, convert_legacy=False)
    new_pages, _, report = load_pdf(pdf, convert_legacy=True)

    found: list[tuple[int, str, str]] = []
    for raw, new in zip(raw_pages, new_pages, strict=True):
        for a, b in zip(raw.text.splitlines(), new.text.splitlines(), strict=False):
            if a.strip() and a != b and len(a.strip()) > 12:
                found.append((raw.page_no, a.strip(), b.strip()))

    if not found:
        return [], report.legacy_fonts
    if len(found) <= n:
        return found, report.legacy_fonts
    step = len(found) / n
    return [found[int(i * step)] for i in range(n)], report.legacy_fonts


def render_pages(pdf: Path, page_nos: set[int], out_dir: Path, dpi: int = 150) -> None:
    fitz = _import_pymupdf()
    if fitz is None:
        print("  ! PyMuPDF missing, cannot render pages", file=sys.stderr)
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    zoom = dpi / 72.0
    with fitz.open(pdf) as doc:
        for n in sorted(page_nos):
            dst = out_dir / f"p{n}.png"
            if not dst.exists():
                doc[n - 1].get_pixmap(matrix=fitz.Matrix(zoom, zoom)).save(dst)


def prepare(pdf: Path, n: int) -> int:
    ok, detail = BijoyConverter().available()
    if not ok:
        print(f"Converter unavailable: {detail}", file=sys.stderr)
        return 1

    samples, fonts = sample_lines(pdf, n)
    if not samples:
        print(f"No legacy lines found in {pdf.name}.", file=sys.stderr)
        return 1

    out = OUT_ROOT / pdf.stem
    out.mkdir(parents=True, exist_ok=True)
    render_pages(pdf, {p for p, _, _ in samples}, out / "pages")

    converted = [c for _, _, c in samples]

    (out / "draft.txt").write_text("\n".join(converted) + "\n", encoding="utf-8")
    (out / "context.txt").write_text(
        "\n".join(f"[{i + 1}] page {p}\n    bijoy : {raw}\n    draft : {c}"
                  for i, (p, raw, c) in enumerate(samples)) + "\n",
        encoding="utf-8",
    )

    print(f"Legacy fonts detected : {sorted(fonts)}")
    print(f"Sampled {len(samples)} legacy line(s) from {pdf.name}")
    print(f"  pages rendered : {out / 'pages'}")
    print(f"  context        : {out / 'context.txt'}")
    print(f"  draft          : {out / 'draft.txt'}")
    print()
    print("NEXT: copy draft.txt to truth.txt, then correct every line while")
    print("      looking at the PAGE IMAGE -- not at the draft. Approving the")
    print("      converter's own output scores 0.000 and measures nothing.")
    print(f"      Then: python {Path(__file__).name} \"{pdf}\" --score")
    return 0


def score(pdf: Path, fold_case: bool) -> int:
    out = OUT_ROOT / pdf.stem
    draft_f, truth_f = out / "draft.txt", out / "truth.txt"
    if not truth_f.exists():
        print(f"No {truth_f}. Run without --score first, then type the truth.",
              file=sys.stderr)
        return 1

    draft = draft_f.read_text(encoding="utf-8").splitlines()
    truth = truth_f.read_text(encoding="utf-8").splitlines()
    if len(draft) != len(truth):
        print(f"draft.txt has {len(draft)} lines but truth.txt has {len(truth)}. "
              f"They must correspond line for line -- do not add or remove lines.",
              file=sys.stderr)
        return 1

    rows = []
    for i, (d, t) in enumerate(zip(draft, truth, strict=True), 1):
        if not t.strip():
            continue  # blank truth line = not yet typed, skipped
        rows.append((i, cer(t, d, fold_case), wer(t, d, fold_case), d, t))

    if not rows:
        print("Every truth line is empty. Nothing was scored.", file=sys.stderr)
        return 1

    cers = [r[1] for r in rows]
    mean = statistics.fmean(cers)

    print("=" * 70)
    print(f"  LEGACY CONVERSION -- {pdf.name}")
    print("=" * 70)
    print(f"lines scored : {len(rows)} of {len(draft)}")
    print(f"mean CER     : {mean:.3f}   <- {verdict(mean)}")
    print(f"median CER   : {statistics.median(cers):.3f}")
    print(f"worst CER    : {max(cers):.3f}")
    print(f"mean WER     : {statistics.fmean([r[2] for r in rows]):.3f}")
    print()
    print("Compare against M0: Tesseract on scanned pages was mean CER 0.307.")
    print("Conversion only earns its place by being clearly better than that.")
    print()

    worst = sorted(rows, key=lambda r: -r[1])[:5]
    print("Worst lines -- read these, the pattern is the fix:")
    for i, c, _, d, t in worst:
        print(f"  [{i}] CER {c:.3f}")
        print(f"      got  : {d}")
        print(f"      want : {t}")
    print()
    print("A repeated wrong glyph is one entry in legacy_convert.POST_MAP.")
    print("A repeated wrong ORDER (reph, ya-phala) is not -- that is upstream")
    print("logic, and patching it by hand is how you introduce a silent error.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Measure legacy-font conversion accuracy")
    ap.add_argument("pdf")
    ap.add_argument("-n", "--samples", type=int, default=20)
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--no-case-fold", action="store_true")
    ap.add_argument("--no-reph-fix", action="store_true",
                    help="draft without the reph reordering, to measure what "
                         "it is actually worth")
    args = ap.parse_args()

    pdf = Path(args.pdf)
    if not pdf.is_file():
        print(f"No such file: {pdf}", file=sys.stderr)
        return 1
    return (score(pdf, not args.no_case_fold) if args.score
            else prepare(pdf, args.samples))


if __name__ == "__main__":
    raise SystemExit(main())