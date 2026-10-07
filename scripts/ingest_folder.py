#!/usr/bin/env python3
"""Ingest a folder of PDFs.

    python scripts/ingest_folder.py m0_reality_check/samples --owner 1
    python scripts/ingest_folder.py <folder> --owner 1 --dry-run

Safe to re-run. An identical file is recognised by its sha256 and does no
work at all, so pointing this at the document folder as often as you like
costs nothing for what has not changed.

The number to write down is chunks/sec. Your adviser will ask how long the
whole corpus takes to ingest, and "a few minutes probably" is not an answer
this project is allowed to give.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from database.connection import session_scope  # noqa: E402
from database.models import User  # noqa: E402
from ingestion.classify import DIGITAL, classify_pdf  # noqa: E402
from ingestion.pipeline import IngestionRefused, ingest_pdf  # noqa: E402


def check_owner(owner_id: int) -> str | None:
    """Fail once, up front, instead of once per file.

    A missing owner is a configuration mistake, not a per-document problem.
    The first version retried every file and printed an identical foreign-key
    stack trace for each -- on a 200-document folder that buries the one
    sentence that matters under 200 copies of the same noise.
    """
    with session_scope() as session:
        user = session.get(User, owner_id)
        if user:
            return None
        existing = session.execute(
            select(User.id, User.email).order_by(User.id).limit(10)
        ).all()
    if not existing:
        return (f"No user {owner_id}, and the users table is empty.\n"
                f"Create one first:\n"
                f"  python scripts/create_admin.py --email you@duet.ac.bd "
                f'--name "Your Name"')
    listing = "\n".join(f"  {i}  {e}" for i, e in existing)
    return f"No user {owner_id}. Existing users:\n{listing}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest a folder of PDFs")
    ap.add_argument("folder")
    ap.add_argument("--owner", type=int, required=True,
                    help="user id that owns these documents "
                         "(python scripts/create_admin.py prints one)")
    ap.add_argument("--department")
    ap.add_argument("--private", action="store_true",
                    help="do not make these visible to every teacher")
    ap.add_argument("--dry-run", action="store_true",
                    help="classify only, touch nothing")
    args = ap.parse_args()

    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"No such folder: {folder}", file=sys.stderr)
        return 1

    pdfs = sorted(folder.rglob("*.pdf"))
    if not pdfs:
        print(f"No PDFs in {folder}", file=sys.stderr)
        return 1

    if args.dry_run:
        for pdf in pdfs:
            cls = classify_pdf(pdf)
            mark = "will ingest" if cls.doc_class == DIGITAL else "SKIP (needs OCR, M3)"
            print(f"{cls.doc_class:<9} {mark:<22} {pdf.name}")
        return 0

    problem = check_owner(args.owner)
    if problem:
        print(problem, file=sys.stderr)
        return 1

    started = time.perf_counter()
    ingested = skipped = refused = 0
    chunks = 0
    embed_seconds = 0.0

    for pdf in pdfs:
        try:
            with session_scope() as session:
                r = ingest_pdf(
                    session, pdf,
                    owner_id=args.owner,
                    department=args.department,
                    is_public=not args.private,
                )
        except IngestionRefused as e:
            refused += 1
            print(f"  refused  {pdf.name}\n           {e}")
            continue
        except Exception as e:  # noqa: BLE001
            refused += 1
            print(f"  FAILED   {pdf.name}: {type(e).__name__}: "
                  f"{str(e).splitlines()[0][:160]}")
            continue

        if r.skipped:
            skipped += 1
            print(f"  skipped  {pdf.name}  ({r.reason})")
            continue

        ingested += 1
        chunks += r.chunks_created + r.chunks_reused
        embed_seconds += r.seconds_embedding
        fonts = f"  legacy={sorted(r.legacy_fonts)}" if r.legacy_fonts else ""
        print(f"  ingested {pdf.name}")
        print(f"           {r.doc_class}  {r.pages} page(s)  "
              f"{r.chunks_created} new + {r.chunks_reused} reused chunk(s)  "
              f"{r.seconds_total:.1f}s{fonts}")

    elapsed = time.perf_counter() - started
    print()
    print("=" * 66)
    print(f"ingested {ingested}   skipped {skipped}   refused {refused}")
    print(f"chunks   {chunks}")
    print(f"wall     {elapsed:.1f}s   embedding {embed_seconds:.1f}s")
    if embed_seconds > 0:
        print(f"rate     {chunks / embed_seconds:.2f} chunk/s")
        print()
        print("Write the rate into docs/M1_report.md together with the machine")
        print("it was measured on. A rate without its hardware is not a number.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())