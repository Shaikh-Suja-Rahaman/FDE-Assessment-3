"""Deterministic scanner for instructions embedded in untrusted business data.

Request text, vendor notes and API text are data, never instructions (policy §9).
The scanner does not decide anything on its own: it surfaces findings so the
policy engine can flag them and the LLM prompt can treat the text as hostile.
"""
from __future__ import annotations

import re
from typing import Any

# (label, pattern) - patterns are deliberately phrased around *intent*
# (override rules, fabricate approval, bypass review, exfiltrate secrets).
INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("override_instructions", re.compile(
        r"\b(ignore|disregard|forget|override)\b[^.?!]{0,40}\b(instructions?|rules?|polic(y|ies)|controls?|guidelines?|procedures?)\b", re.I)),
    ("fabricated_approval", re.compile(
        r"\b(treat|consider|mark|record|log)\b[^.?!]{0,40}\bas\b[^.?!]{0,20}\b(pre-?approved|approved|signed[- ]off|cleared|complete[d]?)\b", re.I)),
    ("fabricated_approval", re.compile(r"\b(cfo|ceo|vp|director|finance|legal|security)[- ](approved|signed[- ]off|cleared)\b", re.I)),
    ("auto_approve", re.compile(r"\b(auto-?approve|approve (it|this|the request|immediately|now|right away))\b", re.I)),
    ("bypass_controls", re.compile(
        r"\b(bypass|skip|waive|circumvent)\b[^.?!]{0,30}\b(controls?|reviews?|polic(y|ies)|approvals?|security|privacy|legal|procurement|finance)\b", re.I)),
    ("role_hijack", re.compile(r"(\bsystem prompt\b|\byou are now\b|\bnew instructions\b|\bdeveloper (message|mode)\b|\badmin mode\b|^\s*system\s*:)", re.I | re.M)),
    ("secret_exfiltration", re.compile(
        r"\b(reveal|print|show|expose|send|leak)\b[^.?!]{0,30}\b(api[ _-]?keys?|secrets?|passwords?|credentials?|tokens?|system prompt)\b", re.I)),
]

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def scan_text(field: str, text: Any) -> list[dict]:
    """Return one finding per (field, label) with a short snippet of the offending text."""
    if not isinstance(text, str) or not text.strip():
        return []
    findings: list[dict] = []
    seen: set[str] = set()
    for label, pattern in INJECTION_PATTERNS:
        match = pattern.search(text)
        if match and label not in seen:
            seen.add(label)
            start, end = max(0, match.start() - 20), min(len(text), match.end() + 20)
            snippet = text[start:end].strip()
            findings.append({"field": field, "type": label, "snippet": snippet[:140]})
    return findings


def scan_record(prefix: str, record: dict | None) -> list[dict]:
    """Scan every free-text value of a record (nested lists included)."""
    findings: list[dict] = []
    if not isinstance(record, dict):
        return findings
    for key, value in record.items():
        if isinstance(value, str):
            findings.extend(scan_text(f"{prefix}.{key}", value))
        elif isinstance(value, list):
            for i, item in enumerate(value):
                findings.extend(scan_text(f"{prefix}.{key}[{i}]", item))
    return findings


def strip_injected_sentences(text: str | None) -> str:
    """Remove sentences that contain injected instructions; used to judge what real content remains."""
    if not text:
        return ""
    kept = [s for s in _SENTENCE_SPLIT.split(text) if s.strip() and not scan_text("_", s)]
    return " ".join(kept).strip()
