"""Run the Pages Function tests with Node's built-in test runner."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_pages_proxy_node_suite():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js not installed")
    test_file = Path(__file__).with_name("test_pages_proxy.mjs")
    proc = subprocess.run(
        [node, "--test", str(test_file)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
