from pathlib import Path

import pytest

from bolkhata.prisma_client import PrismaError, prepare_audio_for_prisma, transcribe


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def test_invalid_api_response_is_safe(monkeypatch, tmp_path: Path):
    audio = tmp_path / "voice.m4a"
    audio.write_bytes(b"synthetic audio")

    def fake_post(*_args, **_kwargs):
        _ = (_args, _kwargs)
        return FakeResponse(401, {"success": False, "error": {"message": "invalid key"}})

    monkeypatch.setattr("bolkhata.prisma_client.requests.post", fake_post)
    with pytest.raises(PrismaError, match="invalid key"):
        transcribe(audio, api_key="not-printed", base_url="https://example.test")


def test_malformed_success_response_is_rejected(monkeypatch, tmp_path: Path):
    audio = tmp_path / "voice.m4a"
    audio.write_bytes(b"synthetic audio")

    def successful_post(*_args, **_kwargs):
        _ = (_args, _kwargs)
        return FakeResponse(200, {"success": True})

    monkeypatch.setattr("bolkhata.prisma_client.requests.post", successful_post)
    with pytest.raises(PrismaError, match="no transcript"):
        transcribe(audio, api_key="not-printed", base_url="https://example.test")


def test_prepare_audio_for_prisma_accepts_supported_wav(tmp_path: Path):
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"RIFF")
    prepared_path, mime = prepare_audio_for_prisma(audio)
    assert prepared_path == audio
    assert mime == "audio/wav"


def test_prepare_audio_for_prisma_converts_unsupported_webm(tmp_path: Path, monkeypatch):
    audio = tmp_path / "voice.webm"
    audio.write_bytes(b"fake webm")

    converted = tmp_path / "converted.wav"
    converted.write_bytes(b"converted")

    monkeypatch.setattr("bolkhata.prisma_client.convert_audio_to_wav", lambda _path: converted)
    prepared_path, mime = prepare_audio_for_prisma(audio, mime_type="audio/webm")
    assert prepared_path == converted
    assert mime == "audio/wav"


def test_prepare_audio_for_prisma_rejects_empty_audio(tmp_path: Path):
    audio = tmp_path / "empty.webm"
    audio.write_bytes(b"")
    with pytest.raises(PrismaError, match="empty"):
        prepare_audio_for_prisma(audio)


def test_prepare_audio_for_prisma_reports_conversion_failure(tmp_path: Path, monkeypatch):
    audio = tmp_path / "voice.webm"
    audio.write_bytes(b"not valid webm")

    def failed_conversion(_path):
        raise PrismaError("Audio conversion is unavailable")

    monkeypatch.setattr("bolkhata.prisma_client.convert_audio_to_wav", failed_conversion)
    with pytest.raises(PrismaError, match="conversion is unavailable"):
        prepare_audio_for_prisma(audio, mime_type="audio/webm;codecs=opus")


def test_transcribe_passes_browser_mime_type_to_format_validation(monkeypatch, tmp_path: Path):
    audio = tmp_path / "voice.webm"
    audio.write_bytes(b"synthetic audio")
    seen = {}

    def fake_prepare(path, mime_type=None):
        seen["path"] = path
        seen["mime_type"] = mime_type
        return audio, "audio/wav"

    def fake_post(*_args, **_kwargs):
        return FakeResponse(200, {"success": True, "transcript": "hello"})

    monkeypatch.setattr("bolkhata.prisma_client.prepare_audio_for_prisma", fake_prepare)
    monkeypatch.setattr("bolkhata.prisma_client.requests.post", fake_post)
    result = transcribe(
        audio,
        mime_type="audio/webm;codecs=opus",
        api_key="not-printed",
        base_url="https://example.test",
    )

    assert result["transcript"] == "hello"
    assert seen == {"path": audio, "mime_type": "audio/webm;codecs=opus"}


def test_transcribe_handles_unsupported_audio_format_error(monkeypatch, tmp_path: Path):
    audio = tmp_path / "voice.webm"
    audio.write_bytes(b"fake webm")

    monkeypatch.setattr("bolkhata.prisma_client.prepare_audio_for_prisma", lambda *args, **kwargs: (audio, "audio/wav"))

    def fake_post(*_args, **_kwargs):
        return FakeResponse(400, {"success": False, "error": {"message": "Unsupported audio format"}})

    monkeypatch.setattr("bolkhata.prisma_client.requests.post", fake_post)
    with pytest.raises(PrismaError, match="unsupported audio format|supported format"):
        transcribe(audio, api_key="not-printed", base_url="https://example.test")


def test_transcribe_handles_failed_segment_only_once(monkeypatch, tmp_path: Path):
    audio = tmp_path / "voice.webm"
    audio.write_bytes(b"fake webm")

    calls = {"count": 0}

    def fake_post(*_args, **_kwargs):
        calls["count"] += 1
        return FakeResponse(400, {"success": False, "error": {"message": "Unsupported audio format"}})

    monkeypatch.setattr("bolkhata.prisma_client.prepare_audio_for_prisma", lambda *args, **kwargs: (audio, "audio/wav"))
    monkeypatch.setattr("bolkhata.prisma_client.requests.post", fake_post)
    with pytest.raises(PrismaError):
        transcribe(audio, api_key="not-printed", base_url="https://example.test")
    assert calls["count"] == 1
