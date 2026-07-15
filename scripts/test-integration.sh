#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -z "${AGROMECH_TEST_POSTGRES_URL:-}" ]]; then
  echo "error: AGROMECH_TEST_POSTGRES_URL is required and must be non-empty" >&2
  exit 2
fi

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

DATABASE_URL="$AGROMECH_TEST_POSTGRES_URL" "$PYTHON_BIN" -m alembic upgrade head

PYTHONDONTWRITEBYTECODE=1 "$PYTHON_BIN" -m pytest -s -p no:cacheprovider \
  backend/tests/test_bm25_retrieval.py \
  backend/tests/test_infrastructure_config.py \
  backend/tests/test_dependency_health.py \
  backend/tests/test_document_upload.py \
  backend/tests/test_document_library.py \
  backend/tests/test_ingest_state_machine.py \
  backend/tests/test_hybrid_retrieval.py \
  backend/tests/test_text_qa.py \
  backend/tests/test_image_qa.py
