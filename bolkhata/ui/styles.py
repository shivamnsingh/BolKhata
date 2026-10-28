from __future__ import annotations

import streamlit as st


def apply() -> None:
    st.markdown(
        """<style>
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
    st.markdown(
        f'<div class="metric-card"><div class="metric-label">{label}</div>'
        f'<div class="metric-value">{value}</div></div>',
        unsafe_allow_html=True,
    )
