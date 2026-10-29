from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE_URL = "https://api.vachana.ai"
ENDPOINT = "/stt/v3"
API_KEY_HEADER = "X-API-Key-ID"
ALLOWED_EXTENSIONS = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
                      ".flac": "audio/flac", ".aac": "audio/aac", ".m4a": "audio/mp4"}
API_REQUIRES_CONVERSION = {".webm": "audio/webm"}
ALLOWED_MIME_TYPES = set(ALLOWED_EXTENSIONS.values())
# Documented Gnani STT limit: "Maximum duration: 30 seconds" (https://docs.gnani.ai/api/STT/speech-to-text).
MAX_AUDIO_SECONDS = 30.0
CONVERSION_TIMEOUT_SECONDS = 30


class PrismaError(RuntimeError):
    """A safe, user-facing Prisma integration error."""


def normalize_mime_type(mime_type: str | None) -> str:
    if not mime_type:
        return ""
    return mime_type.split(";", 1)[0].strip().lower()


def choose_temp_suffix(mime_type: str | None, fallback: str = ".webm") -> str:
    normalized = normalize_mime_type(mime_type)
    if normalized in ALLOWED_MIME_TYPES:
        for ext, value in ALLOWED_EXTENSIONS.items():
            if value == normalized:
                return ext
    return fallback


def wav_duration_seconds(audio_path: str | Path) -> float:
    """Exact duration of a PCM WAV file. Raises PrismaError if it is not a readable WAV."""
    try:
        with wave.open(str(audio_path), "rb") as handle:
            rate = handle.getframerate()
            if rate <= 0:
                raise PrismaError("The WAV audio has an invalid sample rate.")
            return handle.getnframes() / rate
    except (wave.Error, EOFError) as exc:
        raise PrismaError("The recorded audio is not a valid WAV file.") from exc


def audio_duration_seconds(audio_path: str | Path) -> float | None:
    """Best-effort duration: exact for WAV, via ffprobe for other formats, None when unknown."""
    path = Path(audio_path)
    if path.suffix.lower() == ".wav":
        try:
            return wav_duration_seconds(path)
        except PrismaError:
            return None
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        completed = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, check=False, timeout=CONVERSION_TIMEOUT_SECONDS,
        )
        return float(completed.stdout.strip()) if completed.returncode == 0 and completed.stdout.strip() else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def enforce_duration_limit(audio_path: str | Path) -> None:
    duration = audio_duration_seconds(audio_path)
    if duration is not None and duration > MAX_AUDIO_SECONDS:
        raise PrismaError(
            f"Audio is {duration:.0f} seconds long; Prisma accepts at most {MAX_AUDIO_SECONDS:.0f} seconds. Speak in shorter utterances."
        )


def convert_audio_to_wav(audio_path: str | Path) -> Path:
    """Convert browser-recorded or other unsupported audio to WAV for Prisma STT."""
    source_path = Path(audio_path)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise PrismaError(
            "Audio conversion is unavailable on this machine. Please re-record in WAV, MP3, OGG, FLAC, AAC, or M4A format or upload a compatible file."
        )
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        output_path = Path(tmp.name)
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(source_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(output_path),
    ]
    logger.info("Converting unsupported audio for Prisma: input=%s output=%s", source_path.name, output_path.name)
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=CONVERSION_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired as exc:
        output_path.unlink(missing_ok=True)
        raise PrismaError("Audio conversion timed out.") from exc
    except OSError as exc:
        output_path.unlink(missing_ok=True)
        raise PrismaError("Audio conversion could not be started on this machine.") from exc
    if completed.returncode != 0:
        output_path.unlink(missing_ok=True)
        message = (completed.stderr or completed.stdout or "Audio conversion failed.").strip()
        raise PrismaError(f"Prisma could not decode the recorded audio: {message[:200]}")
    return output_path


