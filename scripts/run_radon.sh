#!/bin/bash
set -e

# Compute CC and fail on CC > 10 for worker handlers or > 5 for tool wrappers
radon cc app -s -j > cc.json

# Fail if handlers too complex
python3 - << 'EOF'
import json, sys
data = json.load(open("cc.json"))

fail = False

for filepath, functions in data.items():
    for fn in functions:
        name = fn["name"]
        cc = fn["complexity"]
        if "worker" in filepath and cc > 10:
            print(f"FAIL: {filepath}:{name} has CC {cc} > 10")
            fail = True
        if "tools" in filepath and cc > 5:
            print(f"FAIL: {filepath}:{name} has CC {cc} > 5")
            fail = True

if fail:
    sys.exit(1)
EOF
