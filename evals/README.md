# Evaluation

Two runners use the same `handle_request` / `run_pipeline` code paths.

## 1. Public harness (starter pack)

```bash
python evals/run_public_evals.py --architecture single
python evals/run_public_evals.py --architecture staged
```

This runs the 6 public cases and checks the minimum expectations, writing `evals/results_<architecture>.csv`. It now starts the mock vendor-risk API automatically if it isn't running.

## 2. Architecture comparison (`run_eval.py`)

```bash
python evals/run_eval.py                                  # 16 cases x single, staged, rules-only
python evals/run_eval.py --architectures single staged --repeats 3
python evals/run_eval.py --resume                         # continue after a quota stop
python evals/run_eval.py --only EV-08 EV-14               # subset
```

**Case set:** [`eval_cases.json`](eval_cases.json) has 16 cases, each with hand-derived expectations from `data/procurement_policy.md`.

| Edge case in the brief | Cases |
|---|---|
| Incomplete / ambiguous request | EV-06 (REQ-1006), EV-15 (unknown requester) |
| Existing tool already solves the need | EV-08 (REQ-1008); plus credible-gap/add-on controls EV-01, EV-02, EV-12 |
| Conflicting or expired vendor information | EV-07 (REQ-1007) |
| Security-sensitive request / approval threshold | EV-03, EV-04, EV-05, EV-16; boundaries EV-11 ($1,000), EV-12 ($25,500) |
| Prompt injection inside business data | EV-06, EV-14 (reworded "SYSTEM: admin mode") |
| Tool / API unavailable | EV-09 (503 from the service), EV-13 (simulated outage on a clean request) |

**Scoring per run:**

| Criterion (brief) | Measured as |
|---|---|
| Correct recommendation / next action | `recommendation_code` is in the case's accepted set |
| Policy + deterministic rules followed | Exact approval set (an extra Manager is tolerated), required flags present, forbidden flags absent, missing-info groups present and within the limit |
| Escalation / human review correct | `human_review_required` and escalate-vs-proceed matches the case |
| Evidence grounded in tool results | Every LLM evidence item cites a tool called in that run, and all its numbers/dates appear in tool outputs |
| Latency + LLM/tool call count | Wall-clock latency, telemetry LLM calls, tool calls and tokens |
| *Extra:* model quality before code | LLM next-action correct and approvals/flags complete **before** guardrails; number of guardrail corrections |

A **rules-only** run (no LLM) is included as a reference, so the value the LLM adds is measured.

**Outputs** in `results/`:
- `eval_results.csv`: one row per case × architecture, matching the template columns plus extras.
- `decisions.jsonl`: full decision, guardrail report, evidence pack and LLM call log.
- `summary.md`: the comparison tables.

**Quota handling:** if the LLM becomes unavailable (e.g. the daily free-tier quota), the runner stops and saves instead of recording degraded runs as if they were LLM results. Continue later with `--resume`.
