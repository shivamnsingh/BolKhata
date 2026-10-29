from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import streamlit as st

from .. import voice_workflow as workflow
from ..ledger import LedgerError
from ..prisma_client import PrismaError, transcribe
from ..transaction_parser import ParseError, parse_transaction
from ..voice_component import continuous_voice


def _show_flash() -> None:
    flash = st.session_state.pop(workflow.FLASH, None)
    if flash:
        {"success": st.success, "error": st.error}.get(flash[0], st.info)(flash[1])


def render(db) -> None:
    st.title("Voice entry")
    st.write("Start once, speak naturally, and review each completed utterance before saving.")
    st.subheader("Continuous listening")
    # The ack argument is read from session state *before* the component renders. Every code path that
    # changes it (confirm / reject / error) is followed by st.rerun() so the browser receives it.
    event = continuous_voice(
        ack_event_id=st.session_state.get(workflow.ACK_EVENT),
        key="bolkhata-continuous-voice",
    )
    if event and event["event_id"] != st.session_state.get(workflow.LAST_EVENT):
        with st.spinner("Processing utterance..."):
            workflow.handle_event(st.session_state, event)
        st.rerun()
    _show_flash()
    st.caption("The browser detects silence, sends only completed utterances, and keeps listening after each confirmation. Keep this browser tab open.")
    st.subheader("Fallback: upload an audio file")
    audio = st.file_uploader("Audio recording", type=["wav", "mp3", "ogg", "flac", "aac", "m4a", "webm"])
    language = st.text_input("Language code", workflow.DEFAULT_LANGUAGE, help="Examples: hi-IN,en-IN or mr-IN", key=workflow.LANGUAGE)
    if audio and st.button("Transcribe uploaded file with Gnani Prisma", type="primary"):
        suffix = Path(getattr(audio, "name", "recording.wav")).suffix.lower() or ".wav"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(audio.getbuffer())
            temporary_path = tmp.name
        try:
            with st.spinner("Transcribing..."):
                result = transcribe(temporary_path, language_code=language)
            workflow.start_upload(st.session_state, result["transcript"], uuid.uuid4().hex)
        except PrismaError as exc:
            st.error(str(exc))
        finally:
            Path(temporary_path).unlink(missing_ok=True)
    current = workflow.pending(st.session_state)
    if current is None:
        status = st.session_state.get(workflow.STATUS)
        if status:
            st.caption(f"Status: {status}")
        return
    pending_id, transcript = current
    st.info(f"Status: {st.session_state.get(workflow.STATUS, 'Awaiting confirmation')}")
    st.info(f"Transcript: {transcript}")
    try:
        parsed = parse_transaction(transcript)
    except ParseError as exc:
        st.warning(f"Please edit the details manually: {exc}")
        parsed = None
    # Widget keys carry the event id so each utterance gets a fresh form and never inherits edits.
    with st.form(f"confirm_transaction_{pending_id}"):
        customer_name = st.text_input("Customer", parsed.customer_name if parsed else "", key=f"voice_customer_{pending_id}")
        amount = st.number_input("Amount (₹)", min_value=0.01, value=float(parsed.amount) if parsed else 1.0, step=1.0, key=f"voice_amount_{pending_id}")
        kind = st.selectbox("Type", ["credit", "repayment"], index=0 if not parsed or parsed.transaction_type == "credit" else 1, key=f"voice_kind_{pending_id}")
        description = st.text_input("Description", parsed.description if parsed else "", key=f"voice_description_{pending_id}")
        confirm_col, reject_col = st.columns(2)
        with confirm_col:
            confirmed = st.form_submit_button("Confirm and save", type="primary")
        with reject_col:
            rejected = st.form_submit_button("Reject and continue")
    if rejected:
        workflow.reject_pending(st.session_state)
        st.rerun()
    if confirmed:
        try:
            workflow.confirm_pending(st.session_state, db, customer_name, amount, kind, description)
        except LedgerError as exc:
            st.error(str(exc))
        else:
            st.rerun()  # delivers the acknowledgement to the browser so the next utterance is released
