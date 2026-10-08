"""Reproducible architecture comparison on one shared case set.

    python evals/run_eval.py                          # single + staged + rules baseline, all cases
    python evals/run_eval.py --architectures single staged --repeats 2
    python evals/run_eval.py --cases evals/eval_cases.json --only EV-08 EV-14

Scores each run on the brief's criteria:
  correct next action | policy followed (approvals, flags, missing info) |
  escalation correct  | evidence grounded in tool outputs | latency, LLM & tool calls
and on how much the code guardrails had to correct the model.

Writes evals/results/{eval_results.csv, decisions.jsonl, summary.md}.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.llm import model_name  # noqa: E402
from src.mock_service import ensure_running  # noqa: E402
from src.pipeline import run_pipeline  # noqa: E402

OUT = ROOT / "evals" / "results"
NOT_THRESHOLD_EXTRAS_OK = {"Manager"}   # an extra Manager sign-off is harmless; any other extra approval is over-escalation


def score(case: dict, decision, trace: dict) -> dict:
    exp = case["expected"]
    code = decision.telemetry.recommendation_code
    approvals, flags = set(decision.required_approvals), set(decision.risk_flags)
    missing_text = [m.lower() for m in decision.missing_information]
    guard = trace["guardrails"]

    missing_approvals = sorted(set(exp["approvals"]) - approvals)
    extra_approvals = sorted(approvals - set(exp["approvals"]) - NOT_THRESHOLD_EXTRAS_OK)
    missing_flags = sorted(set(exp["flags_required"]) - flags)
    forbidden_flags = sorted(set(exp["flags_forbidden"]) & flags)
    missing_groups = [g for g in exp["missing_required_groups"] if not any(tok in m for tok in g for m in missing_text)]
    too_many_missing = len(decision.missing_information) > exp["max_missing"]

    expected_escalate = all(c != "proceed_to_approval_routing" for c in exp["recommendation_codes"])
    expected_proceed = all(c == "proceed_to_approval_routing" for c in exp["recommendation_codes"])
    escalated = code != "proceed_to_approval_routing"
    escalation_ok = decision.human_review_required and (
        (expected_escalate and escalated) or (expected_proceed and not escalated) or (not expected_escalate and not expected_proceed))

    proposed = guard.get("evidence_proposed", 0)
    grounded = guard.get("evidence_grounded", 0)
    llm_used = trace["architecture"] != "rules" and not trace["degraded_reason"]
    raw_code = guard.get("llm_raw_code")

    row = {
        "correct_next_action": code in exp["recommendation_codes"],
        "approvals_correct": not missing_approvals and not extra_approvals,
        "flags_correct": not missing_flags and not forbidden_flags,
        "missing_info_correct": not missing_groups and not too_many_missing,
        "human_escalation_correct": escalation_ok,
        "evidence_items": len(decision.evidence),
        "llm_evidence_proposed": proposed,
        "llm_evidence_grounded": grounded,
        "grounded_evidence": (grounded == proposed) if llm_used else True,
        # what the model got right *before* code guardrails touched it
        "llm_raw_correct_next_action": (raw_code in exp["recommendation_codes"]) if llm_used else None,
        "llm_raw_policy_agreement": (not guard.get("approvals_added_by_code") and not guard.get("flags_added_by_code")) if llm_used else None,
        "guardrail_overrides": len(guard.get("overrides", [])),
        "guardrail_corrections": len(guard.get("approvals_added_by_code", [])) + len(guard.get("flags_added_by_code", []))
                                 + len(guard.get("llm_rejected", [])) + len(guard.get("overrides", [])) if llm_used else 0,
        "notes": "; ".join(filter(None, [
            f"code={code}" + (f" (llm said {raw_code})" if raw_code and raw_code != code else ""),
            f"missing approvals {missing_approvals}" if missing_approvals else "",
            f"extra approvals {extra_approvals}" if extra_approvals else "",
            f"missing flags {missing_flags}" if missing_flags else "",
            f"forbidden flags {forbidden_flags}" if forbidden_flags else "",
            f"missing-info groups absent {missing_groups}" if missing_groups else "",
            "too many missing-info items" if too_many_missing else "",
            f"{proposed - grounded} ungrounded LLM evidence dropped" if proposed - grounded else "",
            f"DEGRADED: {trace['degraded_reason']}" if trace["degraded_reason"] else "",
        ])),
    }
    row["policy_followed"] = row["approvals_correct"] and row["flags_correct"] and row["missing_info_correct"]
    row["case_pass"] = row["correct_next_action"] and row["policy_followed"] and row["human_escalation_correct"] and row["grounded_evidence"]
    return row


def main() -> None:
    import logging
    logging.basicConfig(level=logging.WARNING, format="      %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--architectures", nargs="+", default=["single", "staged", "rules"], choices=["single", "staged", "rules"])
    parser.add_argument("--cases", default=str(ROOT / "evals" / "eval_cases.json"))
    parser.add_argument("--only", nargs="*", help="case_ids to run")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--pause", type=float, default=0.0, help="seconds to sleep between LLM runs (rate limits)")
    parser.add_argument("--resume", action="store_true", help="keep completed non-degraded runs from a previous invocation")
    parser.add_argument("--allow-degraded", action="store_true",
                        help="record runs where the LLM was unavailable instead of stopping (default: stop and save)")
    args = parser.parse_args()

    if not ensure_running():
        sys.exit("Could not start the mock vendor-risk API on VENDOR_RISK_BASE_URL")
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    if args.only:
        cases = [c for c in cases if c["case_id"] in set(args.only)]
    OUT.mkdir(parents=True, exist_ok=True)

    rows, records = [], []
    done: set[tuple] = set()
    if args.resume and (OUT / "eval_results.csv").exists():
        with (OUT / "eval_results.csv").open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("degraded") == "False" and not r.get("notes", "").startswith("CRASH"):
                    rows.append(_typed(r))
                    done.add((r["case_id"], r["architecture"], int(r["repeat"])))
        if (OUT / "decisions.jsonl").exists():
            for line in (OUT / "decisions.jsonl").read_text(encoding="utf-8").splitlines():
                rec = json.loads(line)
                if (rec["case_id"], rec["architecture"], rec["repeat"]) in done:
                    records.append(rec)
        print(f"Resuming: {len(done)} completed runs kept")
    stop = False
    per_run = {"single": 2, "staged": 3, "rules": 0}
    todo = sum(per_run[a] for _ in range(args.repeats) for c in cases for a in args.architectures
               if (c["case_id"], a, _) not in done)
    print(f"Estimated LLM calls: ~{todo} (single=2, staged=3 per case; Gemini free tier allows ~20/day per model)")
    print(f"Evaluating {len(cases)} cases x {args.architectures} x {args.repeats} repeat(s) - model {model_name()}\n")
    for rep in range(args.repeats):
        for case in cases:
            for arch in args.architectures:
                if stop or (case["case_id"], arch, rep) in done:
                    continue
                start = time.perf_counter()
                try:
                    result = run_pipeline(case["request_id"], arch, request_override=case.get("request"),
                                          simulate_vendor_outage=case.get("simulate_vendor_outage", False))
                except Exception as exc:  # a crash is a failed case, not a crashed eval
                    rows.append({"case_id": case["case_id"], "architecture": arch, "repeat": rep, "case_pass": False,
                                 "notes": f"CRASH {type(exc).__name__}: {exc}"})
                    print(f"CRASH {case['case_id']} {arch}: {exc}")
                    continue
                wall = (time.perf_counter() - start) * 1000
                d, t = result.decision, result.decision.telemetry
                if t.degraded_mode and not args.allow_degraded:
                    print(f"STOP  {case['case_id']} {arch}: LLM unavailable ({result.trace['degraded_reason']}). "
                          "Saved completed runs; re-run later with --resume.")
                    stop = True
                    continue
                s = score(case, d, result.trace)
                row = {"case_id": case["case_id"], "request_id": case["request_id"], "edge_case": case["edge_case"],
                       "architecture": arch, "repeat": rep, "recommendation_code": t.recommendation_code,
                       **{k: s[k] for k in ("case_pass", "correct_next_action", "policy_followed", "human_escalation_correct",
                                            "grounded_evidence", "approvals_correct", "flags_correct", "missing_info_correct")},
                       "latency_ms": round(wall, 1), "llm_calls": t.llm_calls, "tool_calls": t.tool_calls,
                       "input_tokens": t.llm_input_tokens, "output_tokens": t.llm_output_tokens,
                       "degraded": t.degraded_mode, "model": t.model or "",
                       **{k: s[k] for k in ("llm_raw_correct_next_action", "llm_raw_policy_agreement", "llm_evidence_proposed",
                                            "llm_evidence_grounded", "guardrail_overrides", "guardrail_corrections", "evidence_items", "notes")}}
                rows.append(row)
                records.append({"case_id": case["case_id"], "architecture": arch, "repeat": rep,
                                "decision": d.model_dump(), "guardrails": result.trace["guardrails"],
                                "evidence_pack": result.trace["evidence_pack"], "llm_calls": result.trace["llm_calls"]})
                print(f"{'PASS' if s['case_pass'] else 'FAIL'}  {case['case_id']:<6} {arch:<7} {t.recommendation_code:<29} "
                      f"{wall:>7.0f} ms  llm={t.llm_calls} tools={t.tool_calls}  {s['notes'] if not s['case_pass'] else ''}")
                if arch != "rules" and args.pause:
                    time.sleep(args.pause)

    fields = list(dict.fromkeys(k for r in rows for k in r))
    with (OUT / "eval_results.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with (OUT / "decisions.jsonl").open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, default=str) + "\n")
    summary = summarize(rows, args.architectures, len(cases), args.repeats)
    (OUT / "summary.md").write_text(summary, encoding="utf-8")
    print("\n" + summary)
    print(f"Wrote {OUT.relative_to(ROOT)}/eval_results.csv, decisions.jsonl, summary.md")


def _typed(r: dict) -> dict:
    """Restore types for rows read back from CSV (resume)."""
    out = {}
    for k, v in r.items():
        if v in ("True", "False"):
            out[k] = v == "True"
        elif v == "":
            out[k] = None
        else:
            try:
                out[k] = float(v) if "." in v else int(v)
            except ValueError:
                out[k] = v
    out["case_id"], out["architecture"] = r["case_id"], r["architecture"]
    return out


def _rate(rows, key) -> str:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return f"{sum(bool(v) for v in vals)}/{len(vals)}" if vals else "n/a"


def _avg(rows, key, digits=1) -> str:
    vals = [r[key] for r in rows if isinstance(r.get(key), (int, float)) and not isinstance(r.get(key), bool)]
    return f"{statistics.mean(vals):.{digits}f}" if vals else "n/a"


def _p95(rows, key) -> str:
    vals = sorted(r[key] for r in rows if isinstance(r.get(key), (int, float)))
    return f"{vals[min(len(vals) - 1, int(round(0.95 * (len(vals) - 1))))]:.0f}" if vals else "n/a"


def summarize(rows: list[dict], archs: list[str], n_cases: int, repeats: int) -> str:
    by = defaultdict(list)
    for r in rows:
        by[r["architecture"]].append(r)
    label = {"single": "A: single agent", "staged": "B: staged (analyst -> reviewer)", "rules": "Rules only (no LLM, reference)"}
    metrics = [
        ("Cases passing all criteria", lambda rs: _rate(rs, "case_pass")),
        ("Correct next action", lambda rs: _rate(rs, "correct_next_action")),
        ("Policy followed (approvals + flags + missing info)", lambda rs: _rate(rs, "policy_followed")),
        ("Escalation / human review correct", lambda rs: _rate(rs, "human_escalation_correct")),
        ("All LLM evidence grounded in tool output", lambda rs: _rate(rs, "grounded_evidence")),
        ("LLM evidence items grounded / proposed", lambda rs: f"{sum(r.get('llm_evidence_grounded') or 0 for r in rs)}/{sum(r.get('llm_evidence_proposed') or 0 for r in rs)}"),
        ("LLM next action correct *before* guardrails", lambda rs: _rate(rs, "llm_raw_correct_next_action")),
        ("LLM approvals+flags complete *before* guardrails", lambda rs: _rate(rs, "llm_raw_policy_agreement")),
        ("Avg guardrail corrections per case", lambda rs: _avg(rs, "guardrail_corrections", 2)),
        ("Avg latency (ms)", lambda rs: _avg(rs, "latency_ms", 0)),
        ("p95 latency (ms)", lambda rs: _p95(rs, "latency_ms")),
        ("Avg LLM calls", lambda rs: _avg(rs, "llm_calls")),
        ("Avg tool calls", lambda rs: _avg(rs, "tool_calls")),
        ("Avg tokens (in / out)", lambda rs: f"{_avg(rs, 'input_tokens', 0)} / {_avg(rs, 'output_tokens', 0)}"),
        ("Degraded runs (LLM unavailable)", lambda rs: str(sum(bool(r.get("degraded")) for r in rs))),
    ]
    present = [a for a in archs if by[a]]
    lines = [f"# Evaluation summary\n", f"Model: `{model_name()}` | cases: {n_cases} | repeats: {repeats} | "
             f"generated {time.strftime('%Y-%m-%d %H:%M')}\n",
             "| Metric | " + " | ".join(label[a] for a in present) + " |",
             "|---|" + "---:|" * len(present)]
    for name, fn in metrics:
        lines.append(f"| {name} | " + " | ".join(fn(by[a]) for a in present) + " |")

    lines += ["\n## By edge case (cases passing)\n", "| Edge case | " + " | ".join(present) + " |", "|---|" + "---:|" * len(present)]
    edges = list(dict.fromkeys(r.get("edge_case") for r in rows if r.get("edge_case")))
    for e in edges:
        lines.append(f"| {e} | " + " | ".join(_rate([r for r in by[a] if r.get("edge_case") == e], "case_pass") for a in present) + " |")

    fails = [r for r in rows if not r.get("case_pass")]
    lines.append("\n## Failures\n")
    lines += [f"- **{r['case_id']} / {r['architecture']}** (repeat {r.get('repeat', 0)}): {r.get('notes', '')}" for r in fails] or ["- none"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
