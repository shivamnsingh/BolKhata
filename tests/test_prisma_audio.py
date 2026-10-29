"""Real audio conversion and the documented 30 s limit (no network, no API key)."""
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from bolkhata import prisma_client as pc

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def make_wav(path: Path, seconds: float, rate: int = 8000) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


def make_webm(path: Path, seconds: float) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=300:duration={seconds}",
         "-c:a", "libopus", str(path)], check=True)
    return path


def test_wav_over_documented_limit_is_rejected(tmp_path):
    with pytest.raises(pc.PrismaError, match="30 seconds"):
        pc.prepare_audio_for_prisma(make_wav(tmp_path / "long.wav", 31), "audio/wav")


def test_wav_under_limit_is_accepted(tmp_path):
    path, mime = pc.prepare_audio_for_prisma(make_wav(tmp_path / "ok.wav", 29), "audio/wav")
    assert mime == "audio/wav" and path.name == "ok.wav"


def test_wav_duration_rejects_non_wav(tmp_path):
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"RIFFnotreally")
    with pytest.raises(pc.PrismaError, match="valid WAV"):
        pc.wav_duration_seconds(bad)


@needs_ffmpeg
def test_webm_is_really_converted_to_16k_mono_pcm_wav(tmp_path):
    """Not a MIME rename: the output must decode as 16 kHz mono 16-bit PCM of the same length."""
    webm = make_webm(tmp_path / "speech.webm", 2)
    converted, mime = pc.prepare_audio_for_prisma(webm, "audio/webm;codecs=opus")
    try:
        assert mime == "audio/wav" and converted.suffix == ".wav" and converted != webm
        with wave.open(str(converted), "rb") as handle:
            assert (handle.getframerate(), handle.getnchannels(), handle.getsampwidth()) == (16000, 1, 2)
            assert 1.8 <= handle.getnframes() / 16000 <= 2.3
        assert converted.read_bytes()[:4] == b"RIFF"
    finally:
        converted.unlink(missing_ok=True)


@needs_ffmpeg
def test_converted_audio_over_limit_is_rejected_and_temp_file_removed(tmp_path, monkeypatch):
    created = []
    real = pc.convert_audio_to_wav

    def spy(path):
        out = real(path)
        created.append(out)
        return out

    monkeypatch.setattr(pc, "convert_audio_to_wav", spy)
    with pytest.raises(pc.PrismaError, match="at most 30"):
        pc.prepare_audio_for_prisma(make_webm(tmp_path / "long.webm", 33), "audio/webm")
    assert created and not created[0].exists()


@needs_ffmpeg
def test_garbage_webm_gives_user_facing_error(tmp_path):
    bad = tmp_path / "bad.webm"
    bad.write_bytes(b"\x1aE\xdf\xa3 this is not a real webm stream" * 10)
    with pytest.raises(pc.PrismaError, match="could not decode"):
        pc.prepare_audio_for_prisma(bad, "audio/webm")


def test_conversion_timeout_becomes_prisma_error(tmp_path, monkeypatch):
    src = tmp_path / "x.webm"
    src.write_bytes(b"data")
    monkeypatch.setattr(pc.shutil, "which", lambda _n: "/usr/bin/ffmpeg")

    def hang(*_a, **_k):
        raise subprocess.TimeoutExpired("ffmpeg", 30)

    monkeypatch.setattr(pc.subprocess, "run", hang)
    with pytest.raises(pc.PrismaError, match="timed out"):
        pc.convert_audio_to_wav(src)


def test_transcribe_deletes_its_converted_temp_copy(tmp_path, monkeypatch):
    src = tmp_path / "x.webm"
    src.write_bytes(b"data")
    converted = tmp_path / "converted.wav"
    converted.write_bytes(b"RIFF....")
    monkeypatch.setattr(pc, "prepare_audio_for_prisma", lambda *_a, **_k: (converted, "audio/wav"))

    class Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"success": True, "transcript": "ok"}

    monkeypatch.setattr(pc.requests, "post", lambda *_a, **_k: Resp())
    assert pc.transcribe(src, api_key="not-printed", base_url="https://example.test")["transcript"] == "ok"
    assert not converted.exists()
