"""Voice-entry workflow, independent of Streamlit so every transition can be unit-tested.

``state`` is any mutable mapping (``st.session_state`` in the app, a plain dict in tests).

Lifecycle of one browser event:

    received -> transcribed (PENDING, awaiting explicit confirmation) -> saved or rejected -> ACK
    received -> failed (error)                                                          -> ACK

The browser holds its queue until it receives the event id back as ``ack_event_id``. An event is
acknowledged only at a terminal outcome, and a save is acknowledged only after the database commit
has succeeded. ``LAST_EVENT`` is set only once an event has been fully handled, so a Streamlit run
interrupted mid-transcription simply re-processes the event instead of losing it.
"""
from __future__ import annotations

import logging
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, MutableMapping

from .ledger import LedgerError, add_transaction, balance_paise, format_paise, get_or_create_customer
from .prisma_client import MAX_AUDIO_SECONDS, PrismaError, normalize_mime_type, transcribe, wav_duration_seconds

logger = logging.getLogger(__name__)

LAST_EVENT = "voice_last_event_id"
PENDING_EVENT = "voice_pending_event_id"
TRANSCRIPT = "voice_transcript"
ACK_EVENT = "voice_ack_event_id"
STATUS = "voice_status"
FLASH = "voice_flash"
LANGUAGE = "voice_language"
DEFAULT_LANGUAGE = "hi-IN,en-IN"
UPLOAD_PREFIX = "upload:"

_SUFFIXES = {
    "audio/wav": ".wav", "audio/wave": ".wav", "audio/x-wav": ".wav",
    "audio/webm": ".webm", "audio/ogg": ".ogg",
}


def idempotency_key(pending_event_id: str) -> str:
    """Stable per-utterance key: the same utterance can never be saved twice."""
    return f"voice:{pending_event_id}"


def pending(state: MutableMapping[str, Any]) -> tuple[str, str] | None:
    event_id, transcript = state.get(PENDING_EVENT), state.get(TRANSCRIPT)
    return (event_id, transcript) if event_id and transcript else None


def _finish(state: MutableMapping[str, Any], event_id: str | None, status: str, flash: tuple[str, str]) -> None:
    """Record a terminal outcome and release the browser (uploads have no browser event to ack)."""
    state.pop(TRANSCRIPT, None)
    state[PENDING_EVENT] = None
    state[STATUS] = status
    state[FLASH] = flash
    if event_id and not event_id.startswith(UPLOAD_PREFIX):
        state[ACK_EVENT] = event_id


def _fail(state: MutableMapping[str, Any], event_id: str, message: str) -> str:
    logger.warning("Voice event failed: event_id=%s message=%s", event_id, message)
    _finish(state, event_id, "Error — listening continues", ("error", message))
    state[LAST_EVENT] = event_id
    return "error"


def handle_event(
    state: MutableMapping[str, Any],
    event: dict[str, Any],
    transcribe_fn: Callable[..., dict[str, Any]] | None = None,
) -> str:
    """Process one browser event. Returns 'duplicate', 'pending' or 'error'. Never raises."""
    event_id = event["event_id"]
    if event_id == state.get(LAST_EVENT):
        return "duplicate"
    if state.get(PENDING_EVENT) and state[PENDING_EVENT] != event_id:
        # Only possible after Stop/Start; the older unconfirmed utterance is superseded, never saved.
        logger.info("Pending utterance %s superseded by %s", state[PENDING_EVENT], event_id)
        state.pop(TRANSCRIPT, None)
        state[PENDING_EVENT] = None
    if event.get("error"):
        return _fail(state, event_id, str(event["error"]))
    mime_type = event.get("mime_type") or "audio/wav"
    suffix = _SUFFIXES.get(normalize_mime_type(mime_type), ".bin")
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(event["audio_bytes"])
            temporary_path = tmp.name
        if suffix == ".wav":
            duration = wav_duration_seconds(temporary_path)  # also proves it is a real WAV
            if duration > MAX_AUDIO_SECONDS:
                raise PrismaError(
                    f"Audio is {duration:.0f} seconds long; the limit is {MAX_AUDIO_SECONDS:.0f} seconds."
                )
        result = (transcribe_fn or transcribe)(  # resolved at call time so tests can patch it
            temporary_path,
            language_code=state.get(LANGUAGE, DEFAULT_LANGUAGE),
            mime_type=mime_type,
        )
        transcript = str(result["transcript"]).strip()
        if not transcript:
            raise PrismaError("Prisma returned no transcript.")
    except PrismaError as exc:
        return _fail(state, event_id, str(exc))
    except Exception:  # noqa: BLE001 - any failure must still release the browser queue
        logger.exception("Unexpected failure while processing voice event %s", event_id)
        return _fail(state, event_id, "Something went wrong while processing that utterance. Please say it again.")
    finally:
        if temporary_path:
            Path(temporary_path).unlink(missing_ok=True)
    state[TRANSCRIPT] = transcript
    state[PENDING_EVENT] = event_id
    state[STATUS] = "Awaiting confirmation"
    state[LAST_EVENT] = event_id
    return "pending"


def start_upload(state: MutableMapping[str, Any], transcript: str, token: str) -> None:
    """Register a transcript from the manual-upload fallback as a pending (unconfirmed) utterance."""
    state[TRANSCRIPT] = transcript
    state[PENDING_EVENT] = f"{UPLOAD_PREFIX}{token}"
    state[STATUS] = "Awaiting confirmation"


def confirm_pending(
    state: MutableMapping[str, Any],
    db,
    customer_name: str,
    amount: Decimal | float | str,
    transaction_type: str,
    description: str,
) -> str:
    """Save the pending utterance after explicit confirmation. Returns the success message.

    Raises LedgerError (nothing acknowledged, nothing saved) if there is nothing pending or the
    ledger rejects the data. The acknowledgement is issued only after the commit has succeeded.
    """
    current = pending(state)
    if current is None:
        raise LedgerError("There is no transcript waiting for confirmation.")
    event_id, transcript = current
    customer = get_or_create_customer(db, customer_name)
    add_transaction(
        db,
        customer.id,
        Decimal(str(amount)),
        transaction_type,
        description,
        transcript,
        idempotency_key=idempotency_key(event_id),
    )  # commits internally; raises before reaching the lines below on failure
    message = f"Saved. {customer.name}'s balance is now {format_paise(balance_paise(db, customer.id))}."
    _finish(state, event_id, "Saved — listening continues", ("success", message))
    return message


def reject_pending(state: MutableMapping[str, Any]) -> None:
    current = pending(state)
    event_id = current[0] if current else state.get(PENDING_EVENT)
    _finish(state, event_id, "Rejected — listening continues", ("info", "Rejected. Nothing was saved."))
