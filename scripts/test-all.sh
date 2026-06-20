#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

scripts/lint.sh
scripts/test-unit.sh
scripts/test-integration.sh
scripts/e2e-smoke.sh
npm run build --prefix frontend
