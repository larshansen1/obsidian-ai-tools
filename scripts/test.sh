#!/bin/bash
set -e

# Use project venv if available, otherwise fall back to system Python
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/../.venv/bin/python"

if [ -x "$VENV_PYTHON" ] && "$VENV_PYTHON" -c "import pytest" 2>/dev/null; then
    PYTHONPATH=. "$VENV_PYTHON" -m pytest --maxfail=1 --disable-warnings -v
else
    PYTHONPATH=. python3 -m pytest --maxfail=1 --disable-warnings -v
fi
