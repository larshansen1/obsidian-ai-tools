#!/bin/bash
set -e

# Prefer the project venv, then fall back to another Python on PATH with the
# required test modules installed.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/../.venv/bin/python"

# Tests run in parallel across CPU cores (pytest-xdist). PYTEST_WORKERS=0 runs
# them one at a time, which is easier to read when debugging a single failure.
WORKERS="${PYTEST_WORKERS:-auto}"

find_test_python() {
    for python in "$VENV_PYTHON" $(type -a -p python3 2>/dev/null); do
        if [ ! -x "$python" ]; then
            continue
        fi
        if [ "${COVERAGE:-0}" = "1" ]; then
            "$python" -c "import coverage, pytest, pytest_cov, xdist" 2>/dev/null && printf "%s" "$python" && return
        else
            "$python" -c "import pytest, xdist" 2>/dev/null && printf "%s" "$python" && return
        fi
    done
}

PYTHON="$(find_test_python || true)"
if [ -z "$PYTHON" ]; then
    echo "No Python interpreter with pytest and pytest-xdist${COVERAGE:+ and coverage} installed was found." >&2
    exit 1
fi

if [ "${COVERAGE:-0}" = "1" ]; then
    # pytest-cov (not `coverage run`) so the worker processes are measured too.
    # The report step enforces fail_under from pyproject.toml.
    PYTHONPATH=. "$PYTHON" -m pytest --disable-warnings -n "$WORKERS" --cov --cov-report= "$@"
    "$PYTHON" -m coverage report
else
    PYTHONPATH=. "$PYTHON" -m pytest --disable-warnings -n "$WORKERS" "$@"
fi
