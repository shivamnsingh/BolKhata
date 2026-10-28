from __future__ import annotations

import tempfile
from decimal import Decimal
from pathlib import Path

import streamlit as st

from ..ledger import LedgerError, add_transaction, balance_paise, format_paise, get_or_create_customer
from ..prisma_client import PrismaError, transcribe
from ..transaction_parser import ParseError, parse_transaction
from ..voice_component import continuous_voice


def render(db) -> None:
    st.title("Voice entry")
    st.write("Start once, speak naturally, and review each completed utterance before saving.")
    st.subheader("Continuous listening")
    event = continuous_voice(
        ack_event_id=st.session_state.get("voice_ack_event_id"),
        key="bolkhata-continuous-voice",
    )
    if event and event["event_id"] != st.session_state.get("voice_last_event_id"):
        st.session_state["voice_last_event_id"] = event["event_id"]
        suffix = ".webm" if "webm" in event["mime_type"] else ".ogg"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(event["audio_bytes"])
            temporary_path = tmp.name
        try:
            with st.spinner("Processing utterance..."):
                result = transcribe(temporary_path, language_code=st.session_state.get("voice_language", "hi-IN,en-IN"))
            st.session_state["voice_transcript"] = result["transcript"]
            st.session_state["voice_pending_event_id"] = event["event_id"]
            st.session_state["voice_status"] = "Awaiting confirmation"
        except PrismaError as exc:
            st.session_state["voice_status"] = "Error"
            st.session_state["voice_ack_event_id"] = event["event_id"]
            st.error(str(exc))
        finally:
            Path(temporary_path).unlink(missing_ok=True)
        st.rerun()
    st.caption("The browser detects silence, sends only completed utterances, and keeps listening after each confirmation. Keep this browser tab open.")
    st.subheader("Fallback: upload an audio file")
    audio = st.file_uploader("Audio recording", type=["wav", "mp3", "ogg", "flac", "aac", "m4a", "webm"])
    language = st.text_input("Language code", "hi-IN,en-IN", help="Examples: hi-IN,en-IN or mr-IN", key="voice_language")
    if audio and st.button("Transcribe uploaded file with Gnani Prisma", type="primary"):
        suffix = Path(getattr(audio, "name", "recording.wav")).suffix.lower() or ".wav"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(audio.getbuffer())
            temporary_path = tmp.name
        try:
            with st.spinner("Transcribing..."):
                result = transcribe(temporary_path, language_code=language)
            st.session_state["voice_transcript"] = result["transcript"]
            st.session_state["voice_pending_event_id"] = None
        except PrismaError as exc:
            st.error(str(exc))
        finally:
            Path(temporary_path).unlink(missing_ok=True)
    transcript = st.session_state.get("voice_transcript", "")
    if not transcript:
        return
    st.info(f"Status: {st.session_state.get('voice_status', 'Awaiting confirmation')}")
    st.info(f"Transcript: {transcript}")
    try:
        parsed = parse_transaction(transcript)
    except ParseError as exc:
        st.warning(f"Please edit the details manually: {exc}")
        parsed = None
    with st.form("confirm_transaction"):
        customer_name = st.text_input("Customer", parsed.customer_name if parsed else "")
        amount = st.number_input("Amount (₹)", min_value=0.01, value=float(parsed.amount) if parsed else 1.0, step=1.0)
        kind = st.selectbox("Type", ["credit", "repayment"], index=0 if not parsed or parsed.transaction_type == "credit" else 1)
        description = st.text_input("Description", parsed.description if parsed else "")
        confirm_col, reject_col = st.columns(2)
        with confirm_col:
            confirmed = st.form_submit_button("Confirm and save", type="primary")
        with reject_col:
            rejected = st.form_submit_button("Reject and continue")
    if rejected:
        st.session_state.pop("voice_transcript", None)
        st.session_state["voice_ack_event_id"] = st.session_state.get("voice_last_event_id")
        st.session_state["voice_status"] = "Rejected — listening continues"
        st.rerun()
    if confirmed:
        try:
            customer = get_or_create_customer(db, customer_name)
            add_transaction(db, customer.id, Decimal(str(amount)), kind, description, transcript)
            current_balance = balance_paise(db, customer.id)
            st.success(f"Saved. {customer.name}'s balance is now {format_paise(current_balance)}.")
            st.session_state.pop("voice_transcript", None)
            st.session_state["voice_status"] = "Saved — listening continues"
            st.session_state["voice_ack_event_id"] = st.session_state.get("voice_last_event_id")
        except LedgerError as exc:
            st.error(str(exc))
