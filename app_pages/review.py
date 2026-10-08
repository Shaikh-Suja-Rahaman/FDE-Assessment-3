from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from src import data_access
from src.llm import llm_configured, model_name
from src.mock_service import ensure_running
from src.pipeline import run_pipeline
from src.review_log import HUMAN_ACTIONS, record

CODE_STYLE = {
    "proceed_to_approval_routing": ("green", "check_circle"),
    "route_for_specialist_review": ("orange", "policy"),
    "request_more_information": ("blue", "help"),
    "reuse_existing_tool": ("violet", "inventory_2"),
    "manual_review_required": ("red", "report"),
}
DATA_LEVELS = ["none", "internal_documents", "internal_marketing", "confidential_documents", "source_code",
               "production_telemetry", "employee_pii", "customer_pii", "credentials", "unknown"]
ARCH_LABELS = {"single": "A · Single agent", "staged": "B · Analyst → reviewer", "rules": "Rules only (no AI)"}
LLM_CALLS = {"single": 2, "staged": 3, "rules": 0}


@st.cache_resource
def start_vendor_api() -> bool:
    return ensure_running()


@st.cache_data(ttl=300)
def load_reference():
    return data_access.load_requests(), data_access.load_employees(), data_access.load_vendors(), data_access.load_software_catalog()


def init_state() -> None:
    st.session_state.setdefault("results", {})
    st.session_state.setdefault("custom_requests", {})


init_state()
api_up = start_vendor_api()
requests_data, employees, vendors, catalog = load_reference()

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.subheader("Run settings")
    architecture = st.segmented_control("Architecture", list(ARCH_LABELS), default="single", required=True,
                                        format_func=ARCH_LABELS.get, key="architecture")
    outage = st.toggle("Simulate vendor-risk outage", key="outage",
                       help="Points the vendor-risk tool at a dead endpoint to demonstrate graceful degradation.")
    st.caption(f"Model: `{model_name()}` · ~{LLM_CALLS[architecture]} LLM call(s) per analysis")
    if not llm_configured():
        st.warning("No `GOOGLE_API_KEY` set - runs use deterministic checks only.", icon=":material/key_off:")
    if api_up:
        st.badge("Vendor-risk API online", icon=":material/cloud_done:", color="green")
    else:
        st.badge("Vendor-risk API offline", icon=":material/cloud_off:", color="red")

st.title("Procurement request review")
st.caption("The copilot gathers evidence and recommends a next action. Code enforces policy rules; "
           "a human makes every approval decision.")

# ---------------------------------------------------------------- request intake
source = st.segmented_control("Request", ["Existing request", "New request"], default="Existing request",
                              required=True, key="source")
by_id = {r["request_id"]: r for r in requests_data} | st.session_state.custom_requests

if source == "New request":
    with st.form("new_request", border=True):
        st.markdown("**Submit a new purchase request**")
        emp_options = {f"{e.employee_id} · {e.name} ({e.department})": e.employee_id for e in employees.itertuples()}
        c1, c2, c3 = st.columns(3)
        requester = c1.selectbox("Requester", list(emp_options))
        product = c2.text_input("Product", placeholder="e.g. BrandBoard Enterprise")
        vendor = c3.text_input("Vendor", placeholder="e.g. BrandBoard")
        c1, c2, c3 = st.columns(3)
        category = c1.text_input("Category", placeholder="e.g. Design & Creative")
        cost = c2.number_input("Annual cost (USD)", min_value=0.0, value=None, step=500.0, placeholder="leave empty if unknown")
        users = c3.number_input("Users / licenses", min_value=0, value=None, step=1, placeholder="leave empty if unknown")
        c1, c2, c3 = st.columns(3)
        data_level = c1.selectbox("Data access level", DATA_LEVELS, index=DATA_LEVELS.index("unknown"))
        integrations = c2.text_input("Integrations (comma separated)", placeholder="e.g. SSO, CRM")
        urgency = c3.selectbox("Urgency", ["normal", "high", "urgent"])
        justification = st.text_area("Business justification")
        if st.form_submit_button("Add request", icon=":material/add:", type="primary"):
            rid = f"NEW-{len(st.session_state.custom_requests) + 1:03d}"
            st.session_state.custom_requests[rid] = {
                "request_id": rid, "requester_id": emp_options[requester], "product_name": product.strip(),
                "vendor_name": vendor.strip(), "category": category.strip(),
                "annual_cost_usd": cost, "user_count": int(users) if users is not None else None,
                "business_justification": justification.strip(), "data_access_level": data_level,
                "requested_integrations": [i.strip() for i in integrations.split(",") if i.strip()], "urgency": urgency,
            }
            st.session_state.selected_custom = rid
            st.rerun()
    custom_ids = list(st.session_state.custom_requests)
    if not custom_ids:
        st.stop()
    request_id = st.selectbox("Submitted requests", custom_ids, key="selected_custom",
                              format_func=lambda r: f"{r} · {by_id[r]['product_name'] or '(no product)'}")
