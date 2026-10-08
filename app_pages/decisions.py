from __future__ import annotations

import pandas as pd
import streamlit as st

from src.review_log import HUMAN_ACTIONS, LOG_PATH, load

st.title("Human decisions log")
st.caption("Every action a reviewer recorded on a copilot recommendation. The copilot itself never approves, "
           f"purchases or changes budgets. Stored locally in `{LOG_PATH.relative_to(LOG_PATH.parents[1])}`.")

rows = load()
if not rows:
    st.info("No decisions recorded yet. Analyze a request and use the reviewer decision panel.", icon=":material/info:")
    st.stop()

df = pd.DataFrame(rows).iloc[::-1]
df["action"] = df["action"].map(HUMAN_ACTIONS).fillna(df["action"])
with st.container(horizontal=True):
    st.metric("Decisions", len(df), border=True)
    st.metric("Requests reviewed", df["request_id"].nunique(), border=True)
    st.metric("Returned / escalated / declined", int((~df["action"].eq(HUMAN_ACTIONS["send_for_approvals"])).sum()), border=True)
st.dataframe(df, hide_index=True, column_config={
    "timestamp": st.column_config.DatetimeColumn("When (UTC)", format="YYYY-MM-DD HH:mm"),
    "required_approvals": st.column_config.ListColumn("Approvals required"),
    "risk_flags": st.column_config.ListColumn("Risk flags"),
})
