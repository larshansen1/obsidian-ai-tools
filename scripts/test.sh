#!/bin/bash
set -e

# Prefer the project venv, then fall back to another Python on PATH with the
# required test modules installed.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/../.venv/bin/python"

find_test_python() {
    for python in "$VENV_PYTHON" $(type -a -p python3 2>/dev/null); do
        if [ ! -x "$python" ]; then
            continue
        fi
        if [ "${COVERAGE:-0}" = "1" ]; then
            "$python" -c "import coverage, pytest" 2>/dev/null && printf "%s" "$python" && return
        else
            "$python" -c "import pytest" 2>/dev/null && printf "%s" "$python" && return
        fi
    done
}

PYTHON="$(find_test_python || true)"
if [ -z "$PYTHON" ]; then
    echo "No Python interpreter with pytest${COVERAGE:+ and coverage} installed was found." >&2
    exit 1
fi

if [ "${COVERAGE:-0}" = "1" ]; then
    PYTHONPATH=. "$PYTHON" -m coverage run -m pytest --disable-warnings "$@"
    "$PYTHON" -m coverage report
else
    PYTHONPATH=. "$PYTHON" -m pytest --disable-warnings "$@"
fi
