#!/usr/bin/env bash
# Linux/macOS launcher: creates a venv, installs deps, starts the app (UI + API on one port).
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
$PY -c 'import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)' || { echo "Python 3.11+ required"; exit 1; }
[ -d venv ] || $PY -m venv venv
venv/bin/pip install -q -r requirements.txt
echo "Open http://localhost:8000  (first run prints the initial account passwords once)"
exec venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
