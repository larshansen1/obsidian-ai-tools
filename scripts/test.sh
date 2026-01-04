#!/bin/bash
set -e
PYTHONPATH=. .venv/bin/pytest --maxfail=1 --disable-warnings -v
