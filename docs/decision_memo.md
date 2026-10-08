# Architecture decision memo

## Decision

Ship **Architecture A: the single tool-calling agent**, behind the deterministic policy engine and code guardrails.

## Evidence

| Metric | A: single agent | B: analyst → reviewer | Source |
|---|---:|---:|---|
| LLM calls per request | **2** | 3 | Fixed by design; telemetry |
| Latency, REQ-1008, gemini-3.1-flash-lite (2 runs each) | **4.2 s, 5.7 s** | 15.5 s, 26.8 s | spot checks, `docs/screenshots/` |
| Ideal next action on REQ-1008 ("reuse existing tool"), all 3 runs incl. 2.5-flash-lite | 2/3 | 2/3 | same |
| What the misses were | "request more info" | "request more info" | same |
| Approvals / flags / missing info | Identical: set by shared policy engine + guardrails | Identical | `tests/`, rules run |
| Ungrounded LLM evidence (dropped by code) | 0 | 0 | spot checks |
| Rules-only baseline, 16 cases | 15/16 (misses only EV-08) | | `evals/results/summary.md` |

*Evidence limits:* the free Gemini tier allows 20 requests per model per day, so the full 16-case LLM run (`python evals/run_eval.py`, about 80 calls) was not completed on this key. The table uses only measured runs; the runner reproduces the full comparison with any key.

## Trade-offs

**What B adds:** an inspectable handoff and a reviewer that never sees raw tool text.

**What B costs:**
- 50% more LLM calls, about 4× the latency, and more tokens.
- A second model's summary that can drift from the tool output.

On quality they tied: each architecture picked the ideal action 2 times out of 3. Every miss was the conservative "request more information", never an unsafe "proceed".

**Where the comparison doesn't matter:** the decisions that carry policy risk are made by code, identically in both architectures. That covers approvals, Security/Privacy/Legal triggers, budget, review expiry, conflicts and missing information. The LLM can add an escalation but cannot remove a control or cite a number absent from tool output. So the architecture choice only affects judgement quality (next action, rationale, questions), cost and latency. On those, the extra stage adds cost without a measured quality gain.

**Injection defence** doesn't need the second stage either: untrusted text is scanned in code and can't change approvals, because those are code-enforced.

## Risks / limitations

- **Small, non-deterministic sample.** Gemini 3 runs at its default temperature, so the same case can vary. Validate with the full 16-case run and `--repeats 3` before go-live.
- **The LLM's real contribution is narrow.** It decides overlap and reuse, explains, and asks questions. Rules alone get 15/16 next actions. Monitor reuse-vs-expansion mistakes (an early run misjudged a seat add-on as duplication; the prompt was tightened).
- **Regex injection scanner:** paraphrases can slip past detection, but not past the controls.
- **Quota:** production needs a paid key; degraded mode covers outages.

## Why this is the right MVP

Procurement needs correct controls, visible evidence and a human decision. Code provides the controls, and the guardrails make them architecture-independent. Given that, the second agent mostly buys latency and an extra failure point. A single agent with two LLM calls is simpler, faster and cheaper to run. Every recommendation is still code-checked and human-approved.
