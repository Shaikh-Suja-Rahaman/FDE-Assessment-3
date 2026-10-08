# Evaluation summary

Model: `gemini-3.1-flash-lite` | cases: 16 | repeats: 1 | generated 2026-10-07 16:35

| Metric | Rules only (no LLM, reference) |
|---|---:|
| Cases passing all criteria | 15/16 |
| Correct next action | 15/16 |
| Policy followed (approvals + flags + missing info) | 16/16 |
| Escalation / human review correct | 15/16 |
| All LLM evidence grounded in tool output | 16/16 |
| LLM evidence items grounded / proposed | 0/0 |
| LLM next action correct *before* guardrails | n/a |
| LLM approvals+flags complete *before* guardrails | n/a |
| Avg guardrail corrections per case | 0.00 |
| Avg latency (ms) | 5 |
| p95 latency (ms) | 7 |
| Avg LLM calls | 0.0 |
| Avg tool calls | 6.9 |
| Avg tokens (in / out) | 0 / 0 |
| Degraded runs (LLM unavailable) | 0 |

## By edge case (cases passing)

| Edge case | rules |
|---|---:|
| baseline | 2/2 |
| existing tool + new vendor | 1/1 |
| security-sensitive | 2/2 |
| approval threshold + budget | 1/1 |
| incomplete + prompt injection | 1/1 |
| conflicting / expired vendor info | 1/1 |
| existing tool solves the need | 0/1 |
| tool / API unavailable | 2/2 |
| approval threshold boundary | 2/2 |
| prompt injection in business data | 1/1 |
| incomplete / ambiguous request | 1/1 |
| security-sensitive + unknown vendor | 1/1 |

## Failures

- **EV-08 / rules** (repeat 0): code=proceed_to_approval_routing
