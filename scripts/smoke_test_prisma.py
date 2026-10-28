"""
BolKhata - Step 0: one small, careful test request to Gnani Prisma (speech-to-text).

WHY THIS EXISTS
    Before building anything, we look at the REAL response from the API with
    one short clip. Nothing else in the project should be written until this
    works on your machine.

HOW TO RUN (Windows, from the project folder, in the VS Code terminal)
    py -m pip install requests
    copy .env.example .env          (then put your real key inside .env)
    py scripts\\smoke_test_prisma.py recordings\\test1.m4a --lang hi-IN,en-IN

    Compare output styles on the same clip (uses 3 API calls):
    py scripts\\smoke_test_prisma.py recordings\\test1.m4a --compare

    Try customer-name hints (Prisma v3.0 "keyterms"):
    py scripts\\smoke_test_prisma.py recordings\\test1.m4a --keyterms "Ramesh,Meena,रमेश,मीना"

Every call is appended to smoke_results\\results.jsonl so you never need to
spend credits twice on the same clip. The API key is never printed or saved.

Endpoint, header and field names come from the official docs
(https://docs.gnani.ai/api/STT/speech-to-text and /api/STT/models/prisma-v3).
Anything the docs do not say is printed as-is so we can inspect it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

import requests

DEFAULT_BASE_URL = "https://api.vachana.ai"
ENDPOINT = "/stt/v3"
API_KEY_HEADER = "X-API-Key-ID"

ALLOWED_EXT = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".m4a": "audio/mp4",
}
# The docs disagree (60 s on one page, 30 s on the Prisma v3.0 page).
# We stay on the safe side and keep test clips short.
MAX_SECONDS = 30
FORMATS = ("verbatim", "transcribe", "codemix", "transliterate")

HTTP_HINTS = {
    400: "Bad request: check the audio format/length and the field values.",
    401: "Unauthorized: the API key is missing or invalid.",
    403: "Forbidden: check the API key, the account status, or whether credits ran out.",
    422: "Missing/invalid field (for example language_code).",
    429: "Rate limit: wait a little and retry.",
    500: "Server error: transient, retry with backoff.",
    503: "Service unavailable: retry later.",
    504: "Gateway timeout: retry later.",
}


def load_api_key() -> str | None:
    """Read GNANI_API_KEY from the environment, else from a local .env file."""
    key = os.environ.get("GNANI_API_KEY")
    if key and key.strip():
        return key.strip()
    env_file = Path(".env")
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("GNANI_API_KEY="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value and value != "your_key_here":
                    return value
    return None


def check_audio(path: Path) -> str:
    """Basic validation before spending credits. Returns the MIME type."""
    if not path.is_file():
        sys.exit(f"Audio file not found: {path}")
    ext = path.suffix.lower()
    if ext not in ALLOWED_EXT:
        sys.exit(
            f"Unsupported file type '{ext}'. Prisma accepts: "
            f"{', '.join(sorted(ALLOWED_EXT))}. (Browser .webm is NOT in the list.)"
        )
    if path.stat().st_size == 0:
        sys.exit("The audio file is empty.")
    if ext == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                seconds = w.getnframes() / float(w.getframerate())
            print(f"Audio: {path.name} | WAV | {seconds:.1f} s")
            if seconds > MAX_SECONDS:
                sys.exit(f"Clip is {seconds:.0f}s; keep test clips under {MAX_SECONDS}s.")
        except wave.Error as exc:
            sys.exit(f"Could not read WAV header: {exc}")
    else:
        print(f"Audio: {path.name} | {ext[1:].upper()} | duration not checked "
              f"(keep it under {MAX_SECONDS}s)")
    return ALLOWED_EXT[ext]


def call_prisma(base_url: str, api_key: str, audio: Path, mime: str,
                fields: dict, timeout: float) -> dict:
    """One POST. Always returns a record dict; never raises for network errors."""
    url = base_url.rstrip("/") + ENDPOINT
    started = time.perf_counter()
    record = {
        "time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "audio": audio.name,
        "request_fields": fields,          # never includes the key
        "http_status": None,
        "elapsed_s": None,
        "response": None,
        "client_error": None,
    }
    try:
        with audio.open("rb") as f:
            resp = requests.post(
                url,
                headers={API_KEY_HEADER: api_key},
                files={"audio_file": (audio.name, f, mime)},
                data=fields,
                timeout=timeout,
            )
        record["http_status"] = resp.status_code
        try:
            record["response"] = resp.json()
        except ValueError:
            record["response"] = {"non_json_body": resp.text[:500]}
    except requests.Timeout:
        record["client_error"] = f"Timed out after {timeout:.0f}s. Nothing was confirmed by the server."
    except requests.ConnectionError:
        record["client_error"] = "Could not connect. Check your internet connection / firewall."
    except requests.RequestException as exc:
        record["client_error"] = f"Request failed: {type(exc).__name__}"
    record["elapsed_s"] = round(time.perf_counter() - started, 3)
    return record


def show(record: dict) -> bool:
    """Print a human-readable summary. Returns True when the call succeeded."""
    fmt = record["request_fields"].get("format", "(default)")
    print(f"\n--- format={fmt} | HTTP {record['http_status']} | {record['elapsed_s']}s ---")
    if record["client_error"]:
        print("ERROR:", record["client_error"])
        return False
    body = record["response"] or {}
    if body.get("success"):
        print("transcript            :", body.get("transcript"))
        print("detected_language_code:", body.get("detected_language_code"))
        print("model                 :", body.get("model"))
        print("request_id            :", body.get("request_id"))
        print("server latency (s)    :", body.get("processing_time"), "/", body.get("end_to_end_latency"))
        print("all response fields   :", sorted(body.keys()))
        return True
    err = body.get("error") or {}
    print("ERROR type   :", err.get("type", "(none)"))
    print("ERROR message:", err.get("message", body))
    hint = HTTP_HINTS.get(record["http_status"])
    if hint:
        print("Hint         :", hint)
    return False


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):          # Hindi/Marathi on Windows consoles
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    p = argparse.ArgumentParser(description="One careful test request to Gnani Prisma STT.")
    p.add_argument("audio", type=Path, help="Short audio clip (wav/mp3/ogg/flac/aac/m4a).")
    p.add_argument("--lang", default="hi-IN,en-IN",
                   help="language_code: one code, up to 4 comma-separated codes, or indic-auto. "
                        "Default: hi-IN,en-IN. Try mr-IN for Marathi.")
    p.add_argument("--model", default="prisma-v3.0",
                   help="prisma-v3.0 (default) or 'default' to omit the field (docs say that means v2.5).")
    p.add_argument("--format", default="transcribe", choices=FORMATS)
    p.add_argument("--compare", action="store_true",
                   help="Run transcribe, verbatim and codemix on the same clip (3 API calls).")
    p.add_argument("--keyterms", default="",
                   help="Comma-separated names/words to bias recognition (prisma-v3.0 only).")
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--base-url", default=DEFAULT_BASE_URL, help=argparse.SUPPRESS)  # for tests
    p.add_argument("--out", type=Path, default=Path("smoke_results"))
    args = p.parse_args()

    api_key = load_api_key()
    if not api_key:
        sys.exit("No API key found. Put GNANI_API_KEY=... in a .env file next to this project "
                 "(copy .env.example) or set the GNANI_API_KEY environment variable.")

    mime = check_audio(args.audio)

    formats = ["transcribe", "verbatim", "codemix"] if args.compare else [args.format]
    keyterms = [k.strip() for k in args.keyterms.split(",") if k.strip()]
    if keyterms and args.model != "prisma-v3.0":
        sys.exit("keyterms are a Prisma v3.0 feature. Use --model prisma-v3.0.")
    print(f"Plan: {len(formats)} API call(s) to {args.base_url}{ENDPOINT}")

    args.out.mkdir(parents=True, exist_ok=True)
    log_path = args.out / "results.jsonl"
    all_ok = True
    for fmt in formats:
        fields = {"language_code": args.lang, "format": fmt}
        if args.model != "default":
            fields["model"] = args.model
        if keyterms:
            fields["keyterms"] = json.dumps(keyterms, ensure_ascii=False)
        record = call_prisma(args.base_url, api_key, args.audio, mime, fields, args.timeout)
        with log_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(record, ensure_ascii=False) + "\n")
        all_ok &= show(record)

    print(f"\nSaved to {log_path}. Now check your credit balance on https://app.gnani.ai/voice "
          f"and note how many credits {len(formats)} call(s) used.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