def prepare_audio_for_prisma(audio_path: str | Path, mime_type: str | None = None) -> tuple[Path, str]:
    path = Path(audio_path)
    if not path.is_file():
        raise PrismaError("Audio file not found.")
    if path.stat().st_size == 0:
        raise PrismaError("Audio file is empty.")
    suffix = path.suffix.lower()
    mime = normalize_mime_type(mime_type)
    logger.info(
        "Prisma format validation: filename=%s suffix=%s mime=%s size_bytes=%s",
        path.name,
        suffix or "(none)",
        mime or "(unknown)",
        path.stat().st_size,
    )

    if suffix in ALLOWED_EXTENSIONS:
        enforce_duration_limit(path)
        if mime and mime != ALLOWED_EXTENSIONS[suffix]:
            logger.warning("MIME mismatch for Prisma upload: filename=%s suffix=%s incoming_mime=%s expected=%s", path.name, suffix, mime, ALLOWED_EXTENSIONS[suffix])
        return path, ALLOWED_EXTENSIONS[suffix]

    if suffix in API_REQUIRES_CONVERSION or mime in API_REQUIRES_CONVERSION.values():
        converted = convert_audio_to_wav(path)
        logger.info("Converted browser audio to supported Prisma format: source=%s final=%s", path.name, converted.name)
        return _checked_conversion(converted)

    converted = convert_audio_to_wav(path)
    logger.info("Converted unsupported input extension to supported Prisma format: source=%s final=%s", path.name, converted.name)
    return _checked_conversion(converted)


def _checked_conversion(converted: Path) -> tuple[Path, str]:
    try:
        enforce_duration_limit(converted)
    except PrismaError:
        converted.unlink(missing_ok=True)
        raise
    return converted, "audio/wav"


def _sanitize_prisma_error(body: Any) -> str:
    if not isinstance(body, dict):
        return "Prisma returned an unexpected response."
    error = body.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("type")
        if isinstance(message, str) and message.strip():
            return message.strip()
    if isinstance(body.get("message"), str) and body["message"].strip():
        return body["message"].strip()
    return "Prisma rejected the audio request."


def load_api_key() -> str | None:
    key = os.environ.get("GNANI_API_KEY", "").strip()
    if key:
        return key
    env_file = PROJECT_ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("GNANI_API_KEY="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value and value != "your_key_here":
                    return value
    return None


def transcribe(
    audio_path: str | Path,
    language_code: str = "hi-IN,en-IN",
    model: str = "prisma-v3.0",
    format: str = "transcribe",
    timeout: float = 30,
    base_url: str = DEFAULT_BASE_URL,
    api_key: str | None = None,
    mime_type: str | None = None,
) -> dict[str, Any]:
    path = Path(audio_path)
    if not path.is_file():
        raise PrismaError("Audio file not found.")
    if path.stat().st_size == 0:
        raise PrismaError("Audio file is empty.")

    safe_path, mime_type = prepare_audio_for_prisma(path, mime_type=mime_type)
    try:
        return _submit(safe_path, mime_type, language_code, model, format, timeout, base_url, api_key)
    finally:
        if safe_path != path:
            safe_path.unlink(missing_ok=True)  # converted temp copy; never leak it


def _submit(
    safe_path: Path,
    mime_type: str,
    language_code: str,
    model: str,
    format: str,
    timeout: float,
    base_url: str,
    api_key: str | None,
) -> dict[str, Any]:
    logger.info("Submitting audio to Prisma: filename=%s mime=%s size_bytes=%s", safe_path.name, mime_type, safe_path.stat().st_size)
    key = api_key or load_api_key()
    if not key:
        raise PrismaError("GNANI_API_KEY is not configured. Add it to your local .env file.")
    fields = {"language_code": language_code, "model": model, "format": format}
    try:
        with safe_path.open("rb") as audio:
            response = requests.post(
                base_url.rstrip("/") + ENDPOINT,
                headers={API_KEY_HEADER: key},
                files={"audio_file": (safe_path.name, audio, mime_type)},
                data=fields,
                timeout=timeout,
            )
    except requests.Timeout as exc:
        raise PrismaError("Prisma took too long to respond. Try again only when you are ready to use another request.") from exc
    except requests.ConnectionError as exc:
        raise PrismaError("Could not connect to Prisma. Check your internet connection.") from exc
    except requests.RequestException as exc:
        raise PrismaError("The transcription request failed. Please try again later.") from exc
    try:
        body = response.json()
    except ValueError as exc:
        raise PrismaError("Prisma returned an unreadable response.") from exc
    if response.status_code != 200 or not body.get("success"):
        message = _sanitize_prisma_error(body)
        logger.warning(
            "Prisma audio rejection: http_status=%s mime=%s filename=%s message=%s",
            response.status_code,
            mime_type,
            safe_path.name,
            message,
        )
        if response.status_code == 400 and "Unsupported audio format" in message:
            raise PrismaError("Prisma rejected the audio format. Re-record in a supported format like WAV, MP3, OGG, FLAC, AAC, or M4A.")
        raise PrismaError(message or f"Prisma returned HTTP {response.status_code}.")
    transcript = body.get("transcript")
    if not isinstance(transcript, str) or not transcript.strip():
        raise PrismaError("Prisma returned no transcript.")
    return body
