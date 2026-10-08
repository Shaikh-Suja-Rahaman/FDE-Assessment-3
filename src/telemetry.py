"""Per-run context: tool cache, call trace and telemetry.

Every tool invocation and LLM call goes through a RunContext so that the
reported `llm_calls` / `tool_calls` are measured, not estimated.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCallRecord:
    name: str
    args: dict
    caller: str          # "agent:<name>" | "orchestrator" | "policy_engine"
    ok: bool
    cached: bool
    latency_ms: float
    output: Any

    @property
    def counted(self) -> bool:
        # Agent-requested calls always count; code-initiated calls count only
        # when they actually executed (not when served from the run cache).
        return self.caller.startswith("agent") or not self.cached


@dataclass
class LLMCallRecord:
    stage: str
    model: str
    ok: bool
    latency_ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    retries: int = 0
    error: str | None = None


@dataclass
class RunContext:
    """State for a single request evaluation."""
    request_override: dict | None = None        # ad-hoc request submitted from the UI / synthetic eval case
    vendor_api_base_url: str | None = None      # override to simulate an outage
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    llm_calls: list[LLMCallRecord] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    _cache: dict[str, Any] = field(default_factory=dict)
    started: float = field(default_factory=time.perf_counter)

    # ---- tools -------------------------------------------------------
    def call_tool(self, name: str, fn, args: dict, caller: str) -> Any:
        key = name + json.dumps(args, sort_keys=True, default=str)
        if key in self._cache:
            output = self._cache[key]
            self.tool_calls.append(ToolCallRecord(name, args, caller, _ok(output), True, 0.0, output))
            return output
        start = time.perf_counter()
        try:
            output = fn(self, **args)
        except Exception as exc:  # tools must never crash the run
            output = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        latency = (time.perf_counter() - start) * 1000
        self._cache[key] = output
        self.tool_calls.append(ToolCallRecord(name, args, caller, _ok(output), False, round(latency, 1), output))
        return output

    def tool_outputs(self) -> list[Any]:
        return [r.output for r in self.tool_calls]

    def tools_called(self) -> set[str]:
        return {r.name for r in self.tool_calls}

    # ---- telemetry ---------------------------------------------------
    @property
    def counted_tool_calls(self) -> list[ToolCallRecord]:
        return [r for r in self.tool_calls if r.counted]

    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self.started) * 1000, 1)

    def token_totals(self) -> tuple[int, int]:
        return (sum(c.input_tokens for c in self.llm_calls), sum(c.output_tokens for c in self.llm_calls))


def _ok(output: Any) -> bool:
    return not (isinstance(output, dict) and output.get("status") in {"error", "unavailable", "not_found"})


# Backwards-compatible helper from the starter pack.
@dataclass
class RunTelemetryCounter:
    llm_calls: int = 0
    tool_calls: int = 0
    tool_names: list[str] = field(default_factory=list)

    def record_llm_call(self) -> None:
        self.llm_calls += 1

    def record_tool_call(self, name: str) -> None:
        self.tool_calls += 1
        self.tool_names.append(name)
