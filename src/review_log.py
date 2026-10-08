"""Append-only log of HUMAN decisions taken on copilot recommendations.

The copilot never approves anything; this records what a human reviewer did
with a recommendation (who, when, which architecture/version produced it).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parents[1] / "runtime" / "review_log.jsonl"

HUMAN_ACTIONS = {
    "send_for_approvals": "Send to the listed approvers",
    "return_to_requester": "Return to requester for information",
    "escalate_manual_review": "Escalate to manual review",
    "decline": "Decline the request",
}


def record(entry: dict) -> dict:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"), **entry}
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    return entry


def load() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    rows = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows
