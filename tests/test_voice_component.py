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


def test_dist_is_built_from_current_source_and_references_existing_asset():
    """Streamlit serves dist/, so a stale or missing build silently runs old code."""
    import re
    from pathlib import Path

    frontend = Path(__file__).parents[1] / "voice_component_frontend"
    html = (frontend / "dist" / "index.html").read_text()
    asset = re.search(r'src="\./(assets/[^"]+\.js)"', html)
    assert asset, "dist/index.html does not reference a JS bundle"
    bundle = (frontend / "dist" / asset.group(1)).read_text()
    # Strings that only exist in the current source (the old build had MediaRecorder-based logic).
    for marker in ("bolkhata-capture", "audio session started", "noise discarded"):
        assert marker in bundle, f"dist bundle is stale: missing {marker!r}; run `npm run build`"
    assert "MediaRecorder" not in bundle.replace("MediaRecorder-free", "")
    assert len(list((frontend / "dist" / "assets").glob("*.js"))) == 1, "old hashed bundles left in dist/assets"


def test_parse_component_value_accepts_valid_event():
    from bolkhata.voice_component import parse_component_value

    event = parse_component_value(json.loads(serialize_event_for_test("e1", b"RIFFdata", "audio/wav")))
    assert event["audio_bytes"] == b"RIFFdata" and event["event_id"] == "e1" and "error" not in event


def test_parse_component_value_returns_ackable_error_for_bad_payloads():
    """Regression: a bad payload used to be dropped silently, so the browser never got an ack."""
    from bolkhata.voice_component import parse_component_value

    assert parse_component_value(None) is None
    assert parse_component_value({"audio_base64": "AAAA"}) is None  # no event id: nothing to ack
    for payload in (
        {"event_id": "e", "audio_base64": "***not-base64***"},
        {"event_id": "e", "audio_base64": ""},
        {"event_id": "e"},
        {"event_id": "e", "audio_base64": "A" * 6_000_001},
    ):
        event = parse_component_value(payload)
        assert event and event["event_id"] == "e" and event["error"]
