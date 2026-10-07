#!/usr/bin/env python3
"""Create the first admin user.

    python scripts/create_admin.py --email you@duet.ac.bd --name "Your Name"

The password is prompted for, never passed on the command line -- shell
history is not a secret store.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

# Run from anywhere: python scripts/create_admin.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from database.connection import session_scope  # noqa: E402
from database.models import User, UserRole  # noqa: E402
from security.hashing import hash_password  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Create an admin user")
    ap.add_argument("--email", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--teacher", action="store_true",
                    help="create a teacher instead of an admin")
    args = ap.parse_args()

    pw = getpass.getpass("Password: ")
    if len(pw) < 12:
        return _fail("Password must be at least 12 characters.")
    if pw != getpass.getpass("Confirm: "):
        return _fail("Passwords do not match.")

    role = UserRole.teacher if args.teacher else UserRole.admin
    with session_scope() as s:
        existing = s.scalar(select(User).where(User.email == args.email))
        if existing:
            return _fail(f"{args.email} already exists (id={existing.id}).")
        user = User(
            email=args.email,
            full_name=args.name,
            password_hash=hash_password(pw),
            role=role,
        )
        s.add(user)
        s.flush()
        print(f"Created {role.value} '{args.name}' <{args.email}> id={user.id}")
    return 0


def _fail(msg: str) -> int:
    print(f"ERROR: {msg}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())