"""Orchestration for both architectures (+ a rules-only reference baseline).

    single : single tool-calling agent ................................ -> guardrails -> decision
    staged : analyst agent -> evidence pack -> policy engine -> reviewer -> guardrails -> decision
    rules  : policy engine only (no LLM; evaluation baseline and degraded mode)

If the LLM fails at any point the run degrades to the deterministic decision,
flags `ai_analysis_unavailable`, and still requires human review.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.agents import run_analyst, run_reviewer, run_single_agent
from src.contracts import ProcurementDecision, RunTelemetry
from src.guardrails import finalize
from src.llm import GeminiClient, LLMError, model_name
from src.telemetry import RunContext
from src.tools import TOOL_FUNCTIONS

OUTAGE_URL = "http://127.0.0.1:9"   # closed port -> connection refused


@dataclass
class RunResult:
    decision: ProcurementDecision
    trace: dict = field(default_factory=dict)


def run_pipeline(request_id: str, architecture: str = "single", request_override: dict | None = None,
                 simulate_vendor_outage: bool = False) -> RunResult:
    if architecture not in {"single", "staged", "rules"}:
        raise ValueError(f"Unknown architecture: {architecture}")
    ctx = RunContext(request_override=request_override, vendor_api_base_url=OUTAGE_URL if simulate_vendor_outage else None)
    client = GeminiClient(ctx)
    llm_out: dict | None = None
    pack: dict | None = None
    degraded: str | None = None

    def tool(name: str, caller: str, **args):
        return ctx.call_tool(name, TOOL_FUNCTIONS[name], args, caller)

    if architecture == "single":
        try:
            llm_out, _ = run_single_agent(ctx, client, request_id)
        except LLMError as exc:
            degraded = str(exc)
        policy = tool("run_policy_checks", "guardrail", request_id=request_id)

    elif architecture == "staged":
        try:
            pack, _ = run_analyst(ctx, client, request_id)
        except LLMError as exc:
            degraded = str(exc)
        policy = tool("run_policy_checks", "orchestrator", request_id=request_id)
        if pack is not None:
            sections = tool("get_policy_section", "orchestrator", query="all").get("sections", [])
            try:
                llm_out = run_reviewer(ctx, client, request_id, pack, policy, sections)
            except LLMError as exc:
                degraded = str(exc)

    else:
        policy = tool("run_policy_checks", "orchestrator", request_id=request_id)

    decision, report = finalize(ctx, request_id, llm_out, policy, degraded_reason=degraded)
    tokens_in, tokens_out = ctx.token_totals()
    counted = ctx.counted_tool_calls
    decision.telemetry = RunTelemetry(
        llm_calls=len(ctx.llm_calls),
        tool_calls=len(counted),
        tool_names=[r.name for r in counted],
        architecture=architecture,
        model=model_name() if architecture != "rules" else None,
        latency_ms=ctx.elapsed_ms(),
        llm_input_tokens=tokens_in,
        llm_output_tokens=tokens_out,
        recommendation_code=report["final_code"],
        guardrail_overrides=report["overrides"],
        degraded_mode=degraded is not None,
    )
    trace = {
        "request_id": request_id,
        "architecture": architecture,
        "tool_calls": [
            {"name": r.name, "args": r.args, "caller": r.caller, "ok": r.ok, "cached": r.cached,
             "counted": r.counted, "latency_ms": r.latency_ms, "output": r.output}
            for r in ctx.tool_calls
        ],
        "llm_calls": [vars(c) for c in ctx.llm_calls],
        "policy": policy,
        "evidence_pack": pack,
        "llm_output": llm_out,
        "guardrails": report,
        "degraded_reason": degraded,
    }
    return RunResult(decision, trace)
