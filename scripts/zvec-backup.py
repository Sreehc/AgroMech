#!/usr/bin/env python
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from agromech_api.core.config import Settings
from agromech_api.zvec_backup import (  # noqa: E402
    RestoreStatus,
    backup_zvec,
    cleanup_zvec_backups,
    restore_zvec_backup,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup, cleanup, or restore the configured Zvec directory.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("backup", help="Create a timestamped Zvec tar.gz backup.")
    subparsers.add_parser("cleanup", help="Delete expired Zvec backups.")
    restore_parser = subparsers.add_parser("restore", help="Restore Zvec from a backup archive.")
    restore_parser.add_argument("archive", help="Path to a zvec-*.tar.gz archive.")

    args = parser.parse_args()
    settings = Settings()

    if args.command == "backup":
        result = backup_zvec(settings)
        print(result.message)
        print(result.archive_path)
        return 0 if result.created else 1
    if args.command == "cleanup":
        result = cleanup_zvec_backups(settings)
        for deleted_path in result.deleted_paths:
            print(deleted_path)
        print(f"deleted={len(result.deleted_paths)}")
        return 0
    if args.command == "restore":
        result = restore_zvec_backup(settings, args.archive)
        print(result.message)
        print(result.restore_path)
        return 0 if result.status == RestoreStatus.RESTORED else 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
