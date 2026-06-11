#!/usr/bin/env bash
# Launcher for the roads demo.
# Prompts the user to choose an audio output routing, then runs app.py
# inside the local venv (./env). Pass any extra args after the menu —
# they're forwarded to app.py (e.g. ./run.sh --time 18).
set -euo pipefail

cd "$(dirname "$0")"

PY="./env/bin/python"
if [[ ! -x "$PY" ]]; then
    echo "venv not found at $PY"
    echo "create it first:  python3.12 -m venv env && $PY -m pip install -r requirements.txt"
    exit 1
fi

echo "Audio output:"
echo "  1) both          (system default AND BlackHole 2ch)   [default]"
echo "  2) default       (system default output only)"
echo "  3) blackhole     (BlackHole 2ch only — silent on speakers)"
read -rp "choose [1-3, default=1]: " choice
choice="${choice:-1}"

case "$choice" in
    1) MODE="both" ;;
    2) MODE="default" ;;
    3) MODE="blackhole" ;;
    *) echo "invalid choice: $choice"; exit 2 ;;
esac

exec "$PY" app.py --audio-output "$MODE" "$@"
