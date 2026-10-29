from __future__ import annotations

import base64
import binascii
import json
import logging
from pathlib import Path
from typing import Any

import streamlit.components.v1 as components

logger = logging.getLogger(__name__)

# 30 s of 16 kHz mono 16-bit WAV is ~960 KB; allow generous headroom, but bound memory.
MAX_ENCODED_AUDIO_CHARS = 6_000_000

_component = components.declare_component(
    "bolkhata_continuous_voice",
    path=str(Path(__file__).resolve().parent.parent / "voice_component_frontend" / "dist"),
)


def parse_component_value(value: Any) -> dict[str, Any] | None:
    """Validate one raw value from the browser component.

    Returns None when the value carries no usable ``event_id`` (nothing to acknowledge).
    A malformed payload that does have an ``event_id`` is returned with an ``error`` key so the
    caller can acknowledge it; silently dropping it would leave the browser's queue waiting forever.
    """
    if not isinstance(value, dict):
        return None
    event_id = value.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        return None
    mime_type = str(value.get("mime_type") or "audio/wav")
    try:
        duration_ms = int(value.get("duration_ms") or 0)
    except (TypeError, ValueError):
        duration_ms = 0
    event: dict[str, Any] = {"event_id": event_id, "audio_bytes": b"", "mime_type": mime_type, "duration_ms": duration_ms}
    encoded = value.get("audio_base64")
    if not isinstance(encoded, str) or not encoded:
        event["error"] = "The browser sent an empty audio segment."
    elif len(encoded) > MAX_ENCODED_AUDIO_CHARS:
        event["error"] = "The audio segment was too large. Speak in shorter utterances."
    else:
        try:
            audio = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            event["error"] = "The browser sent corrupted audio."
        else:
            if audio:
                event["audio_bytes"] = audio
            else:
                event["error"] = "The browser sent an empty audio segment."
    if "error" in event:
        logger.warning("Voice event rejected: event_id=%s reason=%s", event_id, event["error"])
    else:
        logger.info(
            "Voice event received: event_id=%s mime=%s size_bytes=%s duration_ms=%s",
            event_id, mime_type, len(event["audio_bytes"]), duration_ms,
        )
    return event


def continuous_voice(*, ack_event_id: str | None = None, key: str = "continuous-voice") -> dict[str, Any] | None:
    """Render the persistent microphone component and return the latest completed segment (if any)."""
    return parse_component_value(_component(ack_event_id=ack_event_id, key=key, default=None))


def serialize_event_for_test(event_id: str, audio: bytes, mime_type: str = "audio/webm") -> str:
    """Small deterministic helper used by protocol tests; not used by the UI."""
    return json.dumps({
        "event_id": event_id,
        "audio_base64": base64.b64encode(audio).decode("ascii"),
        "mime_type": mime_type,
    })
