#!/usr/bin/env python
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "worker"))

from agromech_api.core.database import get_engine  # noqa: E402
from scripts.rebuild_vector_index import rebuild_vector_index  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild pgvector-backed indexes for indexed documents.")
    parser.add_argument("--document-id", help="Only rebuild indexes for one document.")
    visual_group = parser.add_mutually_exclusive_group()
    visual_group.add_argument(
        "--include-visual",
        dest="include_visual",
        action="store_true",
        default=True,
        help="Rebuild visual page vector indexes.",
    )
    visual_group.add_argument(
        "--no-visual",
        dest="include_visual",
        action="store_false",
        help="Skip visual page vector indexes.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Count matching documents without rebuilding indexes.")
    args = parser.parse_args()

    summary = rebuild_vector_index(
        get_engine(),
        document_id=args.document_id,
        include_visual=args.include_visual,
        dry_run=args.dry_run,
    )
    print(f"selected={summary.selected} succeeded={summary.succeeded} failed={summary.failed}")
    for document_id, error in summary.failures:
        print(f"failure document_id={document_id} error={error}")
    return 1 if summary.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
