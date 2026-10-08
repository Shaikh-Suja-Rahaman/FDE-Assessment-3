from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

RESULTS = Path(__file__).resolve().parents[1] / "evals" / "results"
ARCH = {"single": "A · Single agent", "staged": "B · Analyst → reviewer", "rules": "Rules only (reference)"}

st.title("Architecture evaluation")
st.caption("Same case set run through both architectures (and a no-LLM rules baseline). "
           "Reproduce with `python evals/run_eval.py`.")

csv_path = RESULTS / "eval_results.csv"
if not csv_path.exists():
    st.info("No results yet. Run `python evals/run_eval.py`.", icon=":material/info:")
    st.stop()


@st.cache_data(ttl=60)
def load(path: str, mtime: float) -> pd.DataFrame:
    return pd.read_csv(path)


df = load(str(csv_path), csv_path.stat().st_mtime)
df["architecture"] = df["architecture"].map(ARCH).fillna(df["architecture"])

agg = df.groupby("architecture").agg(
    cases=("case_id", "count"),
    pass_rate=("case_pass", "mean"),
    next_action=("correct_next_action", "mean"),
    policy=("policy_followed", "mean"),
    escalation=("human_escalation_correct", "mean"),
    grounded=("grounded_evidence", "mean"),
    latency_s=("latency_ms", lambda s: s.mean() / 1000),
    llm_calls=("llm_calls", "mean"),
    tool_calls=("tool_calls", "mean"),
    tokens=("input_tokens", "mean"),
).reset_index()

st.dataframe(agg, hide_index=True, column_config={
    "architecture": "Architecture", "cases": "Runs", "pass_rate": st.column_config.ProgressColumn("All criteria", min_value=0, max_value=1, format="percent"),
    "next_action": st.column_config.ProgressColumn("Next action", min_value=0, max_value=1, format="percent"),
    "policy": st.column_config.ProgressColumn("Policy followed", min_value=0, max_value=1, format="percent"),
    "escalation": st.column_config.ProgressColumn("Escalation", min_value=0, max_value=1, format="percent"),
    "grounded": st.column_config.ProgressColumn("Grounded", min_value=0, max_value=1, format="percent"),
    "latency_s": st.column_config.NumberColumn("Avg latency (s)", format="%.1f"),
    "llm_calls": st.column_config.NumberColumn("Avg LLM calls", format="%.1f"),
    "tool_calls": st.column_config.NumberColumn("Avg tool calls", format="%.1f"),
    "tokens": st.column_config.NumberColumn("Avg input tokens", format="%d"),
})

c1, c2 = st.columns(2)
with c1:
    st.markdown("**Average latency (s)**")
    st.bar_chart(agg.set_index("architecture")["latency_s"], horizontal=True, height=200)
with c2:
    st.markdown("**Average LLM calls per request**")
    st.bar_chart(agg.set_index("architecture")["llm_calls"], horizontal=True, height=200)

st.subheader("Per case")
arch_filter = st.pills("Architecture", list(df["architecture"].unique()), selection_mode="multi",
                       default=list(df["architecture"].unique()))
view = df[df["architecture"].isin(arch_filter)]
st.dataframe(view[["case_id", "request_id", "edge_case", "architecture", "recommendation_code", "case_pass",
                   "correct_next_action", "policy_followed", "human_escalation_correct", "grounded_evidence",
                   "latency_ms", "llm_calls", "tool_calls", "notes"]], hide_index=True,
             column_config={"notes": st.column_config.TextColumn("Notes", width="large")})

summary = RESULTS / "summary.md"
if summary.exists():
    with st.expander("Full summary (summary.md)"):
        st.markdown(summary.read_text(encoding="utf-8"))
