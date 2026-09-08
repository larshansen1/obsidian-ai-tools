"""Bridge into the Chrome extension's node test suite.

The extension is plain JS (MV3); its capture logic is tested with node:test
(chrome-extension/capture.test.mjs). CI images ship node, but environments
without it must not break the suite, so the bridge skips when node is absent.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
EXTENSION_DIR = Path(__file__).resolve().parents[1] / "chrome-extension"

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def test_extension_capture_tests_pass() -> None:
    """Run the node:test suite for the extension's capture logic."""
    result = subprocess.run(
        [NODE, "--test", "--test-reporter=tap", "capture.test.mjs"],
        capture_output=True,
        text=True,
        cwd=EXTENSION_DIR,
    )
    assert result.returncode == 0, f"node --test failed:\n{result.stdout}\n{result.stderr}"
    assert "# pass" in result.stdout
    assert "# fail 0" in result.stdout
    assert "not ok" not in result.stdout