else:
    ids = [r["request_id"] for r in requests_data]
    request_id = st.selectbox("Choose a request", ids, key="selected_request",
                              format_func=lambda r: f"{r} · {by_id[r]['product_name']} ({by_id[r]['vendor_name']})")

req = by_id[request_id]
emp = employees[employees.employee_id == req.get("requester_id")]
requester_label = f"{emp.iloc[0]['name']} · {emp.iloc[0]['department']}" if not emp.empty else f"{req.get('requester_id')} (unknown)"


def esc(text) -> str:
    """Escape `$` so Streamlit markdown does not render dollar amounts as LaTeX."""
    return str(text).replace("$", "\\$")


def money(v) -> str:
    return "not provided" if v is None else f"${v:,.0f}"


with st.container(border=True):
    st.markdown(f"#### {req.get('product_name') or '(no product)'} · {req.get('vendor_name') or '(no vendor)'}")
    with st.container(horizontal=True):
        st.metric("Annual cost", money(req.get("annual_cost_usd")))
        st.metric("Users", req.get("user_count") if req.get("user_count") is not None else "not provided")
        st.metric("Data access", req.get("data_access_level") or "not provided")
        st.metric("Urgency", req.get("urgency") or "normal")
    st.markdown(f":material/person: **Requester:** {requester_label} &nbsp; :material/category: **Category:** "
                f"{req.get('category') or '-'} &nbsp; :material/hub: **Integrations:** {', '.join(req.get('requested_integrations') or []) or 'none'}")
    st.markdown("**Business justification** :gray-badge[untrusted text]")
    st.markdown(f"> {esc(req.get('business_justification')) if req.get('business_justification') else '_(empty)_'}")

run_key = f"{request_id}|{architecture}|{outage}"
if st.button("Analyze request", type="primary", icon=":material/play_arrow:"):
    with st.status(f"Running {ARCH_LABELS[architecture]}…", expanded=False) as status:
        override = req if request_id in st.session_state.custom_requests else None
        result = run_pipeline(request_id, architecture, request_override=override, simulate_vendor_outage=outage)
        st.session_state.results[run_key] = result
        status.update(label="Analysis complete", state="complete")

result = st.session_state.results.get(run_key)
if result is None:
    st.caption("Run the analysis to see the evidence, required approvals and recommended next step.")
    st.stop()

d, t, trace = result.decision, result.decision.telemetry, result.trace
policy, guard = trace["policy"], trace["guardrails"]
color, icon = CODE_STYLE.get(t.recommendation_code, ("gray", "info"))

# ---------------------------------------------------------------- recommendation
with st.container(border=True):
    head, _, sentence = d.recommendation.partition(": ")
    st.markdown(f"### :{color}[:material/{icon}: {head}]")
    st.markdown(esc(sentence))
    with st.container(horizontal=True):
        st.badge("Human review required", icon=":material/person_check:", color="orange")
        st.badge(ARCH_LABELS[t.architecture], icon=":material/account_tree:", color="gray")
        if t.degraded_mode:
            st.badge("AI unavailable - deterministic decision", icon=":material/warning:", color="red")
        if t.guardrail_overrides:
            st.badge(f"{len(t.guardrail_overrides)} guardrail override(s)", icon=":material/shield:", color="violet")
    st.markdown(f"**Next step:** {esc(d.next_step)}")

with st.container(horizontal=True):
    st.metric("Approvals required", len(d.required_approvals), border=True)
    st.metric("Risk flags", len(d.risk_flags), border=True)
    st.metric("Missing information", len(d.missing_information), border=True)
    st.metric("Latency", f"{t.latency_ms / 1000:.1f} s", border=True)
    st.metric("LLM / tool calls", f"{t.llm_calls} / {t.tool_calls}", border=True)

left, right = st.columns(2)
with left:
    with st.container(border=True):
        st.markdown("**:material/how_to_reg: Approvals required**")
        reasons = policy.get("approval_reasons", {})
        for a in d.required_approvals:
            why = "; ".join(reasons.get(a, [])) or "Added by AI review (conservative escalation)"
            st.markdown(f"- **{a}** - {esc(why)}")
