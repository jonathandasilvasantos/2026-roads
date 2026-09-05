#!/usr/bin/env bash
# Launcher for fullscreen Roads. --windowed is available for development.
set -euo pipefail

cd "$(dirname "$0")"

PY="./env/bin/python"
if [[ ! -x "$PY" ]]; then
    echo "venv not found at $PY"
    echo "create it first:  python3.12 -m venv env && $PY -m pip install -r requirements.txt"
    exit 1
fi

WINDOWED=0
for arg in "$@"; do
    if [[ "$arg" == "--windowed" ]]; then WINDOWED=1; fi
done
if [[ "$WINDOWED" == "1" ]]; then exec "$PY" app.py "$@"; fi
exec "$PY" app.py --fullscreen "$@"
