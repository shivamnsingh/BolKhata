from __future__ import annotations

import streamlit as st

from ..ledger import LedgerError, format_paise, list_transactions, reverse_transaction


def render(db) -> None:
    st.title("Transactions")
    search = st.text_input("Search ledger")
    kind = st.selectbox("Filter", ["all", "credit", "repayment"])
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
                    reverse_transaction(db, st.session_state.pop("reverse_id"), reason)
                    st.success("Reversal recorded in the audit trail.")
                    st.rerun()
                except LedgerError as exc:
                    st.error(str(exc))
