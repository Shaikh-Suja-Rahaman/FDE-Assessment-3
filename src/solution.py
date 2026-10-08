"""Assessment adapter used by the evaluation harness and the UI.

The implementation lives in:
  src/tools.py          - evidence tools (deterministic) + Gemini function declarations
  src/policy_engine.py  - deterministic policy rules (thresholds, budget, vendor, triggers)
  src/agents.py         - Architecture A (single agent) and B (analyst -> reviewer)
  src/guardrails.py     - code-enforced controls applied to every LLM output
  src/pipeline.py       - orchestration, degraded mode and telemetry
"""
from __future__ import annotations

from src.contracts import Architecture, ProcurementDecision
from src.pipeline import run_pipeline


def handle_request(request_id: str, architecture: Architecture = "single") -> ProcurementDecision:
    """Assessment adapter."""
    if architecture not in ("single", "staged"):
        raise ValueError(f"Unknown architecture: {architecture}")
    return run_pipeline(request_id, architecture).decision
