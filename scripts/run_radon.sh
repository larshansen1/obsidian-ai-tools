#!/bin/bash
set -e

# Compute CC and fail on CC > 10 for worker handlers or > 5 for tool wrappers
uv run python3 -m radon cc src -s -j > cc.json

# Fail if handlers too complex
python3 - << 'EOF'
import json, sys
from pathlib import Path
data = json.load(open("cc.json"))

fail = False

for filepath, functions in data.items():
    path_parts = Path(filepath).parts
    for fn in functions:
        name = fn["name"]
        cc = fn["complexity"]
        if "worker" in path_parts and cc > 10:
            print(f"FAIL: {filepath}:{name} has CC {cc} > 10")
            fail = True
        if "tools" in path_parts and cc > 5:
            print(f"FAIL: {filepath}:{name} has CC {cc} > 5")
            fail = True

if fail:
    sys.exit(1)
EOF
