"""Code-enforced controls applied to every LLM output, in both architectures.

AI interprets and recommends; code enforces. Concretely:
- deterministic minimums (approvals, flags, missing info) can be added to, never removed;
- the LLM may not contradict deterministic facts (e.g. claim a budget shortfall that does not exist);
- the recommendation code must be consistent with the rule results;
- LLM evidence is kept only if its source tool was actually called in this run
  and every number/date it cites appears in a tool output (grounding check);
- human review is always required (policy §11).
Everything the guardrails change is recorded so the evaluation can measure it.
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher

from src.contracts import EvidenceItem, ProcurementDecision
from src.policy_engine import (CANONICAL_APPROVALS, RECOMMENDATION_CODES, SPECIALIST_APPROVALS, canonical_approval)
from src.telemetry import RunContext

# Flags only the deterministic engine may set: the LLM cannot invent them.
DETERMINISTIC_FLAGS = {"missing_information", "budget_insufficient", "budget_unverified", "vendor_review_expired",
                       "conflicting_vendor_evidence", "vendor_risk_unavailable", "existing_tool_overlap"}
# Flags the LLM may add as conservative escalations (with the matching approval).
ESCALATION_FLAGS = {"security_review_required": "Security", "privacy_review_required": "Privacy",
                    "legal_review_required": "Legal", "prompt_injection_detected": None}
# Business approvers are fixed by the §4 threshold table; the LLM may not add/remove them.
THRESHOLD_APPROVERS = {"Department Head", "Procurement", "Finance", "CFO"}

MISSING_CATEGORIES = {
    "cost": r"cost|price|spend|amount",
    "users": r"user|seat|licen",
    "purpose": r"purpose|justification|use case|business need|gap",
    "data": r"data",
    "integrations": r"integrat",
    "requester": r"requester|department",
    "product": r"product|vendor",
}

# Only judgement-type fields can be declared "missing" by the LLM; presence of
# cost, users, requester and product is checked deterministically.
LLM_MISSING_CATEGORIES = {"purpose", "data", "integrations"}

FORBIDDEN_CLAIMS = re.compile(r"\b(has been|have been|is now|was|i have|we have|i've|we've)\s+(approved|purchased|bought|signed)\b", re.I)


def _snake(flag: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(flag).lower()).strip("_")


def _categories(text: str) -> set[str]:
    t = text.lower()
    return {c for c, pattern in MISSING_CATEGORIES.items() if re.search(pattern, t)}


_NUM = re.compile(r"(?<![A-Za-z0-9\-_.])\$?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?![\dA-Za-z\-_])")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _numbers(text: str) -> set[str]:
    out = set()
    for whole, frac in _NUM.findall(_DATE.sub(" ", text)):
        value = whole.replace(",", "")
        if frac and frac.strip("0"):
            out.add(f"{value}.{frac.rstrip('0')}")
        else:
            out.add(value)
    return out


def grounding_index(ctx: RunContext) -> tuple[set[str], set[str], set[str]]:
    blob = json.dumps(ctx.tool_outputs(), default=str)
    return ctx.tools_called(), _numbers(blob), set(_DATE.findall(blob))


def check_grounding(item: dict, called: set[str], numbers: set[str], dates: set[str]) -> str | None:
    """Return None if grounded, else the reason it is not."""
    source = str(item.get("source") or "")
    if source not in called:
        return f"source '{source}' was not called in this run"
    finding = str(item.get("finding") or "")
    for d in _DATE.findall(finding):
        if d not in dates:
            return f"date {d} not present in any tool output"
    for n in _numbers(finding):
        if float(n) >= 10 and n not in numbers:
            return f"number {n} not present in any tool output"
    return None


def _is_duplicate(finding: str, existing: list[EvidenceItem]) -> bool:
    """True if the finding just restates evidence already shown (keeps the panel readable)."""
    a = re.sub(r"\W+", " ", finding.lower()).strip()
    for e in existing:
        b = re.sub(r"\W+", " ", e.finding.lower()).strip()
        if a in b or b in a or SequenceMatcher(None, a, b).ratio() > 0.8:
            return True
    return False


def finalize(
    ctx: RunContext,
    request_id: str,
    llm: dict | None,
    policy: dict,
    degraded_reason: str | None = None,
) -> tuple[ProcurementDecision, dict]:
    report: dict = {"overrides": [], "approvals_added_by_code": [], "approvals_added_by_llm": [], "llm_rejected": [],
                    "flags_added_by_code": [], "flags_added_by_llm": [], "missing_added_by_llm": [],
                    "evidence_proposed": 0, "evidence_grounded": 0, "ungrounded_evidence": [],
                    "llm_raw_code": None, "degraded_reason": degraded_reason}
    llm = llm or {}

    # ---- approvals ---------------------------------------------------------
    approvals = list(policy["required_approvals"])
    llm_approvals = []
    for a in llm.get("required_approvals") or []:
        canon = canonical_approval(a)
        if canon and canon not in llm_approvals:
            llm_approvals.append(canon)
    if llm:
        report["approvals_added_by_code"] = [a for a in approvals if a not in llm_approvals]
    for a in llm_approvals:
        if a in approvals:
            continue
        if a in THRESHOLD_APPROVERS:
            report["llm_rejected"].append(f"approval '{a}' contradicts the §4 threshold / budget result")
        else:
            approvals.append(a)
            report["approvals_added_by_llm"].append(a)

    # ---- risk flags ----------------------------------------------------------
    flags = list(policy["risk_flags"])
    llm_flags = [_snake(f) for f in llm.get("risk_flags") or []]
    if llm:
        report["flags_added_by_code"] = [f for f in flags if f not in llm_flags]
    for f in llm_flags:
        if f in flags or not f:
            continue
        if f in ESCALATION_FLAGS:
            flags.append(f)
            report["flags_added_by_llm"].append(f)
            approver = ESCALATION_FLAGS[f]
            if approver and approver not in approvals:
                approvals.append(approver)
                report["approvals_added_by_llm"].append(approver)
        elif f in DETERMINISTIC_FLAGS:
            report["llm_rejected"].append(f"flag '{f}' contradicts the deterministic checks")
        else:
            report["llm_rejected"].append(f"flag '{f}' is not in the risk-flag taxonomy")
    approvals = [a for a in CANONICAL_APPROVALS if a in approvals]

    # ---- recommendation code ------------------------------------------------
    default_code = policy["default_recommendation_code"]
    code = llm.get("recommendation_code")
    report["llm_raw_code"] = code
    overridden = False
    if not llm:
        code = default_code
    elif code not in RECOMMENDATION_CODES:
        report["overrides"].append(f"invalid recommendation_code '{code}' -> {default_code}")
        code, overridden = default_code, True
    elif policy["missing_information"] and code != "request_more_information":
        report["overrides"].append(f"{code} -> request_more_information (required §1 information is missing)")
        code, overridden = "request_more_information", True
    elif code == "proceed_to_approval_routing" and default_code != "proceed_to_approval_routing":
        report["overrides"].append(f"proceed_to_approval_routing -> {default_code} (deterministic checks require review first)")
        code, overridden = default_code, True
    elif code == "reuse_existing_tool" and "existing_tool_overlap" not in flags:
        report["overrides"].append(f"reuse_existing_tool -> {default_code} (no catalog overlap found)")
        code, overridden = default_code, True

    # ---- missing information -----------------------------------------------
    missing = list(policy["missing_information"])
    covered = set().union(*[_categories(m) for m in missing]) if missing else set()
    if code == "request_more_information":
        for item in llm.get("missing_information") or []:
            cats = _categories(str(item))
            if cats and cats <= LLM_MISSING_CATEGORIES and not cats & covered:
                missing.append(str(item))
                covered |= cats
                report["missing_added_by_llm"].append(str(item))
            else:
                report.setdefault("missing_rejected", []).append(str(item))
    if "missing_information" not in flags and missing:
        flags.insert(0, "missing_information")

    # ---- evidence (deterministic first, then grounded LLM interpretation) ----
    evidence = [EvidenceItem(**e) for e in policy["evidence"]]
    called, numbers, dates = grounding_index(ctx)
    report["evidence_duplicates"] = 0
    for item in (llm.get("evidence") or [])[:8]:
        report["evidence_proposed"] += 1
        reason = check_grounding(item, called, numbers, dates)
        if reason:
            report["ungrounded_evidence"].append({"item": item, "reason": reason})
            continue
        report["evidence_grounded"] += 1
        if _is_duplicate(str(item.get("finding", "")), evidence):
            report["evidence_duplicates"] += 1
            continue
        evidence.append(EvidenceItem(source=f"{item['source']} (AI analysis)", finding=str(item["finding"]),
                                     reference=item.get("reference") or None))
    if llm.get("existing_tool_assessment") and "existing_tool_overlap" in flags:
        evidence.append(EvidenceItem(source="search_existing_software (AI analysis)",
                                     finding=str(llm["existing_tool_assessment"]), reference="Policy §3"))

    # ---- recommendation + next step ----------------------------------------
    sentence = str(llm.get("recommendation") or "").strip()
    if overridden or not sentence or FORBIDDEN_CLAIMS.search(sentence):
        sentence = template_sentence(code, policy, approvals)
    next_step = str(llm.get("next_step") or "").strip()
    if overridden or not next_step or FORBIDDEN_CLAIMS.search(next_step):
        next_step = template_next_step(code, policy, approvals)
    questions = [str(q).strip() for q in (llm.get("clarifying_questions") or []) if str(q).strip()]
    if questions:
        next_step += " Ask the requester: " + " ".join(q if q.endswith("?") else q + "?" for q in questions[:3])
    if degraded_reason:
        flags.append("ai_analysis_unavailable")
        next_step = "AI analysis unavailable - decision based on deterministic policy checks only. " + next_step

    decision = ProcurementDecision(
        request_id=request_id,
        recommendation=f"{RECOMMENDATION_CODES[code]}: {sentence}",
        evidence=evidence,
        required_approvals=approvals,
        missing_information=missing,
        risk_flags=list(dict.fromkeys(flags)),
        next_step=next_step,
        human_review_required=True,   # policy §11: the copilot never approves on its own
    )
    report["final_code"] = code
    return decision, report


def template_sentence(code: str, policy: dict, approvals: list[str]) -> str:
    specialists = [a for a in approvals if a in SPECIALIST_APPROVALS]
    business = [a for a in approvals if a not in SPECIALIST_APPROVALS]
    if code == "request_more_information":
        items = "; ".join(m.split(" (")[0].split(" - ")[0] for m in policy["missing_information"]) or "details"
        return f"The request is not ready for approval - missing {items}."
    if code == "manual_review_required":
        reasons = [f.replace("_", " ") for f in policy["risk_flags"] if f in {"vendor_risk_unavailable", "conflicting_vendor_evidence", "vendor_review_expired"}]
        return f"Material vendor evidence could not be verified ({', '.join(reasons) or 'see evidence'}); it must be checked manually before approval."
    if code == "route_for_specialist_review":
        return f"{', '.join(specialists) or 'Finance'} review is required before {', '.join(business)} approval."
    if code == "reuse_existing_tool":
        return "An approved catalog tool appears to cover this need; confirm the gap before buying a new product."
    return f"Deterministic checks pass; route to {', '.join(approvals)} for approval."


def template_next_step(code: str, policy: dict, approvals: list[str]) -> str:
    if code == "request_more_information":
        return "Return the request to the requester to supply the missing information, then re-run the review."
    if code == "manual_review_required":
        return "Send the evidence package to Security and Procurement to verify the vendor status manually before any approval routing."
    if code == "route_for_specialist_review":
        specialists = [a for a in approvals if a in SPECIALIST_APPROVALS] or ["Finance"]
        return f"Open review tickets for {', '.join(specialists)} with this evidence package; route to business approvers once they clear."
    if code == "reuse_existing_tool":
        return "Ask the requester whether the existing approved tool meets the need; if a real gap exists, resubmit with the gap stated."
    return f"Send the request with this evidence to {', '.join(approvals)} for approval."
