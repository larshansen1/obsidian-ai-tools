#!/bin/bash
set -e

# CRAP gate: radon complexity x line coverage. Needs the .coverage file from a
# coverage test run (COVERAGE=1 ./scripts/test.sh), so run that first.
PYTHON=".venv/bin/python"
[ -f "$PYTHON" ] || PYTHON="python"

if [ ! -f .coverage ]; then
    echo "No .coverage file. Run COVERAGE=1 ./scripts/test.sh first." >&2
    exit 1
fi

$PYTHON -m radon cc src -s -j > cc.json
$PYTHON -m coverage json -q -o coverage.json
$PYTHON scripts/crap_report.py
