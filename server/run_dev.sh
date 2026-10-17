#!/bin/sh
# Development runner: starts the auth service on :8787 with dev settings.
# Usage: ./run_dev.sh   (Ctrl+C to stop)
set -eu
HERE="$(dirname "$0")"
cd "$HERE"

PYTHON=${PYTHON:-python3}
if ! "$PYTHON" -c "import fastapi" 2>/dev/null; then
  echo "[run_dev] installing requirements..."
  "$PYTHON" -m pip install --quiet -r requirements.txt
fi

export FF_PUBLIC_ORIGIN="${FF_PUBLIC_ORIGIN:-http://127.0.0.1:8787}"
export FF_COOKIE_SECURE="${FF_COOKIE_SECURE:-0}"   # dev over plain http
export FF_AUTH_DB="${FF_AUTH_DB:-$PWD/dev_auth.db}"
export FF_STATIC_DIR="${FF_STATIC_DIR:-../website}"
export FF_SMTP_HOST=""                             # dry-run: mails suppressed
export FF_LOG_LEVEL=DEBUG

exec "$PYTHON" -m uvicorn app:app --host 127.0.0.1 --port "${PORT:-8787}"
