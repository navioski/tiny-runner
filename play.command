#!/bin/bash
# TINY RUNNER launcher — double-click this file in Finder to play.
# Any arguments are passed through, e.g.  ./play.command --hard
cd "$(dirname "$0")" || exit 1

# Prefer the project venv, then fall back to whatever python3 is on PATH.
PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PY="python3"
  else
    echo "No Python found. Install Python 3 and Pyxel first:"
    echo "  python3 -m venv .venv && .venv/bin/pip install pyxel"
    read -r -p "Press Enter to close..." _
    exit 1
  fi
fi

exec "$PY" tiny_runner.py "$@"
