"""Audit saved benchmark results against evidence and bounded gold after inference.

Mechanical support is necessary, not sufficient for correctness. Semantic claims,
conditions, source drift and ambiguity stay explicitly reviewable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path

from evals.evidence import answer_urls, assembly_occurrences, evidence_gate, mentions, source_urls
from evals.io import read_jsonl


# Mechanical scoring keeps source support, reference recovery, and semantic review separate.
def assess_case(case: dict, gold: dict) -> dict:
    """Check reference recovery and cited assembly support while preserving all claims for semantic review."""
    answer = case.get("response_text") or ""
    evidence = case.get("evidence", [])
    gate = evidence_gate(answer, evidence)
    parts = gold["answer"]["parts"]
    mentioned = [p for p in parts if mentions(answer, p["part_number"])]
    occurrences = assembly_occurrences(evidence)
    backed = sorted(
        {
            p["part_number"]
            for p in mentioned
            if any(
                row.get("part_number") == p["part_number"]
                and row.get("assembly_url") in p["assembly_urls"]
                and row.get("assembly_url") in gate["citations"]
                for row in occurrences
            )
        }
    )
    proposed = set(gate["proposed_part_numbers"])
    novel = sorted(proposed - {p["part_number"] for p in parts})
    if case.get("status") != "received" or case.get("response_status") not in {None, "completed"}:
        outcome = "execution_failure"
    elif not gate["passed"]:
        outcome = "evidence_gate_failed"
    elif novel:
        outcome = "source_drift_or_extra_claim_review"
    elif backed:
        outcome = "supported_candidate_semantics_pending"
    elif mentioned:
        outcome = "wrong_or_unverified_assembly_context"
    else:
        outcome = "incomplete_or_clarification_review"
    return {
        "id": case["id"],
        "outcome": outcome,
        "evidence_gate": gate,
        "mentioned_reference_numbers": sorted(p["part_number"] for p in mentioned),
        "reference_numbers_with_cited_gold_assembly": backed,
        "extra_numbers_for_review": novel,
        "answer_conditions_for_review": gold["answer"].get("conditions", []),
        "manual_semantic_review_required": True,
        "accuracy_pass": None,
    }


# A score is valid only for a complete schedule with a matching answer key.
def audit(report: Path, answers: Path) -> dict:
    """Validate a complete recorded schedule and score its cases against the frozen post-inference answer key."""
    raw = report.read_bytes()
    records = [json.loads(line) for line in raw.decode("utf-8-sig").splitlines() if line.strip()]
    gold, gold_hash = read_jsonl(answers)
    by_id = {row["id"]: row for row in gold}
    cases = [row for row in records if row.get("record_type") == "case"]
    runs = [row for row in records if row.get("record_type") == "run"]
    if len(runs) != 1 or not cases:
        raise ValueError("Expected one run and nonempty case records")
    scheduled = runs[0]["scheduled_cases"]
    if [row["id"] for row in cases] != [row["id"] for row in scheduled]:
        raise ValueError("Cases must match the complete schedule exactly")
    if any(row["id"] not in by_id for row in cases):
        raise ValueError("Gold is missing a scheduled case")
    rows = [assess_case(case, by_id[case["id"]]) for case in cases]
    latencies = sorted(case["latency_seconds"] for case in cases if case.get("latency_seconds") is not None)
    cache = [event for case in cases for event in case.get("diagnostics", {}).get("cache", [])]
    usage = [event for case in cases for event in case.get("diagnostics", {}).get("model_usage", [])]
    unsupported_handoff_urls = []
    for case in cases:
        observed = source_urls(case.get("evidence", []))
        for handoff in case.get("diagnostics", {}).get("handoffs", []):
            unsupported = answer_urls(json.dumps(handoff["handoff"]), observed) - observed
            unsupported_handoff_urls.extend(
                {"case_id": case["id"], "url": url} for url in sorted(unsupported)
            )
    return {
        "schema_version": 1,
        "report_sha256": hashlib.sha256(raw).hexdigest(),
        "answers_sha256": gold_hash,
        "scorer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "gate_sha256": hashlib.sha256((Path(__file__).parents[1] / "evidence.py").read_bytes()).hexdigest(),
        "scheduled": len(cases),
        "outcomes": dict(Counter(row["outcome"] for row in rows)),
        "gate_violations": dict(
            Counter(v["code"] for row in rows for v in row["evidence_gate"]["violations"])
        ),
        "candidate_recovery_cases": sum(bool(row["mentioned_reference_numbers"]) for row in rows),
        "context_backed_cases": sum(bool(row["reference_numbers_with_cited_gold_assembly"]) for row in rows),
        "latency_median_seconds": statistics.median(latencies) if latencies else None,
        "cache_hit_rate": sum(event in {"hit", "coalesced"} for event in cache) / len(cache)
        if cache
        else None,
        "source_requests": sum(len(case.get("diagnostics", {}).get("requests", [])) for case in cases),
        "tokens": {
            name: sum(event.get(name, 0) for event in usage)
            for name in ("input_tokens", "output_tokens", "total_tokens")
        },
        "unsupported_handoff_urls": unsupported_handoff_urls,
        "limits": "No automatic correctness verdict. All proposed claims and applicability need semantic review; "
        "a bounded key is not a whitelist. Errors and clarifications remain in the denominator.",
        "cases": rows,
    }


# Command-line entry point: validate options before starting operational work.
def main() -> None:
    """Parse command-line options and execute score, reporting failures to the caller."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.report, args.answers)
    with args.output.open("x", encoding="utf-8") as output:
        output.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "cases"}))


if __name__ == "__main__":
    main()
