"""Deterministic procurement policy engine.

Everything that the policy states as a rule is decided here, in code:
approval thresholds, budget check, vendor-review expiry and source conflicts,
Security / Privacy / Legal triggers, required-field completeness and the
prompt-injection scan. Thresholds, the reference date and the review validity
window are parsed from `data/procurement_policy.md` (with safe defaults), so
the policy file stays the source of truth.

The LLM layer may *add* conservative escalations but can never remove what
this engine requires (see src/guardrails.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Any

from src import data_access
from src.safety import scan_record, strip_injected_sentences

CANONICAL_APPROVALS = ["Manager", "Department Head", "Procurement", "Finance", "CFO", "Security", "Privacy", "Legal"]
SPECIALIST_APPROVALS = {"Security", "Privacy", "Legal"}

FLAG_ORDER = [
    "missing_information", "prompt_injection_detected", "budget_insufficient", "budget_unverified",
    "existing_tool_overlap", "security_review_required", "vendor_review_expired", "conflicting_vendor_evidence",
    "vendor_risk_unavailable", "privacy_review_required", "legal_review_required",
]

RECOMMENDATION_CODES = {
    "proceed_to_approval_routing": "Proceed to business approval routing",
    "route_for_specialist_review": "Route for specialist review before approval",
    "request_more_information": "Request more information from requester",
    "reuse_existing_tool": "Consider existing approved tool before purchasing",
    "manual_review_required": "Manual review required - evidence unavailable or conflicting",
}

UNKNOWN_VALUES = {"", "unknown", "tbd", "n/a", "na", "none specified", "unspecified", "not sure", "?", "null"}


# ---------------------------------------------------------------------------
# policy parsing
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Tier:
    label: str
    upper_usd: float | None          # inclusive upper bound; None = no upper bound
    approvals: tuple[str, ...]


@dataclass(frozen=True)
class PolicyRules:
    reference_date: date
    review_validity_days: int
    tiers: tuple[Tier, ...]
    legal_new_vendor_threshold_usd: float

    def tier_for(self, amount: float) -> Tier:
        for tier in self.tiers:
            if tier.upper_usd is None or amount <= tier.upper_usd:
                return tier
        return self.tiers[-1]


DEFAULT_TIERS = (
    Tier("Up to $1,000", 1000.0, ("Manager",)),
    Tier("$1,000.01 - $10,000", 10000.0, ("Department Head", "Procurement")),
    Tier("$10,000.01 - $25,000", 25000.0, ("Department Head", "Finance", "Procurement")),
    Tier("Above $25,000", None, ("Department Head", "Finance", "CFO", "Procurement")),
)


def canonical_approval(name: str) -> str | None:
    n = re.sub(r"[^a-z ]", " ", str(name).lower())
    n = re.sub(r"\s+", " ", n).strip()
    aliases = {
        "manager": "Manager", "line manager": "Manager", "direct manager": "Manager",
        "department head": "Department Head", "dept head": "Department Head", "head of department": "Department Head",
        "procurement": "Procurement", "finance": "Finance", "budget owner": "Finance", "cfo": "CFO",
        "chief financial officer": "CFO", "security": "Security", "infosec": "Security", "privacy": "Privacy",
        "dpo": "Privacy", "legal": "Legal",
    }
    if n in aliases:
        return aliases[n]
    for key, value in aliases.items():
        if key in n:
            return value
    return None


def _money(text: str) -> list[float]:
    return [float(x.replace(",", "")) for x in re.findall(r"\$\s*([\d,]+(?:\.\d+)?)", text)]


@lru_cache(maxsize=1)
def load_policy_rules() -> PolicyRules:
    text = data_access.load_policy_text()

    ref = re.search(r"reference date[^0-9]{0,20}(\d{4}-\d{2}-\d{2})", text, re.I)
    reference_date = date.fromisoformat(ref.group(1)) if ref else date(2026, 9, 30)

    validity = re.search(r"current for\W*(\d+)\s*days", text, re.I)
    validity_days = int(validity.group(1)) if validity else 365

    legal = re.search(r"annual spend is\W*\$([\d,]+)", text, re.I)
    legal_threshold = float(legal.group(1).replace(",", "")) if legal else 10000.0

    tiers: list[Tier] = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 2 or not line.strip().startswith("|"):
            continue
        amount, approvers = cells
        if "$" not in amount:
            continue
        names = tuple(a for a in (canonical_approval(p) for p in approvers.split("+")) if a)
        if not names:
            continue
        upper = None if re.search(r"above|over|more than", amount, re.I) else max(_money(amount))
        tiers.append(Tier(amount, upper, names))
    tiers.sort(key=lambda t: float("inf") if t.upper_usd is None else t.upper_usd)
    return PolicyRules(reference_date, validity_days, tuple(tiers) or DEFAULT_TIERS, legal_threshold)


@lru_cache(maxsize=1)
def policy_sections() -> list[dict]:
    text = data_access.load_policy_text()
    sections = []
    for match in re.finditer(r"^## (\d+)\. (.+?)\n(.*?)(?=^## |\Z)", text, re.M | re.S):
        sections.append({"number": match.group(1), "title": match.group(2).strip(), "text": match.group(3).strip()})
    return sections


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None
    return number


def _usd(value: float) -> str:
    return f"${value:,.2f}".replace(".00", "")


def _status(value: Any) -> str:
    v = str(value or "").strip().lower().replace(" ", "_")
    if v in {"approved", "current", "completed", "passed"}:
        return "approved"
    if v in {"expired", "stale", "lapsed"}:
        return "expired"
    if v in {"pending", "not_completed", "in_progress", "incomplete", "draft"}:
        return "not_completed"
    if v in {"", "unknown", "none", "null"}:
        return "unknown"
    return v


def _age(iso: Any, ref: date) -> int | None:
    try:
        return (ref - date.fromisoformat(str(iso)[:10])).days if iso else None
    except ValueError:
        return None


SECURITY_DATA_RULES = [
    (r"source code", "source code access"),
    (r"\bprod(uction)?\b", "production access"),
    (r"\bcloud\b", "cloud-account access"),
    (r"confidential", "confidential documents"),
    (r"employee pii|employee personal", "employee PII"),
    (r"customer pii|customer personal", "customer PII"),
    (r"credential|secret|password|api key", "credentials/secrets"),
]
SECURITY_INTEGRATION_RULES = [
    (r"\bprod(uction)?\b", "production integration"),
    (r"cloud account|\baws\b|\bgcp\b|\bazure\b|kubernetes", "cloud-account integration"),
    (r"\bgit\b|github|gitlab|bitbucket|source code", "source-code repository integration"),
    (r"vault|secret|credential|password", "credentials/secrets integration"),
]


def _matches(rules: list[tuple[str, str]], text: str) -> list[str]:
    return [label for pattern, label in rules if re.search(pattern, text)]


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------
def evaluate_policy(bundle: dict) -> dict:
    rules = load_policy_rules()
    ref = rules.reference_date
    approvals: dict[str, list[str]] = {}
    flags: dict[str, list[str]] = {}
    missing: list[str] = []
    checks: list[dict] = []
    evidence: list[dict] = []

    def need(name: str, reason: str) -> None:
        approvals.setdefault(name, []).append(reason)

    def flag(name: str, reason: str) -> None:
        flags.setdefault(name, []).append(reason)

    def check(name: str, status: str, detail: str, ref_: str) -> None:
        checks.append({"check": name, "status": status, "detail": detail, "policy_reference": ref_})

    def ev(source: str, finding: str, reference: str | None = None) -> None:
        evidence.append({"source": source, "finding": finding, "reference": reference})

    req_out = bundle["request"]
    if req_out.get("status") != "ok":
        missing.append("Purchase request record (request could not be retrieved)")
        flag("missing_information", "Request record not found")
        need("Procurement", "Request could not be retrieved; manual triage required")
        check("Request retrieval", "fail", req_out.get("error", "Request not found"), "§1")
        ev("get_purchase_request", req_out.get("error", "Request not found"), bundle["request_id"])
        return _result(rules, approvals, flags, missing, checks, evidence, [], None, {}, "manual_review_required")

    req = req_out["request"]
    rid = req.get("request_id", bundle["request_id"])
    cost = _num(req.get("annual_cost_usd"))
    users = _num(req.get("user_count"))
    data_level_raw = str(req.get("data_access_level") or "").strip()
    data_level = data_level_raw.lower().replace("_", " ").replace("-", " ")
    integrations = req.get("requested_integrations")
    integ_text = " ".join(str(i) for i in integrations).lower() if isinstance(integrations, list) else str(integrations or "").lower()
    vendor_name = str(req.get("vendor_name") or "").strip()
    product_name = str(req.get("product_name") or "").strip()

    # ---- §9 untrusted content ----------------------------------------
    injections = scan_record("request", req)
    reg_rec = (bundle["registry"] or {}).get("record") if bundle["registry"].get("status") == "ok" else None
    risk_ok = bundle["risk"].get("status") == "ok"
    assessment = bundle["risk"].get("assessment") or {} if risk_ok else {}
    injections += scan_record("vendor_registry", reg_rec) + scan_record("vendor_risk_api", assessment)
    if injections:
        flag("prompt_injection_detected", "Embedded instructions found in business data")
        fields = sorted({f["field"] for f in injections})
        snippet = injections[0]["snippet"]
        ev("run_policy_checks",
           f"Embedded instructions detected in {', '.join(fields)} (e.g. \"{snippet}\"). Treated as untrusted data and ignored; "
           "no approval is implied.", "Policy §9")
        check("Prompt-injection scan", "flag", f"{len(injections)} finding(s) in {', '.join(fields)}", "§9")
    else:
        check("Prompt-injection scan", "pass", "No embedded instructions found in request or vendor text", "§9")

    # ---- §1 required information --------------------------------------
    emp_out = bundle["employee"]
    employee = emp_out.get("employee") if emp_out.get("status") == "ok" else None
    department = (employee or {}).get("department")
    if not employee or not department:
        missing.append(f"Requester and department (requester_id '{req.get('requester_id')}' not found in employee directory)")
    if not product_name or not vendor_name:
        missing.append("Product and vendor name")
    if cost is None or cost < 0:
        missing.append("Annual cost or a reasonable annual estimate (annual_cost_usd is empty)")
    if users is None or users <= 0:
        missing.append("Number of users/licenses (user_count is empty)")
    purpose = strip_injected_sentences(req.get("business_justification"))
    if len(re.findall(r"[A-Za-z]{2,}", purpose)) < 4:
        shown = purpose or "empty"
        missing.append(f"Business purpose - justification is too vague to assess ('{shown}')")
    if data_level in UNKNOWN_VALUES:
        missing.append(f"Intended data-access level (data_access_level is '{data_level_raw or 'empty'}')")
    if integrations is None:
        missing.append("Required integrations (field not provided; use an empty list if none)")
    if missing:
        flag("missing_information", f"{len(missing)} required field(s) missing")
        ev("run_policy_checks", "Request is not ready for approval; missing: " + "; ".join(m.split(" (")[0] for m in missing) + ".",
           "Policy §1")
        check("Required information", "fail", "; ".join(missing), "§1")
    else:
        check("Required information", "pass", "All §1 fields present", "§1")

    if employee:
        mgr = emp_out.get("manager") or {}
        ev("get_requester_profile",
           f"Requester {employee['employee_id']} ({employee.get('name')}) is in {department}"
           + (f"; manager {mgr.get('employee_id')} ({mgr.get('name')})." if mgr else "."),
           f"employees.csv:{employee['employee_id']}")

    # ---- §2 budget ------------------------------------------------------
    budget_out = bundle["budget"]
    if budget_out.get("status") == "ok" and cost is not None:
        available = float(budget_out["budget"]["available_usd"])
        if cost <= available:
            check("Budget", "pass", f"{_usd(cost)} within available {_usd(available)}", "§2")
            ev("check_department_budget",
               f"{department} available software budget is {_usd(available)}; the {_usd(cost)} request fits "
               f"({_usd(available - cost)} would remain). A positive budget check is not an approval.",
               f"department_budgets.csv:{department}")
        else:
            flag("budget_insufficient", f"{_usd(cost)} exceeds available {_usd(available)}")
            need("Finance", f"Budget exception: {_usd(cost)} exceeds {department} available budget {_usd(available)}")
            check("Budget", "flag", f"{_usd(cost)} exceeds available {_usd(available)} (shortfall {_usd(cost - available)})", "§2")
            ev("check_department_budget",
               f"{department} available software budget is {_usd(available)}; the {_usd(cost)} request exceeds it by "
               f"{_usd(cost - available)}. Finance budget-exception review required.", f"department_budgets.csv:{department}")
    elif budget_out.get("status") != "ok" and department:
        flag("budget_unverified", budget_out.get("error", "Budget record unavailable"))
        need("Finance", "Department budget could not be verified")
        check("Budget", "unknown", budget_out.get("error", "Budget record unavailable"), "§2")
    else:
        check("Budget", "unknown", "Cannot compare budget without annual cost / department", "§2")

    # ---- §4 financial thresholds ------------------------------------------
    tier = None
    if cost is not None and cost >= 0:
        tier = rules.tier_for(cost)
        for name in tier.approvals:
            need(name, f"Financial threshold: {_usd(cost)} is in tier '{tier.label}'")
        check("Approval threshold", "pass", f"{_usd(cost)} -> {tier.label}: {' + '.join(tier.approvals)}", "§4")
        ev("run_policy_checks", f"Annual amount {_usd(cost)} falls in tier '{tier.label}' -> minimum business approvals: "
           f"{', '.join(tier.approvals)}.", "Policy §4")
    else:
        need("Procurement", "Approval tier cannot be determined until annual cost is provided")
        check("Approval threshold", "unknown", "Annual cost missing; tier undetermined", "§4")

    # ---- §3 existing software ----------------------------------------------
    software = bundle["software"]
    catalog = software.get("catalog_matches") or []
    if catalog:
        flag("existing_tool_overlap", f"{len(catalog)} catalog match(es)")
        for m in catalog[:3]:
            ev("search_existing_software",
               f"Existing catalog item {m['product_name']} ({m['software_id']}, {m['category']}, status '{m['status']}', "
               f"{m['licensed_seats']} seats, scope {m['scope']}) - matches on {', '.join(r.replace('_', ' ') for r in m['match_reasons'])}.",
               m["software_id"])
        check("Existing software / overlap", "flag", "; ".join(f"{m['product_name']} ({', '.join(m['match_reasons'])})" for m in catalog), "§3")
    else:
        check("Existing software / overlap", "pass", "No catalog item with the same product, vendor or category", "§3")
    for p in (software.get("purchase_history") or [])[:2]:
        ev("search_existing_software",
           f"Purchase history {p['purchase_id']}: {p['department']} bought {p['product_name']} on {p['purchase_date']} "
           f"({_usd(float(p['annual_amount_usd']))}/yr, {p['notes']}).", p["purchase_id"])

    # ---- §5 / §7 vendor status -------------------------------------------
    registry_out, risk_out = bundle["registry"], bundle["risk"]
    is_new = reg_rec is None or str(reg_rec.get("procurement_status", "")).strip().lower() in {"new", "pending", "onboarding", ""}
    terms = str((reg_rec or {}).get("legal_terms_status") or "Unknown")
    terms_ok = terms.strip().lower() in {"approved", "standard"}
    reg_status = _status((reg_rec or {}).get("security_status"))
    reg_date = (reg_rec or {}).get("security_review_date")
    reg_age = _age(reg_date, ref)

    if reg_rec:
        ev("get_vendor_registry_record",
           f"Internal registry: {vendor_name} procurement status '{reg_rec.get('procurement_status')}', security '{reg_rec.get('security_status')}'"
           + (f" (reviewed {reg_date}, {reg_age} days before {ref.isoformat()})" if reg_date else " (no review date)")
           + f", legal terms '{terms}'.", f"vendors.csv:{reg_rec.get('vendor_id')}")
    else:
        ev("get_vendor_registry_record", f"{vendor_name or 'Vendor'} is not in the internal vendor registry - treated as a new vendor.", "vendors.csv")

    vendor_security_reasons: list[str] = []
    if risk_out.get("status") == "unavailable":
        flag("vendor_risk_unavailable", risk_out.get("error", "Vendor-risk service unavailable"))
        vendor_security_reasons.append("vendor-risk service unavailable - current security status cannot be verified (§10)")
        ev("get_vendor_risk_assessment",
           f"Vendor-risk service unavailable ({risk_out.get('error')}). Security status, personal-data processing and data "
           "residency could NOT be verified; no favourable status is assumed.", risk_out.get("endpoint"))
        check("Vendor-risk service", "unknown", f"Unavailable: {risk_out.get('error')}", "§10")
    elif risk_out.get("status") == "not_found":
        vendor_security_reasons.append("no external vendor security assessment exists")
        ev("get_vendor_risk_assessment", f"No external vendor-risk assessment on record for {vendor_name}.", risk_out.get("endpoint"))
        check("Vendor-risk service", "flag", "No assessment on record", "§5")
    elif risk_ok:
        api_status = _status(assessment.get("security_review_status"))
        api_date = assessment.get("last_review_date")
        api_age = _age(api_date, ref)
        ev("get_vendor_risk_assessment",
           f"Vendor-risk service: risk '{assessment.get('risk_level')}', security review '{assessment.get('security_review_status')}'"
           + (f" (last review {api_date}, {api_age} days before {ref.isoformat()})" if api_date else " (no review date)")
           + f"; processes personal data: {assessment.get('processes_personal_data')}; stores data outside region: "
           f"{assessment.get('stores_data_outside_region')}.", risk_out.get("endpoint"))
        if api_status == "expired" or (api_status == "approved" and api_age is not None and api_age > rules.review_validity_days):
            flag("vendor_review_expired", f"Vendor-risk service review dated {api_date} is older than {rules.review_validity_days} days")
            vendor_security_reasons.append(f"vendor security assessment expired (last review {api_date})")
        elif api_status != "approved":
            vendor_security_reasons.append(f"vendor security assessment is '{assessment.get('security_review_status')}'")
        elif api_age is None:
            vendor_security_reasons.append("vendor security assessment has no review date")
        check("Vendor security assessment", "pass" if not vendor_security_reasons else "flag",
              f"API status '{assessment.get('security_review_status')}', last review {api_date}", "§5")

        # registry vs service conflict
        if reg_rec:
            conflicts = []
            if reg_status != "unknown" and api_status != "unknown" and reg_status != api_status:
                conflicts.append(f"registry security status '{reg_rec.get('security_status')}' vs service '{assessment.get('security_review_status')}'")
            if reg_date and api_date and str(reg_date) != str(api_date):
                conflicts.append(f"registry review date {reg_date} vs service {api_date}")
            if conflicts:
                flag("conflicting_vendor_evidence", "; ".join(conflicts))
                vendor_security_reasons.append("internal registry and vendor-risk service disagree")
                ev("run_policy_checks", f"Conflicting vendor evidence: {'; '.join(conflicts)}. Not resolved automatically - "
                   "route to Security for manual verification.", "Policy §5")
                check("Registry vs service consistency", "flag", "; ".join(conflicts), "§5")
            else:
                check("Registry vs service consistency", "pass", "Registry and vendor-risk service agree", "§5")

    if reg_age is not None and reg_age > rules.review_validity_days and "vendor_review_expired" not in flags:
        flag("vendor_review_expired", f"Registry review dated {reg_date} is older than {rules.review_validity_days} days")
        vendor_security_reasons.append(f"registry security review expired ({reg_date})")
    if not risk_ok and reg_status != "approved":
        vendor_security_reasons.append(f"registry security status is '{(reg_rec or {}).get('security_status', 'not on file')}'")

    # ---- §5 data / integration triggers ------------------------------------
    data_triggers = _matches(SECURITY_DATA_RULES, data_level) + _matches(SECURITY_INTEGRATION_RULES, integ_text)
    for reason in dict.fromkeys(data_triggers + vendor_security_reasons):
        need("Security", reason[0].upper() + reason[1:])
    if "Security" in approvals:
        flag("security_review_required", "; ".join(approvals["Security"]))
        ev("run_policy_checks", "Security review required: " + "; ".join(approvals["Security"]) + ".", "Policy §5")
        check("Security triggers", "flag", "; ".join(approvals["Security"]), "§5")
    else:
        check("Security triggers", "pass", "No §5 trigger (data class, integrations, vendor assessment all clear)", "§5")

    # ---- §6 privacy -----------------------------------------------------------
    pii = bool(re.search(r"\bpii\b|personal", data_level))
    sensitive = pii or "confidential" in data_level
    if pii:
        need("Privacy", f"Tool will process {data_level_raw}")
    if risk_ok and assessment.get("stores_data_outside_region") and sensitive:
        need("Privacy", "Vendor stores data outside the operating region and the request involves sensitive data")
    if "Privacy" in approvals:
        flag("privacy_review_required", "; ".join(approvals["Privacy"]))
        ev("run_policy_checks", "Privacy review required: " + "; ".join(approvals["Privacy"]) + ".", "Policy §6")
        check("Privacy triggers", "flag", "; ".join(approvals["Privacy"]), "§6")
    elif sensitive and not risk_ok:
        check("Privacy triggers", "unknown", "Data residency could not be verified (vendor-risk service unavailable)", "§6")
    else:
        check("Privacy triggers", "pass", "No employee/customer PII and no cross-region sensitive data", "§6")

    # ---- §7 legal -------------------------------------------------------------
    if is_new and cost is not None and cost >= rules.legal_new_vendor_threshold_usd:
        need("Legal", f"New vendor with annual spend {_usd(cost)} >= {_usd(rules.legal_new_vendor_threshold_usd)}")
    if not terms_ok:
        need("Legal", f"Legal terms status is '{terms}' (not approved/standard)")
    if risk_ok and assessment.get("stores_data_outside_region") and pii:
        need("Legal", "Cross-region processing of personal data is a material data-processing issue")
    if "Legal" in approvals:
        flag("legal_review_required", "; ".join(approvals["Legal"]))
        ev("run_policy_checks", "Legal review required: " + "; ".join(approvals["Legal"]) + ".", "Policy §7")
        check("Legal triggers", "flag", "; ".join(approvals["Legal"]), "§7")
    else:
        check("Legal triggers", "pass", "Existing vendor with approved terms and no cross-region personal data", "§7")

    # ---- §8 AI tools ------------------------------------------------------------
    ai_text = f"{req.get('category', '')} {product_name} {vendor_name}"
    is_ai = bool(re.search(r"\bAI\b|artificial intelligence|\bLLM\b|\bGPT\b|copilot|assistant", ai_text))
    if is_ai:
        limited = [m for m in catalog if "limited" in str(m.get("status", "")).lower()]
        detail = "AI tool: prior approvals do not extend to new use cases or data classes"
        if limited:
            detail += f"; existing {limited[0]['product_name']} approval is '{limited[0]['status']}' ({limited[0].get('notes')})"
        check("AI tool rules", "flag" if (limited or sensitive) else "pass", detail, "§8")

    # ---- urgency never bypasses controls ---------------------------------------
    if str(req.get("urgency", "")).lower() in {"urgent", "high", "asap", "critical"}:
        check("Urgency", "info", f"Urgency '{req.get('urgency')}' does not reduce required approvals", "§11")

    # ---- default (rules-only) recommendation -------------------------------------
    flag_set = set(flags)
    if missing:
        code = "request_more_information"
    elif flag_set & {"vendor_risk_unavailable", "conflicting_vendor_evidence"}:
        code = "manual_review_required"
    elif (set(approvals) & SPECIALIST_APPROVALS) or flag_set & {"budget_insufficient", "budget_unverified", "vendor_review_expired"}:
        code = "route_for_specialist_review"
    else:
        code = "proceed_to_approval_routing"

    vendor_summary = {"is_new_vendor": is_new, "legal_terms_status": terms, "registry_security_status": (reg_rec or {}).get("security_status"),
                      "risk_service_status": risk_out.get("status")}
    return _result(rules, approvals, flags, missing, checks, evidence, injections, tier, vendor_summary, code, rid)


def _result(rules, approvals, flags, missing, checks, evidence, injections, tier, vendor_summary, code, rid=None) -> dict:
    ordered_approvals = [a for a in CANONICAL_APPROVALS if a in approvals]
    ordered_flags = [f for f in FLAG_ORDER if f in flags] + [f for f in flags if f not in FLAG_ORDER]
    return {
        "status": "ok",
        "request_id": rid,
        "reference_date": rules.reference_date.isoformat(),
        "financial_tier": {"label": tier.label, "approvals": list(tier.approvals)} if tier else None,
        "required_approvals": ordered_approvals,
        "approval_reasons": {a: approvals[a] for a in ordered_approvals},
        "risk_flags": ordered_flags,
        "flag_reasons": {f: flags[f] for f in ordered_flags},
        "missing_information": missing,
        "checks": checks,
        "evidence": evidence,
        "injection_findings": injections,
        "vendor": vendor_summary,
        "default_recommendation_code": code,
        "note": "Deterministic results are authoritative minimums. Recommendation only - humans approve.",
    }
