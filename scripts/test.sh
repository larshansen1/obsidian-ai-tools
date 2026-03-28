#!/bin/bash
set -e

# Use project venv if available, otherwise fall back to system Python
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/../.venv/bin/python"

if [ -x "$VENV_PYTHON" ]; then
    PYTHONPATH=. "$VENV_PYTHON" -m pytest --maxfail=1 --disable-warnings -v
else
    PYTHONPATH=. pytest --maxfail=1 --disable-warnings -v
fi
