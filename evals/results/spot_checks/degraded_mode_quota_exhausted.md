# Degraded-mode evidence (2026-10-07): daily quota hit mid-run; pipeline fell back to deterministic decision

```

=== REQ-1008 staged: code=proceed_to_approval_routing llm=3 tools=7 3759.0ms tok=2477/126 degraded=True
REC: Proceed to business approval routing: Deterministic checks pass; route to Department Head, Procurement for approval.
NEXT: AI analysis unavailable - decision based on deterministic policy checks only. Send the request with this evidence to Department Head, Procurement for approval.
APPR: ['Department Head', 'Procurement'] FLAGS: ['existing_tool_overlap', 'ai_analysis_unavailable'] MISSING: []
guard: {'llm_raw_code': None, 'overrides': [], 'approvals_added_by_code': [], 'approvals_added_by_llm': [], 'flags_added_by_code': [], 'flags_added_by_llm': [], 'llm_rejected': [], 'missing_added_by_llm': [], 'evidence_proposed': 0, 'evidence_grounded': 0}
  llm: analyst#1 True 1653.9 0 None
  llm: analyst#2 True 1411.9 0 None
  llm: analyst#3 False 670.3 6 daily quota exhausted (429)
  tools: [('get_purchase_request', 'agent', False), ('get_requester_profile', 'agent', False), ('search_existing_software', 'agent', False), ('get_vendor_registry_record', 'agent', False), ('get_vendor_risk_assessment', 'agent', False), ('get_purchase_request', 'policy_engine', True), ('get_requester_profile', 'policy_engine', True), ('check_department_budget', 'policy_engine', False), ('search_existing_software', 'policy_engine', True), ('get_vendor_registry_record', 'policy_engine', True), ('get_vendor_risk_assessment', 'policy_engine', True), ('run_policy_checks', 'orchestrator', False)]
```
