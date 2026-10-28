from __future__ import annotations

import streamlit as st

from ..ledger import LedgerError, balance_paise, create_customer, format_paise, list_customers, list_transactions


def render(db) -> None:
    st.title("Customers")
    with st.form("new_customer"):
        name = st.text_input("New customer name")
        phone = st.text_input("Phone (optional)")
        if st.form_submit_button("Add customer"):
            try:
                create_customer(db, name, phone)
                st.success("Customer added.")
            except LedgerError as exc:
                st.error(str(exc))
    customers = list_customers(db, st.text_input("Search customers"))
    for customer in customers:
        st.markdown(f"### {customer.name}")
        st.caption(f"Outstanding: {format_paise(balance_paise(db, customer.id))}")
        with st.expander("View ledger"):
            for row in list_transactions(db, customer_id=customer.id):
                st.write(f"{row.created_at} · {row.transaction_type.title()} · {format_paise(row.amount_paise)} · {row.description}")
