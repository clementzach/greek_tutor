#!/usr/bin/env bash
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)

cd "$ROOT"

# Load .env if present
if [[ -f .env ]]; then
  set -a
  # shellcheck source=/dev/null
  source .env
  set +a
fi

PYTHON_BIN=${PYTHON_BIN:-python3.13}
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "[error] $PYTHON_BIN not found. Install Python 3.13 or set PYTHON_BIN to your interpreter." >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  echo "[dev] Creating virtualenv with $PYTHON_BIN..."
  "$PYTHON_BIN" -m venv .venv
fi

source .venv/bin/activate

echo "[dev] Installing dependencies..."
pip install --upgrade pip >/dev/null
pip install flask fastapi uvicorn openai pydantic gunicorn markdown bleach >/dev/null

echo "[dev] Initializing databases..."
python db_init.py

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "[warn] OPENAI_API_KEY is not set. The agent will not work until set."
fi

FLASK_PORT=${FLASK_PORT:-5000}
API_PORT=${API_PORT:-8000}
echo "[dev] Starting FastAPI on ${API_PORT} and Flask on ${FLASK_PORT}..."

FASTAPI_URL=${FASTAPI_URL:-http://127.0.0.1:${API_PORT}}

(.venv/bin/uvicorn fastapi_server.api:app --host 127.0.0.1 --port "${API_PORT}" --reload & echo $! > .fastapi.pid)

trap 'echo "[dev] Stopping..."; if [[ -f .fastapi.pid ]]; then kill $(cat .fastapi.pid) 2>/dev/null || true; rm -f .fastapi.pid; fi' EXIT

export FLASK_APP=flask_app.app:app
export FASTAPI_URL
flask run --host 127.0.0.1 --port "${FLASK_PORT}" --debug
