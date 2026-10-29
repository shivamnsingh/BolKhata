"""Optional real-browser tests (headless Chromium + fake microphone, mocked Gnani).

Skipped unless BOLKHATA_E2E=1 and Playwright is installed:
    pip install playwright ; python -m playwright install chromium
    $env:BOLKHATA_E2E = "1" ; python -m pytest tests/e2e -q
"""
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("playwright")
pytestmark = pytest.mark.skipif(os.environ.get("BOLKHATA_E2E") != "1", reason="set BOLKHATA_E2E=1 to run browser tests")
sys.path.insert(0, str(Path(__file__).parent))
from harness import run_scenario  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def test_three_consecutive_utterances_confirm_and_save_once_each():
    result = run_scenario(ROOT, "basic")
    assert all(step.endswith("True") for step in result["steps"]), result["steps"]
    assert [r[0] for r in result["rows"]] == ["Ramesh", "Suresh", "Meena"]
    assert len({r[3] for r in result["rows"]}) == 3  # distinct stable idempotency keys
    assert all('"riff": true' in line for line in result["stt_log"].splitlines())


def test_slow_review_reject_stop_and_restart():
    result = run_scenario(ROOT, "full")
    assert all(step.endswith("True") for step in result["steps"]), result["steps"]
    assert [(r[0], r[1]) for r in result["rows"]] == [("Ramesh", 7000), ("Meena", 4000), ("Suresh", 2500)]
    assert result["status_after_stop"] == "Stopped"
    assert result["tracks_after_stop"] and set(result["tracks_after_stop"]) == {"ended"}  # mic released
