"""Runs the Node unit tests for the browser-side segmenter, WAV encoder and ack queue."""
import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).parents[1] / "voice_component_frontend"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_frontend_unit_tests_pass():
    completed = subprocess.run(
        ["node", "--test", "test/frontend.test.js"], cwd=FRONTEND, capture_output=True, text=True, timeout=120
    )
    assert completed.returncode == 0, completed.stdout[-3000:] + completed.stderr[-2000:]