with right:
    with st.container(border=True):
        st.markdown("**:material/flag: Risk flags**")
        flag_reasons = policy.get("flag_reasons", {})
        for f in d.risk_flags:
            st.markdown(f"- `{f}` - {esc('; '.join(flag_reasons.get(f, [])) or 'raised by AI review')}")
        if not d.risk_flags:
            st.caption("No risk flags.")
        st.markdown("**:material/help: Missing information**")
        for m in d.missing_information:
            st.markdown(f"- {esc(m)}")
        if not d.missing_information:
            st.caption("Nothing missing.")

# ---------------------------------------------------------------- evidence + trace
ev_tab, checks_tab, trace_tab, json_tab = st.tabs(["Evidence", "Policy checks", "Agent trace", "Decision JSON"])
with ev_tab:
    ev = pd.DataFrame([e.model_dump() for e in d.evidence])
    ev.insert(0, "origin", ["AI analysis" if "(AI analysis)" in s else "Tool / rule" for s in ev.source])
    ev["source"] = ev.source.str.replace(" (AI analysis)", "", regex=False)
    st.dataframe(ev, hide_index=True, column_config={
        "origin": st.column_config.TextColumn("Origin", width="small"),
        "source": st.column_config.TextColumn("Tool", width="medium"),
        "finding": st.column_config.TextColumn("Finding", width="large"),
        "reference": st.column_config.TextColumn("Reference", width="small"),
    })
    if guard.get("ungrounded_evidence"):
        with st.expander(f":material/block: {len(guard['ungrounded_evidence'])} AI evidence item(s) removed - not grounded in tool output"):
            for u in guard["ungrounded_evidence"]:
                st.markdown(f"- _{esc(u['item'].get('finding'))}_ → {esc(u['reason'])}")
with checks_tab:
    st.caption(f"Deterministic policy engine · reference date {policy.get('reference_date')} · thresholds parsed from procurement_policy.md")
    st.dataframe(pd.DataFrame(policy.get("checks", [])), hide_index=True, column_config={
        "check": "Check", "status": st.column_config.TextColumn("Status", width="small"),
        "detail": st.column_config.TextColumn("Detail", width="large"), "policy_reference": "Policy"})
with trace_tab:
    st.markdown("**Tool calls**")
    st.dataframe(pd.DataFrame([{
        "tool": c["name"], "called by": c["caller"], "status": "ok" if c["ok"] else "failed",
        "cached": c["cached"], "counted": c["counted"], "ms": c["latency_ms"],
        "args": json.dumps(c["args"])} for c in trace["tool_calls"]]), hide_index=True)
    st.markdown("**LLM calls**")
    if trace["llm_calls"]:
        st.dataframe(pd.DataFrame(trace["llm_calls"]), hide_index=True)
    else:
        st.caption("None (rules-only run).")
    st.markdown("**Guardrail report** - what code changed or rejected in the model's output")
    st.json({k: v for k, v in guard.items() if k != "ungrounded_evidence"}, expanded=False)
    if trace.get("evidence_pack"):
        st.markdown("**Analyst → reviewer evidence pack** (architecture B handoff)")
        st.json(trace["evidence_pack"], expanded=False)
with json_tab:
    st.json(d.model_dump(), expanded=False)

# ---------------------------------------------------------------- human decision
with st.container(border=True):
    st.markdown("**:material/gavel: Reviewer decision**")
    st.caption("The copilot only recommends. Record the human decision taken on this recommendation.")
    with st.form(f"decision_{run_key}", border=False):
        action = st.segmented_control("Action", list(HUMAN_ACTIONS), format_func=HUMAN_ACTIONS.get, required=True,
                                      default="return_to_requester" if d.missing_information else "send_for_approvals")
        c1, c2 = st.columns([1, 2])
        reviewer = c1.text_input("Reviewer name")
        comment = c2.text_input("Comment")
        if st.form_submit_button("Record decision", icon=":material/save:"):
            if not reviewer.strip():
                st.error("Enter the reviewer's name.")
            else:
                record({"request_id": request_id, "reviewer": reviewer.strip(), "action": action, "comment": comment.strip(),
                        "architecture": t.architecture, "model": t.model, "recommendation_code": t.recommendation_code,
                        "required_approvals": d.required_approvals, "risk_flags": d.risk_flags})
                st.success(f"Recorded: {HUMAN_ACTIONS[action]} by {reviewer.strip()}.", icon=":material/check:")
