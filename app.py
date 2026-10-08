from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Procurement request copilot", page_icon=":material/fact_check:", layout="wide")

page = st.navigation([
    st.Page("app_pages/review.py", title="Review a request", icon=":material/fact_check:", default=True),
    st.Page("app_pages/decisions.py", title="Human decisions log", icon=":material/history:"),
    st.Page("app_pages/evaluation.py", title="Architecture evaluation", icon=":material/query_stats:"),
])
page.run()
