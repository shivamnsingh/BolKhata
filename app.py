from __future__ import annotations

import streamlit as st

from bolkhata.config import DATABASE_PATH
from bolkhata.database import connection_scope
from bolkhata.ui import customers, dashboard, styles, transactions, voice_entry

st.set_page_config(page_title="BolKhata Trust", page_icon="₹", layout="wide")
styles.apply()

with connection_scope(DATABASE_PATH) as database:
    page = st.sidebar.radio("Navigate", ["Dashboard", "Voice Entry", "Customers", "Transactions"])
    if page == "Dashboard":
        dashboard.render(database)
    elif page == "Voice Entry":
        voice_entry.render(database)
    elif page == "Customers":
        customers.render(database)
    else:
        transactions.render(database)
