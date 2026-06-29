#!/usr/bin/env python
from __future__ import annotations

import argparse
import getpass
from pathlib import Path
import sys

from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from agromech_api.auth import create_database_user  # noqa: E402
from agromech_api.config import get_settings  # noqa: E402
from agromech_api.db.enums import UserRole  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create an AgroMech database user.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", choices=[role.value for role in UserRole], default=UserRole.ADMIN.value)
    parser.add_argument("--display-name", default=None)
    parser.add_argument("--password", default=None, help="Prefer omitting this flag and entering the password prompt.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    password = args.password or getpass.getpass("Password: ")
    if not password:
        print("Password cannot be empty.", file=sys.stderr)
        return 2

    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    user = create_database_user(
        engine,
        username=args.username,
        password=password,
        role=UserRole(args.role),
        display_name=args.display_name,
    )
    if user is None:
        print(f"User already exists: {args.username}", file=sys.stderr)
        return 1
    print(f"Created {user.role.value} user: {user.username}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
