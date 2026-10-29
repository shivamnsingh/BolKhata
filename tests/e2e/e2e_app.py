"""Streamlit entry point used ONLY by the browser E2E test.

It runs the real app.py but replaces the Gnani network call with a deterministic
fake, so no live API call, credit or API key is ever involved.
"""
from __future__ import annotations

import json
import os
import runpy
import sys
import types
import wave
from pathlib import Path

ROOT = Path(os.environ.get("BOLKHATA_PROJECT", Path(__file__).resolve().parents[2])).resolve()
sys.path.insert(0, str(ROOT))

PHRASES = [
    "Ramesh ke khate mein 70 rupaye udhaar likh do.",
    "Suresh ne 100 rupaye de diye.",
    "Meena ke khate mein 40 rupaye udhaar likh do.",
    "Suresh ne 25 rupaye wapas kiye.",
    "Ramesh ke khate mein 10 rupaye udhaar likh do.",
]
state = sys.modules.setdefault("_bolkhata_e2e_state", types.SimpleNamespace(calls=0))


def _audio_info(path: str) -> dict:
    data = Path(path).read_bytes()
    info = {"bytes": len(data), "riff": data[:4] == b"RIFF", "duration_s": None}
    try:
        with wave.open(path, "rb") as handle:
            info["duration_s"] = handle.getnframes() / handle.getframerate()
            info["rate"] = handle.getframerate()
            info["channels"] = handle.getnchannels()
    except Exception:  # noqa: BLE001 - recorded for the test to assert on
        pass
    return info


def fake_transcribe(audio_path, language_code="hi-IN,en-IN", mime_type=None, **_kwargs):
    phrase = PHRASES[state.calls % len(PHRASES)]
    state.calls += 1
    record = {"call": state.calls, "mime_type": mime_type, "phrase": phrase, **_audio_info(str(audio_path))}
    log = os.environ.get("BOLKHATA_E2E_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    return {"success": True, "transcript": phrase}


import bolkhata.voice_workflow as voice_workflow  # noqa: E402

voice_workflow.transcribe = fake_transcribe
runpy.run_path(str(ROOT / "app.py"), run_name="__main__")
