from __future__ import annotations

import streamlit as st

from ..ledger import dashboard_totals, format_paise, list_transactions
from .styles import metric


def render(db) -> None:
    st.title("BolKhata Trust")
    st.caption("Voice makes recording fast. Confirmation makes it safer.")
    totals = dashboard_totals(db)
    rows = list_transactions(db)[:8]
    cols = st.columns(3)
    with cols[0]:
        metric("Total outstanding credit", format_paise(totals["credits"] - totals["repayments"]))
    with cols[1]:
        metric("Repayments received", format_paise(totals["repayments"]))
    with cols[2]:
        metric("Customers", str(totals["customers"]))
    st.markdown(
        '<div class="trust-note">Positive balance means the customer owes the shop. '
        'Every saved entry remains in the ledger and corrections create a visible reversal record.</div>',
        unsafe_allow_html=True,
    )
    st.subheader("Recent transactions")
    if not rows:
        st.info("No transactions yet. Open Voice Entry or add a customer to get started.")
    for row in rows:
        sign = "+" if row.transaction_type == "credit" else "-"
        st.write(f"**{row.customer_name}** · {row.transaction_type.title()} · {sign}{format_paise(row.amount_paise)} · {row.created_at}")
