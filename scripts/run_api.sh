#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

# Load .env if present
if [[ -f .env ]]; then
  set -a
  # shellcheck source=/dev/null
  source .env
  set +a
fi
source .venv/bin/activate

API_PORT=${API_PORT:-8000}
exec uvicorn fastapi_server.api:app --host 127.0.0.1 --port "${API_PORT}" --reload
