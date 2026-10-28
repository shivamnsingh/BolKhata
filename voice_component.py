from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import streamlit.components.v1 as components

_component = components.declare_component(
    "bolkhata_continuous_voice",
    path=str(Path(__file__).parent / "voice_component_frontend" / "dist"),
)


def continuous_voice(*, ack_event_id: str | None = None, key: str = "continuous-voice") -> dict[str, Any] | None:
    """Render the persistent microphone component and return one completed segment."""
    value = _component(ack_event_id=ack_event_id, key=key, default=None)
    if not isinstance(value, dict):
        return None
    event_id = value.get("event_id")
    encoded = value.get("audio_base64")
    if not isinstance(event_id, str) or not event_id or not isinstance(encoded, str):
        return None
    try:
        audio = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error):
        return None
    if not audio:
        return None
    return {
        "event_id": event_id,
        "audio_bytes": audio,
        "mime_type": str(value.get("mime_type") or "audio/webm"),
        "duration_ms": int(value.get("duration_ms") or 0),
    }


def serialize_event_for_test(event_id: str, audio: bytes, mime_type: str = "audio/webm") -> str:
    """Small deterministic helper used by protocol tests; not used by the UI."""
    return json.dumps({
        "event_id": event_id,
        "audio_base64": base64.b64encode(audio).decode("ascii"),
        "mime_type": mime_type,
    })
