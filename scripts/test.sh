#!/bin/bash
set -e
PYTHONPATH=. pytest --maxfail=1 --disable-warnings -v
