#!/usr/bin/env python3
"""
M0 self-test -- verify the bake-off harness works BEFORE you spend hours
typing ground truth for real DUET documents.

    python selftest.py            # run all checks, clean up afterwards
    python selftest.py --keep     # leave _selftest/ on disk to inspect

WHAT IT CHECKS
    1. Required packages import
    2. CER / WER maths is correct, including Bangla zero-width handling
    3. PDF rasterisation works
    4. DIGITAL vs SCANNED classification is correct
    5. At least one OCR engine is installed and reachable
    6. The full pipeline produces a scored summary.csv

WHAT IT DOES NOT CHECK
    Bangla OCR *quality*. The synthetic pages are English, because rendering
    correctly shaped Bangla text requires complex-script layout that a test
    fixture cannot honestly fake. Bangla accuracy is only measurable on real
    scanned DUET documents -- that is the actual point of M0.

A green self-test means "the tool is not lying to you". It does not mean
the OCR is good enough for the project.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = HERE / "_selftest"

PAGE1 = [
    "DUET Academic Calendar 2026",
    "",
    "Examination begins on 12 March 2026 for all undergraduate programmes.",
    "All departments must submit final results within 21 days of the last",
    "examination date. Late submissions require written approval from the",
    "Controller of Examinations.",
    "",
    "Registration for the Spring semester closes on 28 February 2026.",
    "Students who fail to register by that date must apply for late",
    "registration and pay the prescribed fee of 500 taka.",
    "",
    "Contact the Controller of Examinations for corrections to published",
    "results. Corrections requested after 30 April 2026 will not be",
    "considered under any circumstances.",
]
PAGE2 = [
    "Department of Computer Science and Engineering",
    "First Year First Semester Course List",
    "",
    "Course Code    Course Name              Credit",
    "CSE1101        Programming              3",
    "CSE1102        Data Structure           3",
    "CSE1103        Digital Logic Design     4",
    "CSE1104        Discrete Mathematics     3",
    "MATH1101       Calculus and Geometry    3",
    "",
    "Total credit for the semester is 16. A student must maintain a grade",
    "point average of at least 2.20 to continue to the next semester.",
    "Any student who fails more than two courses must repeat the semester.",
]

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"
results: list[tuple[str, str, str]] = []


def record(name: str, status: str, detail: str = "") -> None:
    results.append((name, status, detail))
    icon = {"PASS": "  ok  ", "FAIL": " FAIL ", "WARN": " warn "}[status]
    print(f"[{icon}] {name}" + (f"  --  {detail}" if detail else ""))


# ---------------------------------------------------------------- check 1
def check_imports() -> bool:
    try:
        import pymupdf  # noqa: F401
    except ImportError:
        try:
            import fitz  # noqa: F401
        except ImportError:
            record("packages", FAIL, "pip install pymupdf")
            return False
    try:
        import PIL  # noqa: F401
    except ImportError:
        record("packages", FAIL, "pip install pillow")
        return False
    try:
        from rapidfuzz.distance import Levenshtein  # noqa: F401
        extra = "rapidfuzz present (fast scoring)"
    except ImportError:
        extra = "rapidfuzz absent -- pure-Python fallback, slower but correct"
    record("packages", PASS, extra)
    return True


# ---------------------------------------------------------------- check 2
def check_metrics() -> bool:
    sys.path.insert(0, str(HERE))
    try:
        from ocr_bakeoff import cer, wer, normalise
    except Exception as e:
        record("metrics import", FAIL, str(e)[:90])
        return False

    cases: list[tuple[str, bool]] = []

    # identical strings score zero
    cases.append(("identical text -> 0", cer("hello world", "hello world") == 0.0))

    # a known single-character substitution
    cases.append(("1 char wrong in 6 -> 1/6",
                  abs(cer("abcdef", "abcdXf") - 1 / 6) < 1e-9))

    # one wrong word out of four
    cases.append(("1 word wrong in 4 -> 0.25",
                  abs(wer("the exam is monday", "the exam is friday") - 0.25) < 1e-9))

    # whitespace and case must not count as OCR errors
    cases.append(("whitespace collapsed", cer("a b", "a      b") == 0.0))
    cases.append(("case folded by default", cer("Exam Routine", "exam routine") == 0.0))
    cases.append(("case counted when asked",
                  cer("Exam Routine", "exam routine", fold_case=False) > 0))

    # Bangla: ZWNJ must be ignored, otherwise a correct read scores as an error
    bn = "\u09aa\u09b0\u09c0\u0995\u09cd\u09b7\u09be\u09b0 \u09b0\u09c1\u099f\u09bf\u09a8"
    bn_zwnj = "\u09aa\u09b0\u09c0\u0995\u09cd\u09b7\u09be\u09b0  \u09b0\u09c1\u099f\u09bf\u09a8\u200c"
    cases.append(("Bangla ZWNJ ignored", cer(bn, bn_zwnj) == 0.0))

    # Bangla: a real substitution must still be caught
    bn_wrong = bn.replace("\u09b0\u09c1", "\u09b0\u09c2")
    cases.append(("Bangla real error caught", cer(bn, bn_wrong) > 0))

    # Unicode composition differences must not count
    import unicodedata
    cases.append(("NFC normalised",
                  cer("é", unicodedata.normalize("NFD", "é")) == 0.0))

    # empty reference edge cases
    cases.append(("empty ref, empty hyp -> 0", cer("", "") == 0.0))
    cases.append(("empty ref, some hyp -> 1", cer("", "junk") == 1.0))

    failed = [name for name, ok in cases if not ok]
    if failed:
        record("metric maths", FAIL, f"{len(failed)} failed: {failed[:3]}")
        return False
    record("metric maths", PASS, f"{len(cases)} assertions")
    return True


# ---------------------------------------------------------------- check 3
def build_fixtures() -> bool:
    """Create one DIGITAL pdf (real text layer) and one SCANNED pdf (image only)."""
    try:
        import pymupdf as pm
    except ImportError:
        import fitz as pm  # type: ignore

    samples = WORK / "samples"
    truth = WORK / "ground_truth"
    samples.mkdir(parents=True, exist_ok=True)
    truth.mkdir(parents=True, exist_ok=True)

    try:
        doc = pm.open()
        for lines in (PAGE1, PAGE2):
            page = doc.new_page()
            y = 70
            for line in lines:
                page.insert_text((60, y), line, fontsize=13, fontname="helv")
                y += 22
        doc.save(samples / "digital_doc.pdf")
        doc.close()

        # image-only PDF: render the first page, then embed it as a picture
        src = pm.open(samples / "digital_doc.pdf")
        pix = src[0].get_pixmap(dpi=150)
        out = pm.open()
        pg = out.new_page(width=pix.width, height=pix.height)
        pg.insert_image(pm.Rect(0, 0, pix.width, pix.height), pixmap=pix)
        out.save(samples / "scanned_doc.pdf")
        out.close()
        src.close()
    except Exception as e:
        record("fixture pdfs", FAIL, str(e)[:90])
        return False

    # ground truth is known exactly, because we wrote the text ourselves
    (truth / "digital_doc_p1.txt").write_text("\n".join(PAGE1), encoding="utf-8")
    (truth / "digital_doc_p2.txt").write_text("\n".join(PAGE2), encoding="utf-8")
    (truth / "scanned_doc_p1.txt").write_text("\n".join(PAGE1), encoding="utf-8")

    record("fixture pdfs", PASS, "2 pdfs, 3 ground-truth pages")
    return True


# ---------------------------------------------------------------- check 4
def check_classification() -> bool:
    sys.path.insert(0, str(HERE))
    from ocr_bakeoff import text_layer_chars_per_page

    digital = text_layer_chars_per_page(WORK / "samples" / "digital_doc.pdf")
    scanned = text_layer_chars_per_page(WORK / "samples" / "scanned_doc.pdf")
    if digital is None or scanned is None:
        record("classification", FAIL, "could not read the fixture pdfs")
        return False
    ok = digital >= 200 and scanned < 20
    record("classification", PASS if ok else FAIL,
           f"digital={digital:.0f} chars/page, scanned={scanned:.0f} chars/page")
    return ok


# ---------------------------------------------------------------- check 5+6
def check_pipeline() -> bool:
    cmd = [sys.executable, str(HERE / "ocr_bakeoff.py"),
           "--samples", str(WORK / "samples"),
           "--truth", str(WORK / "ground_truth"),
           "--out", str(WORK / "results"),
           "--engines", "tesseract",
           "--max-pages", "2"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = proc.stdout + proc.stderr

    if "No engines available" in out:
        record("ocr engine", WARN,
               "no OCR engine installed -- see requirements/ocr.txt")
        record("pipeline", WARN, "skipped (needs an engine)")
        return True  # harness is fine; the engine is simply not installed yet

    if proc.returncode != 0:
        record("pipeline", FAIL, out.strip().splitlines()[-1][:110] if out.strip() else "no output")
        return False

    if "missing traineddata" in out:
        record("ocr engine", FAIL,
               "tesseract found but Bengali traineddata missing -- install tesseract-ocr-ben")
        return False
    record("ocr engine", PASS, "tesseract with ben+eng")

    summary = WORK / "results" / "summary.csv"
    if not summary.exists():
        record("pipeline", FAIL, "summary.csv was not written")
        return False

    with summary.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        record("pipeline", FAIL, "summary.csv is empty")
        return False

    row = rows[0]
    scored = int(row["pages_scored"] or 0)
    if scored < 3:
        record("pipeline", FAIL, f"only {scored}/3 pages were scored")
        return False

    try:
        mean_cer = float(row["mean_cer"])
    except (TypeError, ValueError):
        record("pipeline", FAIL, "mean_cer missing from summary")
        return False

    # Clean rendered English at 300 DPI should be near-perfect. A high number
    # here means the harness or the install is broken, not that OCR is hard.
    if mean_cer > 0.15:
        record("pipeline", FAIL,
               f"mean CER {mean_cer:.3f} on clean synthetic English -- "
               f"something is wrong with the install, not with OCR")
        return False

    record("pipeline", PASS,
           f"3 pages scored, mean CER {mean_cer:.3f}, "
           f"{row['mean_sec_per_page']}s per page")
    return True


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="Self-test for the M0 OCR bake-off harness")
    ap.add_argument("--keep", action="store_true",
                    help="keep _selftest/ on disk so you can inspect the output")
    args = ap.parse_args()

    print("=" * 70)
    print("  M0 SELF-TEST")
    print("=" * 70)

    if WORK.exists():
        shutil.rmtree(WORK)

    ok = True
    ok &= check_imports()
    if ok:
        ok &= check_metrics()
    if ok:
        ok &= build_fixtures()
    if ok:
        ok &= check_classification()
    if ok:
        ok &= check_pipeline()

    print("=" * 70)
    failures = [r for r in results if r[1] == FAIL]
    warnings = [r for r in results if r[1] == WARN]

    if failures:
        print(f"  RESULT: FAILED ({len(failures)} check(s))")
        print("=" * 70)
        for name, _, detail in failures:
            print(f"  - {name}: {detail}")
        print("\nFix these before running the bake-off on real documents.")
    else:
        print(f"  RESULT: PASSED"
              + (f"  ({len(warnings)} warning(s))" if warnings else ""))
        print("=" * 70)
        for name, _, detail in warnings:
            print(f"  ! {name}: {detail}")
        print("\nThe harness works. Next:")
        print("  1. put 8-15 real DUET documents in m0_reality_check/samples/")
        print("  2. python m0_reality_check/ocr_bakeoff.py --make-truth-stubs")
        print("  3. type the correct text into 10-20 files in")
        print("     m0_reality_check/ground_truth/")
        print("  4. python m0_reality_check/ocr_bakeoff.py --engines tesseract")
        print()
        print("Remember: this test used clean English. It proves the tool is")
        print("honest, not that Bangla OCR will be good enough. Only real")
        print("scanned DUET pages can tell you that.")

    if not args.keep and WORK.exists():
        shutil.rmtree(WORK)
    elif args.keep:
        print(f"\nFixtures kept in {WORK}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())