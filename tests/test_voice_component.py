import base64
import json

from bolkhata.voice_component import serialize_event_for_test


def test_voice_event_protocol_preserves_event_id_and_audio():
    payload = json.loads(serialize_event_for_test("event-1", b"audio", "audio/webm"))
    assert payload["event_id"] == "event-1"
    assert base64.b64decode(payload["audio_base64"]) == b"audio"
    assert payload["mime_type"] == "audio/webm"


def test_dist_bundle_exists_for_streamlit_component():
    from pathlib import Path

    assert (Path(__file__).parents[1] / "voice_component_frontend" / "dist" / "index.html").is_file()


def test_voice_source_defines_emitter_at_module_scope_and_limits_queue():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "voice_component_frontend" / "src" / "main.js").read_text()
    assert source.count("async function emitBlob") == 1
    assert source.index("async function emitBlob") < source.index("function startListening")
    assert "queuedBlobs.length < 3" in source
    assert "if (!root.querySelector(\"#start\"))" in source
