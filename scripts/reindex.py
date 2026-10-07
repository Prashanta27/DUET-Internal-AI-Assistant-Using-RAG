#!/usr/bin/env python3
"""Re-embed chunks whose embedding_model / embedding_rev differs from config.

This exists from M1, BEFORE it is needed, because the day you need it you
will need it urgently. Changing the embedding model invalidates every
vector: vectors from two models cannot be compared, so a half-migrated
table returns silently wrong results rather than an error.

    python scripts/reindex.py --dry-run
    python scripts/reindex.py --batch 32

It never drops the table. It processes in batches and commits as it goes,
so it can be interrupted and resumed.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Run from anywhere: python scripts/reindex.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, or_, select  # noqa: E402

from database.connection import session_scope  # noqa: E402
from database.models import Chunk  # noqa: E402
from embeddings.registry import embedding_identity, get_embedder  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Re-embed stale chunks")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--limit", type=int, default=None,
                    help="stop after this many chunks (for a timed sample)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    model, rev = embedding_identity()
    stale = or_(
        Chunk.embedding_model != model,
        Chunk.embedding_rev != rev,
        Chunk.embedding.is_(None),
    )

    with session_scope() as s:
        total = s.scalar(select(func.count()).select_from(Chunk).where(stale)) or 0
        overall = s.scalar(select(func.count()).select_from(Chunk)) or 0

    print(f"Configured embedding: {model} rev {rev}")
    print(f"Chunks total        : {overall}")
    print(f"Chunks to re-embed  : {total}")
    if args.dry_run or total == 0:
        return 0

    embedder = get_embedder()
    done = 0
    t0 = time.perf_counter()

    while True:
        if args.limit is not None and done >= args.limit:
            break
        with session_scope() as s:
            rows = list(s.scalars(
                select(Chunk).where(stale).order_by(Chunk.id).limit(args.batch)
            ))
            if not rows:
                break
            vectors = embedder.embed_documents([r.content for r in rows])
            for row, vec in zip(rows, vectors, strict=True):
                row.embedding = vec
                row.embedding_model = model
                row.embedding_rev = rev
        done += len(rows)
        rate = done / max(time.perf_counter() - t0, 1e-6)
        remaining = (total - done) / rate if rate else 0
        print(f"  {done}/{total}  {rate:.2f} chunk/s  ~{remaining/60:.1f} min left")

    elapsed = time.perf_counter() - t0
    print(f"\nRe-embedded {done} chunk(s) in {elapsed:.1f}s "
          f"({done/max(elapsed,1e-6):.2f} chunk/s).")
    print("Record this rate in docs/M1_report.md -- ingestion throughput is a "
          "number your adviser will ask for.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())