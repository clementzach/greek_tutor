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
export FLASK_APP=flask_app.app:app

FLASK_PORT=${FLASK_PORT:-5000}
exec flask run --host 127.0.0.1 --port "${FLASK_PORT}" --debug
