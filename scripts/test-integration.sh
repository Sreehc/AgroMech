#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

PYTHONDONTWRITEBYTECODE=1 "$PYTHON_BIN" -m pytest -s -p no:cacheprovider \
  backend/tests/test_document_upload.py \
  backend/tests/test_document_library.py \
  backend/tests/test_ingest_state_machine.py \
  backend/tests/test_hybrid_retrieval.py \
  backend/tests/test_text_qa.py \
  backend/tests/test_image_qa.py
