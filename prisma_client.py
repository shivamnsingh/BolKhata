from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests

DEFAULT_BASE_URL = "https://api.vachana.ai"
ENDPOINT = "/stt/v3"
API_KEY_HEADER = "X-API-Key-ID"
ALLOWED_EXTENSIONS = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
                      ".flac": "audio/flac", ".aac": "audio/aac", ".m4a": "audio/mp4"}


class PrismaError(RuntimeError):
    """A safe, user-facing Prisma integration error."""


def load_api_key() -> str | None:
    key = os.environ.get("GNANI_API_KEY", "").strip()
    if key:
        return key
    env_file = Path(".env")
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
) -> dict[str, Any]:
    path = Path(audio_path)
    if not path.is_file() or path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise PrismaError("Choose a supported audio file: WAV, MP3, OGG, FLAC, AAC, or M4A.")
    key = api_key or load_api_key()
    if not key:
        raise PrismaError("GNANI_API_KEY is not configured. Add it to your local .env file.")
    fields = {"language_code": language_code, "model": model, "format": format}
    try:
        with path.open("rb") as audio:
            response = requests.post(
                base_url.rstrip("/") + ENDPOINT,
                headers={API_KEY_HEADER: key},
                files={"audio_file": (path.name, audio, ALLOWED_EXTENSIONS[path.suffix.lower()])},
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
        error = body.get("error") if isinstance(body, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        raise PrismaError(message or f"Prisma returned HTTP {response.status_code}.")
    transcript = body.get("transcript")
    if not isinstance(transcript, str) or not transcript.strip():
        raise PrismaError("Prisma returned no transcript.")
    return body
