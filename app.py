from __future__ import annotations

import tempfile
from decimal import Decimal
from pathlib import Path

import streamlit as st

from bolkhata.config import DATABASE_PATH
from bolkhata.database import connection_scope
from bolkhata.ledger import (
    LedgerError,
    add_transaction,
    balance_paise,
    create_customer,
    dashboard_totals,
    format_paise,
    get_or_create_customer,
    list_customers,
    list_transactions,
    reverse_transaction,
)
from bolkhata.prisma_client import PrismaError, transcribe
from bolkhata.transaction_parser import ParseError, parse_transaction
from bolkhata.voice_component import continuous_voice
st.set_page_config(page_title="BolKhata Trust", page_icon="₹", layout="wide")


DB_PATH = DATABASE_PATH


def style() -> None:
    st.markdown(
        """<style>
        .stApp { background: #f7f5ef; color: #173d36; }
        [data-testid="stSidebar"] { background: #123b35; }
        [data-testid="stSidebar"] * { color: #f7f5ef !important; }
        h1, h2, h3 { color: #123b35; }
        .metric-card { background: white; border: 1px solid #e1e4dc; border-radius: 14px; padding: 18px; }
        .metric-label { color: #68756f; font-size: .85rem; }
        .metric-value { color: #123b35; font-size: 1.7rem; font-weight: 700; }
        .trust-note { background: #e7f2eb; border-left: 4px solid #27815b; padding: 12px; border-radius: 5px; }
        </style>""",
        unsafe_allow_html=True,
    )


def metric(label: str, value: str) -> None:
    st.markdown(f'<div class="metric-card"><div class="metric-label">{label}</div><div class="metric-value">{value}</div></div>', unsafe_allow_html=True)


def dashboard() -> None:
    st.title("BolKhata Trust")
    st.caption("Voice makes recording fast. Confirmation makes it safer.")
    with connection_scope(DB_PATH) as db:
        totals = dashboard_totals(db)
        rows = list_transactions(db)[:8]
    outstanding = totals["credits"] - totals["repayments"]
    cols = st.columns(3)
    with cols[0]:
        metric("Total outstanding credit", format_paise(outstanding))
    with cols[1]:
        metric("Repayments received", format_paise(totals["repayments"]))
    with cols[2]:
        metric("Customers", str(totals["customers"]))
    st.markdown('<div class="trust-note">Positive balance means the customer owes the shop. Every saved entry remains in the ledger and corrections create a visible reversal record.</div>', unsafe_allow_html=True)
    st.subheader("Recent transactions")
    if not rows:
        st.info("No transactions yet. Open Voice Entry or add a customer to get started.")
    for row in rows:
        sign = "+" if row.transaction_type == "credit" else "-"
        st.write(f"**{row.customer_name}** · {row.transaction_type.title()} · {sign}{format_paise(row.amount_paise)} · {row.created_at}")


def voice_entry() -> None:
    st.title("Voice entry")
    st.write("Start once, speak naturally, and review each completed utterance before saving.")
    st.subheader("Continuous listening")
    if "voice_ack_event_id" not in st.session_state:
        st.session_state["voice_ack_event_id"] = None
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
    upload_requested = bool(audio and st.button("Transcribe uploaded file with Gnani Prisma", type="primary"))
    selected_audio = audio if upload_requested else None
    if selected_audio:
        suffix = Path(getattr(selected_audio, "name", "recording.wav")).suffix.lower() or ".wav"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(selected_audio.getbuffer())
            temporary_path = tmp.name
        try:
            with st.spinner("Transcribing..."):
                result = transcribe(temporary_path, language_code=language)
            st.session_state["voice_transcript"] = result["transcript"]
            st.session_state["voice_pending_event_id"] = None
            st.session_state.pop("voice_parse_error", None)
        except PrismaError as exc:
            st.error(str(exc))
        finally:
            Path(temporary_path).unlink(missing_ok=True)
    transcript = st.session_state.get("voice_transcript", "")
    if not transcript:
        return
    status = st.session_state.get("voice_status", "Awaiting confirmation")
    st.info(f"Status: {status}")
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
        st.session_state["voice_pending_event_id"] = None
        st.session_state["voice_ack_event_id"] = st.session_state.get("voice_last_event_id")
        st.session_state["voice_status"] = "Rejected — listening continues"
        st.rerun()
    if confirmed:
        try:
            with connection_scope(DB_PATH) as db:
                customer = get_or_create_customer(db, customer_name)
                add_transaction(db, customer.id, Decimal(str(amount)), kind, description, transcript)
                current_balance = balance_paise(db, customer.id)
            st.success(f"Saved. {customer.name}'s balance is now {format_paise(current_balance)}.")
            st.session_state.pop("voice_transcript", None)
            st.session_state["voice_status"] = "Saved — listening continues"
            st.session_state["voice_pending_event_id"] = None
            st.session_state["voice_ack_event_id"] = st.session_state.get("voice_last_event_id")
        except LedgerError as exc:
            st.error(str(exc))


def customers_page() -> None:
    st.title("Customers")
    with st.form("new_customer"):
        name = st.text_input("New customer name")
        phone = st.text_input("Phone (optional)")
        if st.form_submit_button("Add customer"):
            try:
                with connection_scope(DB_PATH) as db:
                    create_customer(db, name, phone)
                st.success("Customer added.")
            except LedgerError as exc:
                st.error(str(exc))
    search = st.text_input("Search customers")
    with connection_scope(DB_PATH) as db:
        customers = list_customers(db, search)
    for customer in customers:
        st.markdown(f"### {customer.name}")
        with connection_scope(DB_PATH) as db:
            current_balance = balance_paise(db, customer.id)
            customer_transactions = list_transactions(db, customer_id=customer.id)
        st.caption(f"Outstanding: {format_paise(current_balance)}")
        with st.expander("View ledger"):
            for row in customer_transactions:
                st.write(f"{row.created_at} · {row.transaction_type.title()} · {format_paise(row.amount_paise)} · {row.description}")


def transactions_page() -> None:
    st.title("Transactions")
    search = st.text_input("Search ledger")
    kind = st.selectbox("Filter", ["all", "credit", "repayment"])
    with connection_scope(DB_PATH) as db:
        rows = list_transactions(db, search=search, transaction_type=None if kind == "all" else kind)
    for row in rows:
        col1, col2, col3 = st.columns([3, 2, 1])
        col1.write(f"**{row.customer_name}** — {row.description or row.transaction_type.title()}")
        col2.write(f"{row.transaction_type.title()} · {format_paise(row.amount_paise)}")
        col3.caption(row.created_at)
        if row.reversed_transaction_id is None and st.button("Reverse", key=f"reverse-{row.id}"):
            st.session_state["reverse_id"] = row.id
    if "reverse_id" in st.session_state:
        with st.form("reverse_form"):
            reason = st.text_input("Why is this being corrected?")
            if st.form_submit_button("Create reversal"):
                try:
                    with connection_scope(DB_PATH) as db:
                        reverse_transaction(db, st.session_state.pop("reverse_id"), reason)
                    st.success("Reversal recorded in the audit trail.")
                    st.rerun()
                except LedgerError as exc:
                    st.error(str(exc))


style()
page = st.sidebar.radio("Navigate", ["Dashboard", "Voice Entry", "Customers", "Transactions"])
if page == "Dashboard":
    dashboard()
elif page == "Voice Entry":
    voice_entry()
elif page == "Customers":
    customers_page()
else:
    transactions_page()
