from pathlib import Path

import pytest

from bolkhata.prisma_client import PrismaError, transcribe


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
